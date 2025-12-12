#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Batch Video Rendering Orchestrator (Portfolio Example)

This script represents a production-grade automation used to render large volumes
of personalized videos for GTM and outbound campaigns.

Core responsibilities:
- Orchestrates batch rendering across hundreds or thousands of prospects
- Coordinates dynamic media assets (video, audio, images, brand data)
- Integrates AI-generated voice and face assets
- Handles retries, failures, cache hygiene, and memory pressure
- Designed for long-running, unattended execution

This code is intentionally shared as a representative example.
It is not intended to be executed outside its original environment.
Sensitive paths, identifiers, and client-specific details have been sanitized.
"""

import os
import shutil
import subprocess
import sys
import time
import logging
import glob
import re
from typing import List

# ─── START DELAY (optional) ───────────────────────────────────────────────────
DELAY_HOURS   = 0
DELAY_MINUTES = 0
DELAY_SECONDS = DELAY_HOURS * 3600 + DELAY_MINUTES * 60
if DELAY_SECONDS > 0:
    time.sleep(DELAY_SECONDS)

# ─── CONFIG (SANITIZED PATHS) ─────────────────────────────────────────────────
PROJECT_DIR          = "/PATH/TO/PROJECT_ROOT"
INDEX_CSV            = f"{PROJECT_DIR}/index.csv"

PROJECT_SOLUTIONS    = f"{PROJECT_DIR}/Solutions.aep"
PROJECT_SALES        = f"{PROJECT_DIR}/Sales.aep"

AERENDER_BIN         = "/Applications/Adobe After Effects 2025/aerender"

FOOTAGE_ROOT         = f"{PROJECT_DIR}/(Footage)"
VARIABLES_ROOT       = f"{FOOTAGE_ROOT}/Variables"
OUTPUT_DIR           = f"{PROJECT_DIR}/render"

START_INDEX          = 1311
END_INDEX            = 2085

AE_APP_NAME          = "Adobe After Effects 2025"
AE_PROC_SUBSTR       = "After Effects 2025"
RENDER_TIMEOUT       = 160 * 60  # seconds

# ─── Wav2Lip CONFIG ───────────────────────────────────────────────────────────
W2L_DIR              = "/PATH/TO/Wav2Lip"
W2L_CHECKPOINT       = "checkpoints/wav2lip.pth"
W2L_DESKTOP_OUTDIR   = "/PATH/TO/TEMP_OUTPUT"

TEMPLATES_DIR             = f"{FOOTAGE_ROOT}/Assets/templates"
TEMPLATE_FACE_SOLUTIONS   = f"{TEMPLATES_DIR}/Solutions_engineer_Template.mp4"
TEMPLATE_FACE_SALES       = f"{TEMPLATES_DIR}/Sales_engineer_Template.mp4"

CLONES_DIR_A  = f"{VARIABLES_ROOT}/Clones"
VOICES_DIR_A  = f"{VARIABLES_ROOT}/Voices"

W2L_LINK_DIR = "/PATH/TO/SYMLINK_DIR"
os.makedirs(W2L_LINK_DIR, exist_ok=True)

VARIABLES_LIST = [
    ("Linkedin",  "LinkedinX.png"),
    ("Names",     "NameX.csv"),
    ("Website",   "WebsiteX.mp4"),
    ("Companies", "CompanyX.csv"),
    ("Clones",    "CloneX.mp4"),
    ("Voices",    "VoiceX.wav"),
    ("Color_1_X", "Color_1_X.csv"),
    ("Color_2_X", "Color_2_X.csv"),
    ("Profiles",  "ProfileX.png"),
    ("Hi_names",  "Hi_nameX.csv"),
    ("Logos",     "LogoX.png"),
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ─── SAFE PROCESS MANAGEMENT ──────────────────────────────────────────────────
SAFE_PROCS = {
    "Google Chrome","Google Chrome Helper","chromedriver",
}

def _run_quiet(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _pkill_safe(patterns):
    for pat in patterns:
        try:
            out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
            pids = [pid for pid in out.stdout.splitlines() if pid]
        except Exception:
            pids = []
        for pid in pids:
            try:
                ps = subprocess.run(["ps", "-o", "comm=", "-p", pid], capture_output=True, text=True)
                comm = (ps.stdout or "").strip()
                if any(safe.lower() in comm.lower() for safe in SAFE_PROCS):
                    continue
                subprocess.run(["kill", "-9", pid])
            except Exception:
                pass

def close_nonessential_apps():
    for app in ["Safari","Slack","Zoom","Dropbox","Notion","Spotify"]:
        _run_quiet(["osascript","-e", f'tell application "{app}" to quit'])
    _pkill_safe(["Slack","Dropbox","Spotify"])

def kill_adobe_helpers():
    _pkill_safe([
        "aerendercore","dynamiclinkmanager","dynamiclinkmediaserver",
        "AdobeIPCBroker","Adobe CEF Helper"
    ])

# ─── CACHE HYGIENE ────────────────────────────────────────────────────────────
def _safe_rmtree(path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass

def clear_ae_disk_caches():
    home = os.path.expanduser("~")
    targets = [
        f"{home}/Library/Caches/Adobe/After Effects",
        f"{home}/Library/Application Support/Adobe/Common/Media Cache",
    ]
    for p in targets:
        if os.path.exists(p):
            _safe_rmtree(p)

def deep_reboot_like_prep():
    close_nonessential_apps()
    kill_adobe_helpers()
    clear_ae_disk_caches()
    time.sleep(1)

# ─── ASSET SWAPPING ───────────────────────────────────────────────────────────
def swap_assets(idx: int):
    for folder, pattern in VARIABLES_LIST:
        src = os.path.join(VARIABLES_ROOT, folder, pattern.replace("X", str(idx)))
        dst = os.path.join(VARIABLES_ROOT, folder, pattern)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(src):
            shutil.copy2(src, dst)

# ─── AE RENDERING ─────────────────────────────────────────────────────────────
def open_ae(project_path: str):
    subprocess.run(["open","-a",AE_APP_NAME,project_path])
    time.sleep(25)

def render_attach(project_path: str) -> bool:
    out = os.path.join(OUTPUT_DIR, "CompositionX.mp4")
    cmd = [
        AERENDER_BIN,"-project",project_path,"-comp","CompositionX",
        "-output",out,"-close","DO_NOT_SAVE_CHANGES",
    ]
    try:
        res = subprocess.run(cmd, timeout=RENDER_TIMEOUT)
        return res.returncode == 0
    except subprocess.TimeoutExpired:
        return False

def rename_output(idx: int) -> bool:
    src = os.path.join(OUTPUT_DIR, "CompositionX.mp4")
    dst = os.path.join(OUTPUT_DIR, f"Composition{idx}.mp4")
    if os.path.exists(src):
        os.replace(src, dst)
        return True
    return False

# ─── CSV → PROJECT SELECTION ──────────────────────────────────────────────────
def _load_index_list(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", errors="ignore") as f:
        raw = re.split(r"[,\n]+", f.read())
    return [t.strip().lower() for t in raw if t.strip()]

def _project_for_index(i: int, tokens: List[str]) -> str:
    if 1 <= i <= len(tokens):
        if tokens[i-1].startswith("sale"):
            return PROJECT_SALES
    return PROJECT_SOLUTIONS

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    index_tokens = _load_index_list(INDEX_CSV)

    deep_reboot_like_prep()

    for i in range(START_INDEX, END_INDEX + 1):
        project_path = _project_for_index(i, index_tokens)
        logging.info(f"Rendering index {i} using {os.path.basename(project_path)}")

        swap_assets(i)

        attempt = 0
        while True:
            attempt += 1
            deep_reboot_like_prep()
            open_ae(project_path)

            ok_render = render_attach(project_path)
            ok_rename = rename_output(i) if ok_render else False

            if ok_render and ok_rename:
                logging.info(f"Completed render {i}")
                break

            logging.warning(f"Retrying render {i} (attempt {attempt})")
            time.sleep(10)

    kill_adobe_helpers()
    logging.info("All renders complete")

if __name__ == "__main__":
    main()
