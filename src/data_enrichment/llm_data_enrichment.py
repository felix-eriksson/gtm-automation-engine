#!/usr/bin/env python3
"""
LLM-powered CSV enrichment utility.

This script processes a CSV file row-by-row and uses an LLM (via the OpenAI API)
to enrich, classify, or normalize unstructured inputs into structured outputs.

Typical GTM use cases include:
- ICP qualification
- Role or persona classification
- Time zone normalization
- Company name normalization
- Campaign routing labels

The script is intentionally generic and prompt-driven so it can be reused
across many enrichment tasks without code changes.
"""

import os
import time
import pandas as pd
import requests
from typing import Optional
from openai import OpenAI

# ─────────────────────────────────────────────────────────────
# Configuration (environment-driven)
# ─────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CSV_FILE_PATH = os.getenv("CSV_FILE_PATH", "input.csv")

INPUT_COLUMN = os.getenv("INPUT_COLUMN", "input_text")
OUTPUT_COLUMN = os.getenv("OUTPUT_COLUMN", "enriched_output")

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
RETRY_DELAY_SECONDS = int(os.getenv("RETRY_DELAY_SECONDS", "5"))

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a data enrichment assistant. "
    "Return concise, deterministic outputs suitable for automation."
)

USER_PROMPT_TEMPLATE = os.getenv(
    "USER_PROMPT_TEMPLATE",
    "{input}"
)

if not OPENAI_API_KEY:
    raise RuntimeError(
        "Missing OPENAI_API_KEY environment variable. "
        "Set it before running this script."
    )

# ─────────────────────────────────────────────────────────────
# OpenAI client
# ─────────────────────────────────────────────────────────────

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────────────────────────
# LLM call helper with retries
# ─────────────────────────────────────────────────────────────

def call_llm_with_retries(
    model: str,
    user_input: str,
    max_retries: int,
    delay_seconds: int,
) -> Optional[str]:
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": USER_PROMPT_TEMPLATE.format(input=user_input),
                    },
                ],
            )
            return response.choices[0].message.content.strip()

        except (requests.RequestException, Exception) as e:
            print(
                f"[WARN] LLM call failed (attempt {attempt}/{max_retries}): {e}"
            )
            if attempt < max_retries:
                time.sleep(delay_seconds)

    print("[ERROR] Max retries reached. Skipping row.")
    return None

# ─────────────────────────────────────────────────────────────
# Main processing loop
# ─────────────────────────────────────────────────────────────

def main():
    print(f"Loading CSV: {CSV_FILE_PATH}")
    df = pd.read_csv(CSV_FILE_PATH)

    if INPUT_COLUMN not in df.columns:
        raise RuntimeError(
            f"Input column '{INPUT_COLUMN}' not found in CSV."
        )

    if OUTPUT_COLUMN not in df.columns:
        df[OUTPUT_COLUMN] = ""

    total_rows = len(df)
    print(f"Processing {total_rows} rows using model '{MODEL_NAME}'")

    for idx, row in df.iterrows():
        input_value = str(row.get(INPUT_COLUMN, "")).strip()

        if not input_value:
            df.at[idx, OUTPUT_COLUMN] = ""
            continue

        result = call_llm_with_retries(
            model=MODEL_NAME,
            user_input=input_value,
            max_retries=MAX_RETRIES,
            delay_seconds=RETRY_DELAY_SECONDS,
        )

        df.at[idx, OUTPUT_COLUMN] = result or ""

        print(f"[{idx + 1}/{total_rows}] processed")

        # Persist progress continuously to avoid data loss
        df.to_csv(CSV_FILE_PATH, index=False)

    print("Enrichment complete.")

# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
