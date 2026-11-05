#!/usr/bin/env python3
"""Stickman finance Shorts bot.

topic -> script (Gemini) -> stickman images (Pollinations) -> voice (edge-tts)
      -> vertical video with zoom + captions (FFmpeg) -> Telegram / artifact
"""
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

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

W, H, FPS = 1080, 1920, 30
PAD = 0.3  # seconds of silence after each scene
OUT = Path("output")
WORK = OUT / "work"

# This prefix is what makes every image look like the same channel.
STYLE = (
    "simple minimalist stick figure drawing, thick black outlines, "
    "round white head with dot eyes, plain off-white background, "
    "flat pastel colors, clean vector doodle style, no text, no words, "
)

PROMPT = """You write scripts for a faceless YouTube Shorts channel that explains
personal finance with stick-figure cartoons.

Topic: {topic}

Rules:
- Total narration 105-125 words (about 45 seconds spoken).
- First scene is a punchy hook that makes people stay.
- Educational only. No investment advice, no "buy X", no guaranteed returns.
- Plain, friendly words. Short sentences. One idea per scene.
- 8 scenes. Last scene is a quick takeaway line.
- For each scene write an image_prompt describing ONLY what the stick figure
  scene shows (action, props like coins, bank, chart, piggy bank, house).
  Vary the scene each time. Do not mention style or text in the image.

Return ONLY JSON in this shape:
{{
  "title": "catchy title under 70 chars",
  "description": "1-2 sentence description with 3 hashtags including #shorts",
  "scenes": [{{"narration": "...", "image_prompt": "..."}}]
}}"""


def log(*a):
    print(*a, flush=True)


# ---------- 1. topic ----------
def pick_topic():
    forced = os.environ.get("TOPIC", "").strip()
    if forced:
        return forced, False
    for line in Path("topics.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line, True
    sys.exit("topics.txt is empty - add more topics.")


def consume_topic(topic):
    lines = Path("topics.txt").read_text().splitlines()
    left = [l for l in lines if l.strip() != topic]
    Path("topics.txt").write_text("\n".join(left) + "\n")
    with open("done.txt", "a") as f:
        f.write(topic + "\n")


# ---------- 2. script ----------
def fetch_script(topic):
    if not GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set.")
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(topic=topic)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.9,
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
        for attempt in range(3):
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
                for s in data["scenes"]:
                    assert s["narration"] and s["image_prompt"]
                log("script model:", model)
                return data
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"{model} attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (attempt + 1))
    sys.exit(f"Could not get a script: {last}")


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


def make_image(prompt, path, seed):
    full = STYLE + prompt
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(full)
        + f"?width=576&height=1024&nologo=true&model=flux&seed={seed}"
    )
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith(
                "image"
            ):
                path.write_bytes(r.content)
                return
            raise RuntimeError(f"status {r.status_code}")
        except Exception as e:  # noqa: BLE001
            log(f"image attempt {attempt + 1} failed: {e}")
            time.sleep(8 * (attempt + 1))
    log("using fallback card for:", prompt[:50])
    fallback_card(prompt, path)


# ---------- 4. voice ----------
def make_voice(text, path):
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, VOICE, rate=VOICE_RATE).save(str(path))

    for attempt in range(3):
        try:
            asyncio.run(run())
            if path.exists() and path.stat().st_size > 1000:
                return
        except Exception as e:  # noqa: BLE001
            log(f"tts attempt {attempt + 1} failed: {e}")
            time.sleep(4)
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


# ---------- 6. delivery ----------
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


# ---------- main ----------
def main():
    OUT.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    topic, consume = pick_topic()
    log("topic:", topic)

    data = fetch_script(topic)
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

    if consume:
        consume_topic(topic)
    log("done")


if __name__ == "__main__":
    main()
