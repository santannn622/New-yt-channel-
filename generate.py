#!/usr/bin/env python3
"""Stickman finance Shorts bot.

topic -> script (Gemini) -> stickman images (Pollinations) -> voice (edge-tts)
      -> vertical video with zoom + captions (FFmpeg) -> Telegram / Cloudinary / Buffer (Multi-Platform)
"""
import asyncio
import base64
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import cloudinary
import cloudinary.uploader
import requests

# ---------- settings (override with env vars) ----------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
# Google retires models often (2.5 was shut down Oct 2026). If one 404s,
# the bot just tries the next. Update this list when needed.
GEMINI_FALLBACKS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
VOICE = os.environ.get("VOICE", "en-US-AndrewNeural")
VOICE_RATE = os.environ.get("VOICE_RATE", "+5%")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
POLL_KEY = os.environ.get("POLLINATIONS_API_KEY", "")
CF_VOICE = os.environ.get("CF_VOICE", "arcas")  # works for Aura 1 and 2
CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "")

# Buffer integration settings
BUFFER_ACCESS_TOKEN = os.environ.get("BUFFER_ACCESS_TOKEN", "")

# Cloudinary credentials (for automatic video URL hosting)
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
    )

W, H, FPS = 1080, 1920, 30
PAD = 0.3  # seconds of silence after each scene
OUT = Path("output")
WORK = OUT / "work"

# This prefix is what makes every image look like the same channel.
# "Stick" is the recurring mascot, so viewers recognise the channel instantly.
STYLE = (
    "Hand-drawn doodle illustration, one consistent style: a simple stick figure "
    "character named Stick with a big round white head, two black dot eyes, a small "
    "smile, thin black limbs and a small green necktie. Thick black marker outlines, "
    "flat pastel colors with green and gold accents for money, plain warm off-white "
    "background, lots of empty space, centered composition, no text, no letters, "
    "no numbers, no watermark. Scene: "
)

# One emotional angle per video (viewers watch for a feeling, not just facts).
ANGLES = [
    ("fear of staying broke", "Open with a stakes-raising hook that makes the viewer worry about their own money, then give relief with one simple fix."),
    ("curiosity", "Open with a surprising fact or open loop the viewer must see resolved."),
    ("anger at being played", "Open by exposing how money systems quietly take a cut, then show how to fight back. Stay strictly factual, no conspiracy claims."),
    ("hope and freedom", "Open with an inspiring 'what if' about financial freedom, then give the realistic first step."),
    ("relief", "Open by naming a money mistake everyone feels guilty about, then reassure them it is fixable."),
]

DRAFT_PROMPT = """You write viral YouTube Shorts scripts for a faceless channel that explains
personal finance with stick-figure cartoons. The recurring mascot is "Stick".

Topic: {topic}
Emotional angle: {angle} - {angle_note}

Rules:
- Goldilocks length: 90-110 words total narration (about 40 seconds). Not rushed, not padded.
- Goldilocks depth: ONE idea only, explained so a smart 16-year-old gets it instantly,
  but with one concrete example using simple numbers (e.g. "$100 a month at 7% a year").
  Only use numbers you are sure are correct.
- Scene 1 is the hook: max 12 words, makes the viewer need the next sentence.
- Sound like a friend talking: contractions, short sentences, no jargon, no filler.
- Educational only. No investment advice, no "buy X", no guaranteed returns.
- Exactly 10 scenes, about 9-11 words each. One beat per scene.
- Final scene: {ending}
- For each scene write image_prompt: what Stick is doing, with props (coins, bank, piggy
  bank, chart, house, credit card, clock). A different visual metaphor every scene,
  mix close-ups and wide shots. Max 30 words. Never mention text or style.

Return ONLY JSON in exactly this shape:
{{
  "title": "curiosity-driven title under 60 chars",
  "description": "1-2 sentence description ending with 3 hashtags including #shorts",
  "scenes": [{{"narration": "...", "image_prompt": "..."}}]
}}"""

POLISH_PROMPT = """You are a ruthless editor of viral YouTube Shorts scripts. Improve this draft.
1. Sharpen the hook: first sentence max 12 words, impossible to scroll past.
2. Cut filler. Every line must earn its place. Keep it conversational.
3. Check every number and calculation. If unsure, replace with a clearly-labelled simple example.
4. Keep exactly 10 scenes, 90-110 words total, same JSON shape, Stick as the mascot in image prompts.
5. Keep the final scene's ending (teaser or question) intact.
Return ONLY the improved JSON.

Draft:
{draft}"""

def log(*a):
    print(*a, flush=True)


# ---------- 1. topic ----------
def pick_topic():
    forced = os.environ.get("TOPIC", "").strip()
    lines = [
        l.strip() for l in Path("topics.txt").read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    if forced:
        return forced, False, (lines[0] if lines else "")
    if not lines:
        sys.exit("topics.txt is empty - add more topics.")
    return lines[0], True, (lines[1] if len(lines) > 1 else "")


def consume_topic(topic):
    lines = Path("topics.txt").read_text().splitlines()
    left = [l for l in lines if l.strip() != topic]
    Path("topics.txt").write_text("\n".join(left) + "\n")
    with open("done.txt", "a") as f:
        f.write(topic + "\n")


# ---------- 2. script ----------
def call_gemini(prompt, temperature=0.9):
    if not GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set.")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
        },
    }
    models = []
    for m in [GEMINI_MODEL] + GEMINI_FALLBACKS:
        if m not in models:
            models.append(m)
    last = None
    for model in models:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        for attempt in range(2):
            try:
                r = requests.post(url, json=body, timeout=90)
                if r.status_code == 404:
                    last = f"{model}: 404 model not found"
                    log(last, "- trying next model")
                    break
                if r.status_code in (429, 500, 503):
                    raise RuntimeError(f"Gemini {r.status_code}: {r.text[:150]}")
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                data = json.loads(text)
                assert data["scenes"], "no scenes"
                for sc in data["scenes"]:
                    assert sc["narration"] and sc["image_prompt"]
                log("script model:", model)
                return data
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"{model} attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Gemini failed: {last}")


def word_count(d):
    return sum(len(sc["narration"].split()) for sc in d["scenes"])


def fetch_script(topic, next_topic=""):
    angle, note = random.choice(ANGLES)
    if next_topic:
        ending = (
            "a one-line teaser for the next video (topic: "
            f"{next_topic}) that makes them follow so they don't miss it."
        )
    else:
        ending = "a short takeaway, then ask viewers one question they will want to answer in the comments."
    prompt = DRAFT_PROMPT.format(topic=topic, angle=angle, angle_note=note, ending=ending)
    try:
        draft = call_gemini(prompt)
    except RuntimeError as e:
        sys.exit(f"Could not get a script: {e}")
    log(f"angle: {angle} | draft words: {word_count(draft)}")
    # Second pass: an editor sharpens hook, trims filler, checks the numbers.
    try:
        better = call_gemini(POLISH_PROMPT.format(draft=json.dumps(draft)), 0.6)
        wc = word_count(better)
        if len(better["scenes"]) >= 8 and 70 <= wc <= 130:
            log(f"using polished script ({wc} words)")
            draft = better
        else:
            log(f"polish rejected (scenes={len(better['scenes'])}, words={wc}); keeping draft")
    except Exception as e:  # noqa: BLE001
        log("polish step skipped:", e)
    if CHANNEL_NAME:
        draft["description"] = (
            draft.get("description", "")
            + f"\n\nNew money lesson every day on {CHANNEL_NAME}."
        )
    return draft


# ---------- 3. images ----------
def fallback_card(text, path):
    """If image generation fails, still produce a clean card so the run completes."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (576, 1024), (250, 246, 238))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44
        )
    except OSError:
        font = ImageFont.load_default()
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) > 18:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    y = 400
    for ln in lines[:6]:
        d.text((40, y), ln, fill=(30, 30, 30), font=font)
        y += 60
    img.save(path)


class ProviderUnavailable(Exception):
    """Out of credit / bad key: stop retrying this provider."""


def fit_to_portrait(raw, path):
    """Flux via Cloudflare returns a square. Put it on a 9:16 canvas whose
    colour matches the image's own background so there is no visible seam."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    if h / w >= 1.6:
        im.save(path)
        return
    strip = im.crop((0, 0, w, max(4, h // 50))).resize((1, 1), Image.BILINEAR)
    bg = strip.getpixel((0, 0))
    canvas = Image.new("RGB", (W, H), bg)
    sq = im.resize((W, int(W * h / w)))
    canvas.paste(sq, (0, int((H - sq.height) * 0.25)))
    canvas.save(path)


def cloudflare_image(prompt, seed):
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
        "/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"prompt": prompt, "steps": 8},
        timeout=120,
    )
    if r.status_code in (401, 402, 403):
        raise ProviderUnavailable(f"cloudflare {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    return base64.b64decode(r.json()["result"]["image"])


def pollinations_image(prompt, seed):
    q = urllib.parse.quote(prompt)
    if POLL_KEY:
        url = f"https://gen.pollinations.ai/image/{q}?model=flux&width=576&height=1024&seed={seed}"
        headers = {"Authorization": f"Bearer {POLL_KEY}"}
    else:
        url = f"https://image.pollinations.ai/prompt/{q}?width=576&height=1024&nologo=true&model=flux&seed={seed}"
        headers = {}
    r = requests.get(url, headers=headers, timeout=120)
    if r.status_code in (401, 402, 403):
        raise ProviderUnavailable(f"pollinations {r.status_code}")
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(f"pollinations status {r.status_code}")
    return r.content


def make_image(prompt, path, seed):
    full = STYLE + prompt
    providers = []
    if CF_ACCOUNT and CF_TOKEN:
        providers.append(("cloudflare", cloudflare_image))
    providers.append(("pollinations", pollinations_image))
    for name, fn in providers:
        for attempt in range(3):
            try:
                fit_to_portrait(fn(full, seed), path)
                return
            except ProviderUnavailable as e:
                log(f"{name} unavailable: {e}")
                break
            except Exception as e:  # noqa: BLE001
                log(f"{name} attempt {attempt + 1} failed: {e}")
                time.sleep(4 * (attempt + 1))
    log("using fallback card for:", prompt[:50])
    fallback_card(prompt, path)


# ---------- 4. voice ----------
def clean_for_tts(t):
    """Strip characters that make the voice service choke."""
    t = (t.replace("\u2019", "'").replace("\u2018", "'")
          .replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2014", ", ").replace("\u2013", " to "))
    t = t.replace("&", " and ").replace("%", " percent")
    t = re.sub(r"[*_#`~<>\[\]{}|\\^=+]", " ", t)
    t = re.sub(r"[^\x20-\x7E]", " ", t)  # emoji, odd symbols
    return re.sub(r"\s+", " ", t).strip()


def tts_edge(text, path, rate):
    import edge_tts

    async def run():
        kw = {"rate": rate} if rate else {}
        await edge_tts.Communicate(text, VOICE, **kw).save(str(path))

    asyncio.run(run())


def tts_gtts(text, path):
    from gtts import gTTS

    gTTS(text, lang="en", tld="com").save(str(path))


def tts_cloudflare(text, path, model):
    """Deepgram Aura via Cloudflare: far more natural than the free robotic voices."""
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{model}"
    )
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"text": text, "speaker": CF_VOICE, "encoding": "mp3"},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"cloudflare tts {r.status_code}: {r.text[:200]}")
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        res = r.json().get("result") or {}
        b64 = res.get("audio") if isinstance(res, dict) else None
        if not b64:
            raise RuntimeError("cloudflare tts: no audio in JSON reply")
        path.write_bytes(base64.b64decode(b64))
    else:
        path.write_bytes(r.content)
    if duration(path) < 0.5:
        raise RuntimeError("cloudflare tts: audio too short/invalid")


def make_voice(text, path):
    text = clean_for_tts(text)
    plans = []
    if CF_ACCOUNT and CF_TOKEN:
        plans += [("cf", "@cf/deepgram/aura-2-en"), ("cf", "@cf/deepgram/aura-1")]
    plans += [("edge", VOICE_RATE), ("edge", ""), ("gtts", None)]
    for i, (engine, arg) in enumerate(plans):
        try:
            if path.exists():
                path.unlink()
            if engine == "cf":
                tts_cloudflare(text, path, arg)
            elif engine == "edge":
                tts_edge(text, path, arg)
            else:
                tts_gtts(text, path)
            if path.exists() and path.stat().st_size > 1000:
                if engine != "cf":
                    log(f"voice: used {engine} fallback")
                return
        except Exception as e:  # noqa: BLE001
            log(f"tts attempt {i + 1} ({engine}) failed: {e}")
        time.sleep(2 * (i + 1))
    sys.exit("Text-to-speech failed.")


def duration(path):
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ]
    )
    return float(out.strip())


# ---------- 5. video ----------
def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        log(r.stderr[-2000:])
        sys.exit(f"ffmpeg failed: {' '.join(cmd[:4])}")


def scene_clip(img, aud, clip_dur, idx, out):
    frames = int(clip_dur * FPS)
    if idx % 2 == 0:
        z = f"1+0.18*on/{frames}"
    else:
        z = f"1.18-0.18*on/{frames}"
    vf = (
        "scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={W}x{H}:fps={FPS},format=yuv420p"
    )
    sh(
        [
            "ffmpeg", "-y", "-i", str(img), "-i", str(aud),
            "-filter_complex", f"[0:v]{vf}[v];[1:a]apad,aresample=44100[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{clip_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out),
        ]
    )


def ass_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(scenes, durs, path):
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Cap,DejaVu Sans,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,7,0,2,60,60,430,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines, t0 = [], 0.0
    for sc, (adur, cdur) in zip(scenes, durs):
        words = sc["narration"].split()
        chunks = [" ".join(words[i : i + 3]) for i in range(0, len(words), 3)]
        total = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            span = adur * len(c) / total
            text = c.upper().replace("\n", " ")
            lines.append(
                f"Dialogue: 0,{ass_time(t)},{ass_time(t + span)},Cap,,0,0,0,,{text}"
            )
            t += span
        t0 += cdur
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def assemble(clips, ass_name, final):
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
    sh(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-c", "copy", "joined.mp4"],
        cwd=WORK,
    )
    music = next(iter(sorted(Path("music").glob("*.mp3"))), None) if Path(
        "music"
    ).exists() else None
    cmd = ["ffmpeg", "-y", "-i", "joined.mp4"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
        fc = (
            f"[0:v]ass={ass_name}[v];"
            "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        fc = f"[0:v]ass={ass_name}[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]"]
    cmd += ["-map", "[a]"] if music else ["-map", "0:a"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(final.resolve()),
    ]
    sh(cmd, cwd=WORK)


# ---------- 6. delivery & integrations ----------
def send_telegram(video, caption):
    if not (TG_TOKEN and TG_CHAT):
        log("Telegram not configured - grab the video from the run's Artifacts.")
        return
    with open(video, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo",
            data={"chat_id": TG_CHAT, "caption": caption[:1000], "supports_streaming": True},
            files={"video": f},
            timeout=300,
        )
    log("telegram:", r.status_code, r.text[:200])


def upload_to_cloudinary(video_path: Path) -> str:
    """Uploads local generated video to Cloudinary to generate a public URL for Buffer."""
    log(f"DEBUG: CLOUDINARY_CLOUD_NAME present? {bool(CLOUDINARY_CLOUD_NAME)}")
    log(f"DEBUG: CLOUDINARY_API_KEY present? {bool(CLOUDINARY_API_KEY)}")
    log(f"DEBUG: CLOUDINARY_API_SECRET present? {bool(CLOUDINARY_API_SECRET)}")

    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        log("Cloudinary credentials missing or incomplete. Skipping upload.")
        return ""
    try:
        log("Uploading video to Cloudinary...")
        res = cloudinary.uploader.upload(str(video_path), resource_type="video")
        url = res.get("secure_url", "")
        log("Cloudinary upload successful:", url)
        return url
    except Exception as e:
        log("Cloudinary upload failed with error:", e)
        return ""


def get_social_channel_ids(buffer_token: str) -> list:
    """Queries Buffer GraphQL API to retrieve connected TikTok, Instagram, and YouTube channel IDs."""
    url = "https://api.buffer.com"
    headers = {
        "Authorization": f"Bearer {buffer_token}",
        "Content-Type": "application/json",
    }
    
    org_query = """
    query GetAccount {
      account {
        organizations {
          id
        }
      }
    }
    """
    try:
        r = requests.post(url, json={"query": org_query}, headers=headers, timeout=30)
        data = r.json()
        log(f"DEBUG: Buffer Account response: {data}")
        orgs = data.get("data", {}).get("account", {}).get("organizations", [])
        if not orgs:
            log("No Buffer organization found.")
            return []
        
        org_id = orgs[0]["id"]

        channels_query = """
        query GetChannels($input: ChannelsInput!) {
          channels(input: $input) {
            id
            name
            service
          }
        }
        """
        r = requests.post(
            url, 
            json={"query": channels_query, "variables": {"input": {"organizationId": org_id}}}, 
            headers=headers, 
            timeout=30
        )
        chan_data = r.json()
        log(f"DEBUG: Buffer Channels response: {chan_data}")
        channels = chan_data.get("data", {}).get("channels", [])
        
        target_services = {"tiktok", "instagram", "youtube"}
        matched_channel_ids = []
        
        for channel in channels:
            service = channel.get("service", "").lower()
            if service in target_services:
                log(f"Found {service.capitalize()} channel: {channel.get('name')} (ID: {channel['id']})")
                matched_channel_ids.append(channel["id"])
                
        return matched_channel_ids
    except Exception as e:
        log("Error fetching social channel IDs:", e)
        return []


def publish_to_buffer_multi(video_url: str, caption: str):
    """Sends the generated video URL and caption to Buffer for all connected channels."""
    log(f"DEBUG: BUFFER_ACCESS_TOKEN present? {bool(BUFFER_ACCESS_TOKEN)}")
    if not BUFFER_ACCESS_TOKEN:
        log("BUFFER_ACCESS_TOKEN not set. Skipping Buffer.")
        return

    channel_ids = get_social_channel_ids(BUFFER_ACCESS_TOKEN)
    if not channel_ids:
        log("No target social channels found in Buffer. Skipping auto-publish.")
        return

    url = "https://api.buffer.com"
    headers = {
        "Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    
    query = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        post {
          id
          status
        }
        userErrors {
          message
        }
      }
    }
    """
    
    for cid in channel_ids:
        variables = {
            "input": {
                "channelId": cid,
                "text": caption,
                "schedulingType": "NOW",
                "assets": [
                    {
                        "video": {
                            "url": video_url
                        }
                    }
                ]
            }
        }

        try:
            r = requests.post(url, json={"query": query, "variables": variables}, headers=headers, timeout=30)
            res = r.json()
            log(f"DEBUG: Buffer CreatePost response for {cid}: {res}")
            if "errors" in res or res.get("data", {}).get("createPost", {}).get("userErrors"):
                log(f"Failed to post to channel {cid}:", res)
            else:
                log(f"Successfully queued video to Buffer for channel {cid}!")
        except Exception as e:
            log(f"Buffer API error for channel {cid}:", e)


# ---------- main ----------
def main():
    OUT.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    topic, consume, next_topic = pick_topic()
    log("topic:", topic)

    data = fetch_script(topic, next_topic)
    scenes = data["scenes"]
    log("title:", data["title"], "| scenes:", len(scenes))

    seed = random.randint(1, 10**6)
    clips, durs = [], []
    for i, sc in enumerate(scenes):
        img = WORK / f"s{i}.png"
        aud = WORK / f"s{i}.mp3"
        make_image(sc["image_prompt"], img, seed + i)
        make_voice(sc["narration"], aud)
        adur = duration(aud)
        cdur = adur + PAD
        clip = WORK / f"c{i}.mp4"
        scene_clip(img, aud, cdur, i, clip)
        clips.append(clip)
        durs.append((adur, cdur))
        log(f"scene {i + 1}/{len(scenes)} ok ({adur:.1f}s)")
        time.sleep(1.5)

    total = sum(c for _, c in durs)
    log(f"total length: {total:.1f}s")
    if total > 58:
        log("warning: over ~58s; trim the script prompt if you want strict Shorts length")

    build_ass(scenes, durs, WORK / "captions.ass")
    final = OUT / "short.mp4"
    assemble(clips, "captions.ass", final)

    caption = f"{data['title']}\n\n{data['description']}"
    (OUT / "title.txt").write_text(caption, encoding="utf-8")
    send_telegram(final, caption)

    # Automatically upload video to Cloudinary and broadcast to all Buffer channels
    public_url = upload_to_cloudinary(final)
    if public_url:
        publish_to_buffer_multi(public_url, caption)
    else:
        log("No public video URL available. Skipping Buffer auto-publish.")

    if consume:
        consume_topic(topic)
    log("done")


if __name__ == "__main__":
    main()
