#!/usr/bin/env python3
"""Stickman finance Shorts bot.

topic -> script (Gemini) -> stickman images (Pollinations) -> voice (edge-tts)
      -> vertical video with zoom + captions (FFmpeg) -> Telegram / Buffer (TikTok)
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
BUFFER_TIKTOK_CHANNEL_ID = os.environ.get("BUFFER_TIKTOK_CHANNEL_ID", "")
VIDEO_PUBLIC_URL = os.environ.get("VIDEO_PUBLIC_URL", "")

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
        if l
