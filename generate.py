#!/usr/bin/env python3
"""Stickman finance Shorts bot.

topic -> script (Gemini) -> stickman images (Pollinations) -> voice (edge-tts)
      -> vertical video with zoom + captions (FFmpeg) -> Telegram / Cloudinary / Buffer (TikTok)
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
BUFFER_TIKTOK_CHANNEL_ID = os.environ.get("BUFFER_TIKTOK_CHANNEL_ID", "")

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
    
