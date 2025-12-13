#!/usr/bin/env python3
"""
Email Provider Verification (MX-based)

Purpose:
---------
Detect which email provider a target domain uses (Google Workspace, Microsoft 365 / Outlook, or Other)
by inspecting MX records via DNS-over-HTTPS.

This is used to:
- Route prospects to provider-specific sending inboxes
- Apply provider-specific warmup strategies
- Protect deliverability and inbox placement in high-stakes outbound campaigns

Designed for:
- High-volume GTM workflows
- Deliverability-aware outbound systems
- Provider-aware inbox warmup and routing
"""

import os
import time
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────
# PROVIDER SIGNATURES
# ─────────────────────────────────────────────────────────────

GOOGLE_MX_RECORDS = {
    "aspmx.l.google.com",
    "alt1.aspmx.l.google.com",
    "alt2.aspmx.l.google.com",
    "alt3.aspmx.l.google.com",
    "alt4.aspmx.l.google.com",
}

OUTLOOK_MX_SUFFIX = "protection.outlook.com"

# ─────────────────────────────────────────────────────────────
# NETWORK CONFIG (OPTIONAL PROXY)
# ─────────────────────────────────────────────────────────────

PROXY_URL = os.getenv("HTTP_PROXY")  # optional
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# DNS-over-HTTPS (Cloudflare)
DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"

# Simple in-memory cache to avoid duplicate lookups
CACHE = {}

# ─────────────────────────────────────────────────────────────
# DNS LOOKUP
# ─────────────────────────────────────────────────────────────

def doh_lookup(domain: str, retries: int = 3):
    headers = {"accept": "application/dns-json"}
    params = {"name": domain, "type": "MX"}

    for attempt in range(retries):
        try:
            resp = requests.get(
                DOH_ENDPOINT,
                headers=headers,
                params=params,
                proxies=PROXIES,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"❌ DNS lookup failed for {domain}: {e}")
            time.sleep(2 ** attempt)

    return None

# ─────────────────────────────────────────────────────────────
# PROVIDER CLASSIFICATION
# ─────────────────────────────────────────────────────────────

def get_email_provider(domain: str) -> str:
    """
    Returns:
      - 'Google'
      - 'Outlook'
      - 'Other'
    """

    if domain in CACHE:
        return CACHE[domain]

    data = doh_lookup(domain)
    if not data or "Answer" not in data:
        CACHE[domain] = "Other"
        return "Other"

    for answer in data.get("Answer", []):
        if answer.get("type") == 15:  # MX record
            mx_host = answer["data"].split()[1].rstrip(".").lower()

            if mx_host in GOOGLE_MX_RECORDS:
                CACHE[domain] = "Google"
                return "Google"

            if mx_host.endswith(OUTLOOK_MX_SUFFIX):
                CACHE[domain] = "Outlook"
                return "Outlook"

    CACHE[domain] = "Other"
    return "Other"

# ─────────────────────────────────────────────────────────────
# CSV PIPELINE
# ─────────────────────────────────────────────────────────────

def process_domain(domain: str, idx: int, total: int):
    d = str(domain).strip()
    if not d:
        return d, "Other"

    print(f"[{idx}/{total}] 🔍 Checking {d}")
    return d, get_email_provider(d)

def process_csv(input_csv: str, output_csv: str, max_workers: int = 10):
    df = pd.read_csv(input_csv)

    if "provider" not in df.columns:
        df["provider"] = ""

    total = len(df)
    print(f"Processing {total} domains…")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_domain, dom, i, total): dom
            for i, dom in enumerate(df.iloc[:, 0], start=1)
        }

        for completed, fut in enumerate(as_completed(futures), start=1):
            domain = futures[fut]
            try:
                d, provider = fut.result()
                df.loc[df.iloc[:, 0] == d, "provider"] = provider
            except Exception as e:
                print(f"❌ Error processing {domain}: {e}")
                df.loc[df.iloc[:, 0] == domain, "provider"] = "Error"

            if completed % 100 == 0 or completed == total:
                print(f"Progress: {completed}/{total}")

    df.to_csv(output_csv, index=False)
    print(f"✅ Results written to {output_csv}")

# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    process_csv(
        input_csv="domains.csv",
        output_csv="domains_with_providers.csv",
        max_workers=10,
    )
