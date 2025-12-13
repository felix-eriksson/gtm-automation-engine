#!/usr/bin/env python3
"""
AI Voice Synthesis Pipeline (ElevenLabs)

This script generates AI voice assets at scale from structured text inputs.
It is used as part of a larger GTM personalization system where spoken
personalization is injected into downstream media generation pipelines.

Typical use cases:
- Personalized video voiceovers
- Prospect-specific spoken intros
- Campaign- or role-adapted narration
"""

import os
import time
import csv
import logging
import requests
from typing import List

# ── CONFIG (ENV-DRIVEN) ────────────────────────────────────────────────

CSV_FILE_PATH = os.getenv("VOICE_INPUT_CSV", "./inputs/voices.csv")
OUTPUT_DIR    = os.getenv("VOICE_OUTPUT_DIR", "./outputs/voices")

START_INDEX   = int(os.getenv("START_INDEX", "1"))  # resume-safe

# ElevenLabs configuration
VOICE_ID      = os.getenv("ELEVENLABS_VOICE_ID", "VOICE_ID_PLACEHOLDER")
MODEL_ID      = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
OUTPUT_FORMAT = "wav"

VOICE_SETTINGS = {
    "stability": 0.35,
    "similarity_boost": 0.90,
    "style": 0.10,
    "use_speaker_boost": True,
    "speed": 1.12,
}

XI_API_KEY = os.getenv("XI_API_KEY")  # must be set externally

# Retry & timeout tuning
MAX_RETRIES  = 6
BACKOFF_BASE = 2.0
TIMEOUT_SEC  = 180

# ── LOGGING ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── HELPERS ────────────────────────────────────────────────────────────

def read_texts(csv_path: str) -> List[str]:
    """Read one text input per row from CSV."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        return [row[0] for row in csv.reader(f)]

def synthesize_voice(text: str) -> bytes:
    """Call ElevenLabs TTS API with retry + exponential backoff."""
    if not XI_API_KEY:
        raise RuntimeError("XI_API_KEY is not set")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"
    headers = {
        "xi-api-key": XI_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=TIMEOUT_SEC,
                stream=True,
            )

            if resp.status_code == 403:
                logging.error("403 Forbidden — invalid API key or voice permissions.")
                raise RuntimeError("Unauthorized request")

            if resp.status_code in (429, 500, 502, 503, 504):
                wait = (BACKOFF_BASE ** (attempt - 1)) + (0.1 * attempt)
                logging.warning(
                    f"API {resp.status_code} (attempt {attempt}/{MAX_RETRIES}); "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.content

        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            wait = (BACKOFF_BASE ** (attempt - 1)) + (0.1 * attempt)
            logging.warning(
                f"Network error: {e} (attempt {attempt}/{MAX_RETRIES}); "
                f"retrying in {wait:.1f}s"
            )
            time.sleep(wait)

    raise RuntimeError("Voice synthesis failed after max retries.")

# ── MAIN ───────────────────────────────────────────────────────────────

def main():
    texts = read_texts(CSV_FILE_PATH)
    total = len(texts)

    logging.info(f"Loaded {total} text rows. Starting at index {START_INDEX}.")

    for i in range(START_INDEX - 1, total):
        text = (texts[i] or "").strip()
        if not text:
            logging.info(f"Row {i+1}: empty → skipped.")
            continue

        out_path = os.path.join(OUTPUT_DIR, f"voice_{i+1}.{OUTPUT_FORMAT}")
        if os.path.exists(out_path):
            logging.info(f"Row {i+1}: output already exists → skipped.")
            continue

        logging.info(f"▶ Row {i+1}: generating voice…")
        try:
            audio = synthesize_voice(text)
            with open(out_path, "wb") as f:
                f.write(audio)
            logging.info(f"✅ Saved {out_path}")
        except Exception as e:
            logging.error(f"❌ Failed row {i+1}: {e}")

    logging.info("Voice generation complete.")

if __name__ == "__main__":
    main()
