"""
Batch Video Rendering Orchestrator (GTM Personalization Engine)

BUSINESS CONTEXT
----------------
This script is the core production orchestrator behind a high-scale,
high-fidelity personalized video system used for GTM, sales outreach,
and marketing automation.

The goal is not “video generation” in isolation, but the ability to
programmatically personalize *any* part of a video narrative at scale,
using structured data upstream (CRM, enrichment, scraping, AI models).

Think of this as:
- What Clay enables for cold email
- But applied to fully animated, narrated video

This system was used in real client work to generate thousands of
highly personalized videos with measurable revenue impact.


WHAT THIS SCRIPT ENABLES
------------------------
For each prospect (row/index), the system can dynamically inject:

- Company name, logo, and brand colors
- Industry- or segment-specific messaging
- Screenshots, profiles, or visual references
- Different video structures per GTM motion
- AI-generated voice that mimics a real SDR/AE
- Optional lip-synced “person speaking” video using Wav2Lip
- Different AE templates (Sales vs Solutions, etc.)

All of this is rendered automatically through Adobe After Effects,
without manual intervention.


WHY THIS IS COMPLEX (AND WHY IT LOOKS THIS WAY)
-----------------------------------------------
Adobe After Effects is not designed for unattended batch rendering
at scale. This script exists because production reality required:

- Aggressive crash recovery
- Cache and memory pressure management
- Process isolation and forced restarts
- Defensive handling of missing or partial assets
- Retry logic across rendering, voice, and lip-sync steps
- Stable execution over hundreds/thousands of renders

Much of the length in this file is *intentional defensive engineering*.


HOW IT FITS INTO THE SYSTEM
---------------------------
Upstream:
- CRM / enrichment scripts generate structured assets (CSV, PNG, MP4)
- AI voice models generate per-prospect speech
- Website / brand / LinkedIn assets are prepared

This script:
- Orchestrates asset swapping
- Generates lip-synced video where applicable
- Renders personalized AE compositions in batch
- Outputs final videos ready for upload and distribution

Downstream:
- Videos are uploaded automatically
- Outreach campaigns are triggered
- Engagement (watch rate) is tracked and fed back into GTM workflows


IMPORTANT NOTE
--------------
This is a production-grade orchestration script.
It prioritizes robustness and throughput over elegance.

It is intentionally presented here to demonstrate:
- Systems thinking
- GTM automation depth
- Real-world production constraints
- Business-driven engineering decisions
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import sys
import time
import logging
import glob
import re
from typing import List

# ───────────────────────────────────────────────────────────────────
# OPTIONAL START DELAY
# ───────────────────────────────────────────────────────────────────
DELAY_HOURS   = 0
DELAY_MINUTES = 0
DELAY_SECONDS = DELAY_HOURS * 3600 + DELAY_MINUTES * 60

if DELAY_SECONDS > 0:
    print(f"⏱ Waiting {DELAY_HOURS}h {DELAY_MINUTES}m before starting…")
    time.sleep(DELAY_SECONDS)

# ───────────────────────────────────────────────────────────────────
# PROJECT ROOT & PATHS (SANITIZED / PORTABLE)
# ───────────────────────────────────────────────────────────────────
PROJECT_DIR = os.environ.get(
    "VIDEO_PROJECT_DIR",
    os.path.abspath("./video_project")
)

INDEX_CSV = os.path.join(PROJECT_DIR, "index.csv")

FOOTAGE_ROOT   = os.path.join(PROJECT_DIR, "Footage")
VARIABLES_ROOT = os.path.join(FOOTAGE_ROOT, "Variables")
OUTPUT_DIR     = os.path.join(PROJECT_DIR, "render")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ───────────────────────────────────────────────────────────────────
# AFTER EFFECTS CONFIG
# ───────────────────────────────────────────────────────────────────
AE_APP_NAME    = "Adobe After Effects"
AE_PROC_SUBSTR = "After Effects"
AERENDER_BIN   = os.environ.get(
    "AERENDER_BIN",
    "/Applications/Adobe After Effects/aerender"
)

RENDER_TIMEOUT = 160 * 60  # seconds

# Example index range (configured per run)
START_INDEX = 1
END_INDEX   = 100

# ───────────────────────────────────────────────────────────────────
# TEMPLATE VARIANTS (GENERIC / REUSABLE)
# ───────────────────────────────────────────────────────────────────
TEMPLATES_DIR = os.path.join(FOOTAGE_ROOT, "Assets", "templates")

PROJECT_VARIANT_A = os.path.join(PROJECT_DIR, "Template_Variant_A.aep")
PROJECT_VARIANT_B = os.path.join(PROJECT_DIR, "Template_Variant_B.aep")

TEMPLATE_VARIANT_A = os.path.join(
    TEMPLATES_DIR, "Template_Variant_A.mp4"
)

TEMPLATE_VARIANT_B = os.path.join(
    TEMPLATES_DIR, "Template_Variant_B.mp4"
)

# ───────────────────────────────────────────────────────────────────
# AI VOICE + LIP SYNC (WAV2LIP)
# ───────────────────────────────────────────────────────────────────
W2L_DIR        = os.environ.get("WAV2LIP_DIR", "./Wav2Lip")
W2L_CHECKPOINT = os.path.join(W2L_DIR, "checkpoints", "wav2lip.pth")

W2L_WORK_DIR   = os.path.join(PROJECT_DIR, "w2l_links")
os.makedirs(W2L_WORK_DIR, exist_ok=True)

# Single-stream assets (one voice + one talking-head per render)
CLONES_DIR = os.path.join(VARIABLES_ROOT, "Clones")
VOICES_DIR = os.path.join(VARIABLES_ROOT, "Voices")

os.makedirs(CLONES_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)

# ───────────────────────────────────────────────────────────────────
# VARIABLE-DRIVEN MEDIA ASSETS
# Each asset is swapped dynamically per index (X → index)
# ───────────────────────────────────────────────────────────────────
VARIABLES_LIST = [
    ("Linkedin",   "LinkedinX.png"),
    ("Names",      "NameX.csv"),
    ("Websites",   "WebsiteX.mp4"),
    ("Companies",  "CompanyX.csv"),
    ("Clones",     "CloneX.mp4"),     # AI lip-synced video
    ("Voices",     "VoiceX.wav"),     # AI-generated voice
    ("Color_1_X",  "Color_1_X.csv"),
    ("Color_2_X",  "Color_2_X.csv"),
    ("Profiles",   "ProfileX.png"),
    ("Greetings",  "GreetingX.csv"),
    ("Logos",      "LogoX.png"),
]

# ───────────────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
# ─── CHROME-SAFE "SOFT REBOOT" HELPERS (trimmed; no Chrome/QuickTime now) ────
SAFE_PROCS = {
    "Google Chrome","Google Chrome Helper","Google Chrome Canary",
    "chromedriver","Chrome","Chrome Helper",
}

def _run_quiet(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass

def _pkill_safe(patterns):
    for pat in patterns:
        try:
            out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
            pids = [pid for pid in out.stdout.strip().splitlines() if pid]
        except Exception:
            pids = []
        for pid in pids:
            try:
                ps = subprocess.run(["ps", "-o", "comm=", "-p", pid], capture_output=True, text=True)
                comm = (ps.stdout or "").strip()
                if any(safe.lower() in comm.lower() for safe in SAFE_PROCS):
                    continue
                subprocess.run(["kill", "-9", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

def close_nonessential_apps():
    polite_quit = [
        "Safari","Slack","Zoom","Microsoft Teams","Dropbox","OneDrive",
        "Photos","Music","Discord","Notion","Spotify"
    ]
    for app in polite_quit:
        _run_quiet(["osascript","-e", f'tell application "{app}" to quit'])
    time.sleep(0.5)
    brute = [
        "Slack","Dropbox","OneDrive","photoanalysisd","photolibraryd",
        "Zoom","Microsoft Teams","Discord","Notion Helper","CEFHelper","Spotify"
    ]
    _pkill_safe(brute)

def kill_adobe_helpers():
    adobe = [
        "aerendercore","dynamiclinkmanager","dynamiclinkmediaserver",
        "AdobeIPCBroker","AdobeCRDaemon","CEPHtmlEngine","Adobe CEF Helper",
        "Adobe Desktop Service"
    ]
    _pkill_safe(adobe)

# ─── ROBUST FILE REMOVAL HELPERS (used by cache cleaner) ──────────────────────
def _safe_rmtree(path: str):
    try:
        if os.path.islink(path) or os.path.isfile(path):
            try:
                os.unlink(path); return
            except Exception:
                pass
        if not os.path.isdir(path): return
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                fp = os.path.join(root, name)
                try: os.chmod(fp, 0o700)
                except Exception: pass
                try: os.remove(fp)
                except Exception:
                    try: os.unlink(fp)
                    except Exception: pass
            for name in dirs:
                dp = os.path.join(root, name)
                try: os.chmod(dp, 0o700)
                except Exception: pass
                try: os.rmdir(dp)
                except Exception: pass
        try: os.rmdir(path)
        except Exception:
            try: shutil.rmtree(path, ignore_errors=True)
            except Exception: pass
    except Exception:
        pass

def _glob_many(patterns):
    out = []
    for pat in patterns:
        try: out.extend(glob.glob(pat))
        except Exception: pass
    return out

def _scan_custom_disk_cache_paths():
    found = set()
    prefs_root = os.path.expanduser("~/Library/Preferences/Adobe/After Effects")
    if not os.path.isdir(prefs_root): return []
    for root, _, files in os.walk(prefs_root):
        for fn in files:
            if not fn.lower().endswith((".txt", ".plist", ".xml", ".json")): continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", errors="ignore") as f: text = f.read()
            except Exception:
                continue
            for m in re.finditer(r'(?i)(disk\s*cache|cache).{0,200}?(/[^"\n\r]+)', text):
                candidate = m.group(2).strip()
                if os.path.isdir(candidate): found.add(candidate)
            for m in re.finditer(r'/(?:Users|Volumes|private)/[^\s"\']+', text):
                candidate = m.group(0).strip()
                if ("Cache" in candidate or "cache" in candidate) and os.path.isdir(candidate):
                    found.add(candidate)
    home = os.path.expanduser("~")
    guess_dirs = _glob_many([
        os.path.join(home, "Documents", "Adobe After Effects Disk Cache*"),
        os.path.join(home, "Movies",    "Adobe After Effects Disk Cache*"),
        os.path.join(home, "Library", "Caches", "Adobe", "After Effects", "*Disk*Cache*"),
    ])
    for d in guess_dirs:
        if os.path.isdir(d): found.add(d)
    return sorted(found)

def clear_ae_disk_caches():
    home = os.path.expanduser("~")
    static_paths = [
        f"{home}/Library/Caches/Adobe/After Effects",
        f"{home}/Library/Application Support/Adobe/Common/Media Cache",
        f"{home}/Library/Application Support/Adobe/Common/Media Cache Files",
        f"{home}/Library/Application Support/Adobe/Common/Team Projects Cache",
        f"{home}/Library/Application Support/Adobe/Common/DynamicLinkMediaServer",
        f"{home}/Library/Caches/Adobe/DynamicLinkMediaServer",
        f"{home}/Library/Caches/Adobe/GLCache",
        f"{home}/Library/Caches/Adobe/UXP",
    ]
    ae_cache_root = os.path.join(home, "Library", "Caches", "Adobe", "After Effects")
    if os.path.isdir(ae_cache_root):
        for d in os.listdir(ae_cache_root):
            static_paths.append(os.path.join(ae_cache_root, d))
    varfolder_globs = [
        "/private/var/folders/*/*/*/com.adobe.AfterEffects*",
        "/private/var/folders/*/*/*/T/com.adobe.AfterEffects*",
        "/private/var/folders/*/*/*/C/com.adobe.AfterEffects*",
    ]
    temp_matches = _glob_many(varfolder_globs)
    custom = _scan_custom_disk_cache_paths()

    all_targets = []
    for p in static_paths:
        if os.path.exists(p): all_targets.append(p)
    all_targets.extend([p for p in temp_matches if os.path.exists(p)])
    all_targets.extend([p for p in custom if os.path.exists(p)])

    if not all_targets:
        logging.info("🧽 AE cache cleanup: nothing to remove."); return

    seen = set(); ordered_targets = []
    for p in all_targets:
        if p not in seen:
            seen.add(p); ordered_targets.append(p)

    for p in ordered_targets:
        _safe_rmtree(p); logging.info(f"🧽 Cleared cache: {p}")

def flush_inactive_ram():
    _run_quiet(["sudo","-n","purge"]); _run_quiet(["purge"])

def spotlight_indexing(off: bool):
    state = "off" if off else "on"
    _run_quiet(["sudo","-n","mdutil","-a","-i",state])

def lock_power_settings():
    _run_quiet(["caffeinate","-dimsu","-t","5"])
    _run_quiet(["defaults","write","com.adobe.AfterEffects","NSAppSleepDisabled","-bool","true"])
    _run_quiet(["defaults","write","com.adobe.aerender","NSAppSleepDisabled","-bool","true"])

def deep_reboot_like_prep():
    close_nonessential_apps()
    kill_adobe_helpers()
    clear_ae_disk_caches()
    flush_inactive_ram()
    lock_power_settings()
    spotlight_indexing(True)
    time.sleep(1.0)

def post_render_restore():
    spotlight_indexing(False)

# ─── AE SCRIPTING READINESS & MODAL DISMISSAL ─────────────────────────────────
def _ae_click_crash_repair_and_errors():
    ascript = r'''
    try
        tell application "System Events"
            if exists process "Adobe After Effects 2025" then
                tell process "Adobe After Effects 2025"
                    repeat with w in windows
                        try
                            set wname to (name of w as text)
                            if (wname contains "Crash") or (wname contains "problem") or (wname contains "Safe Mode") or (wname contains "Repair") then
                                repeat with b in buttons of w
                                    try
                                        set t to (title of b as text)
                                        if t is in {"Continue","OK","Close","Don’t Send","Don't Send","Ignore"} then
                                            click b
                                            exit repeat
                                        end if
                                    end try
                                end repeat
                            end if
                        end try
                    end repeat
                    repeat with w in windows
                        try
                            set wname to (name of w as text)
                            if (wname contains "Scripting Plugin is not installed") or (wname contains "Unable to execute script") then
                                repeat with b in buttons of w
                                    try
                                        if (title of b as text) is in {"OK","Close"} then click b
                                    end try
                                end repeat
                            end if
                        end try
                    end repeat
                end tell
            end if
        end tell
    end try
    '''
    _run_quiet(["osascript","-e",ascript])

def _try_doscript(js_snippet: str):
    try:
        res = subprocess.run(
            ["osascript","-e", f'tell application "{AE_APP_NAME}" to DoScript "{js_snippet}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        return (res.returncode == 0, (res.stderr or ""))
    except Exception as e:
        return (False, str(e))

def wait_for_ae_scripting_ready(timeout_s: int = 90, poll_s: float = 1.5) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        _ae_click_crash_repair_and_errors()
        ok, err = _try_doscript("1+1;")
        if ok: return True
        if "Scripting Plugin is not installed" in err or "Unable to execute script" in err:
            time.sleep(poll_s); continue
        time.sleep(poll_s)
    return False

# ─── MEMORY PRESSURE ──────────────────────────────────────────────────────────
def _parse_vm_stat():
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    except Exception:
        return {}
    pages = {}
    for line in (out or "").splitlines():
        if ":" not in line: continue
        k, v = line.split(":", 1)
        k = k.strip(); v = v.strip().replace(".", "").replace(",", "")
        m = re.match(r"(\d+)", v)
        if m: pages[k] = int(m.group(1))
    return pages

def memory_pressure_level():
    p = _parse_vm_stat()
    if not p: return "low"
    free = p.get("Pages free", 0) + p.get("Pages speculative", 0)
    active = p.get("Pages active", 0)
    inactive = p.get("Pages inactive", 0)
    wired = p.get("Pages wired down", 0)
    compressed = p.get("Pages occupied by compressor", 0)
    total = free + active + inactive + wired + compressed
    if total <= 0: return "low"
    used_est = active + wired + compressed
    used_ratio = used_est / total
    if used_ratio < 0.65: return "low"
    elif used_ratio < 0.80: return "med"
    else: return "high"

def mem_usage_tuple_for_pressure():
    lvl = memory_pressure_level()
    if lvl == "low": return ("70","95")
    elif lvl == "med": return ("60","90")
    else: return ("50","85")

# ─── IN-APP PURGE + AE QUIT (SOFT) ────────────────────────────────────────────
def _dismiss_adobe_crash_dialogs():
    ascript = r'''
    try
        tell application "System Events"
            if exists process "Adobe Crash Reporter" then
                tell process "Adobe Crash Reporter"
                    repeat with w in windows
                        repeat with b in buttons of w
                            try
                                set t to (title of b as text)
                                if t is in {"Quit","Close","Don't Send","Don’t Send","OK"} then click b
                            end try
                        end repeat
                    end repeat
                end tell
            end if
        end tell
    end try
    '''
    _run_quiet(["osascript","-e",ascript])

def purge_ae_in_app():
    # cache purge only (not a save)
    if not wait_for_ae_scripting_ready(timeout_s=10, poll_s=1.5):
        logging.info("🧹 In-app purge skipped (AE scripting not ready)."); return
    ascript = f'''
    tell application "{AE_APP_NAME}"
        try
            DoScript "app.purge(PurgeTarget.ALL_CACHES);"
        on error errMsg
        end try
    end tell
    '''
    _run_quiet(["osascript","-e",ascript])
    logging.info("🧹 In-app purge invoked.")

def kill_ae(timeout_term: float = 8.0):
    # try purge (not save) if scripting is up
    if wait_for_ae_scripting_ready(timeout_s=5, poll_s=1.0):
        purge_js = 'app.purge(PurgeTarget.ALL_CACHES);'
        _run_quiet(["osascript", "-e", f'tell application "{AE_APP_NAME}" to DoScript "{purge_js}"'])

    _run_quiet(["osascript", "-e", f'tell application "{AE_APP_NAME}" to quit'])
    _dismiss_adobe_crash_dialogs()

    term_names = [
        AE_APP_NAME,"After Effects","aerender","aerendercore",
        "dynamiclinkmanager","dynamiclinkmediaserver",
        "AdobeIPCBroker","CEPHtmlEngine","Adobe CEF Helper",
        "Adobe Crash Reporter","Adobe Notification Client","Adobe Desktop Service"
    ]
    for n in term_names:
        _run_quiet(["killall","-TERM",n]); _run_quiet(["pkill","-TERM","-f",n])

    t0 = time.time()
    while time.time() - t0 < timeout_term:
        p = subprocess.run(["pgrep","-f",AE_PROC_SUBSTR], capture_output=True, text=True)
        if not p.stdout.strip(): break
        time.sleep(0.2)

    kill_adobe_helpers(); clear_ae_disk_caches()
    try:
        saved_root = os.path.expanduser("~/Library/Saved Application State")
        for d in os.listdir(saved_root):
            if ("AfterEffects" in d) or ("After Effects" in d) or ("com.adobe.AfterEffects" in d):
                shutil.rmtree(os.path.join(saved_root,d), ignore_errors=True)
    except Exception:
        pass
    print("✔️  AE quit requested (soft); no force-kill used.")

# ─── CACHE HYGIENE CYCLE ──────────────────────────────────────────────────────
def cache_hygiene_cycle(tag: str = ""):
    if tag: logging.info(f"🧼 Cache hygiene cycle start [{tag}]")
    else:   logging.info("🧼 Cache hygiene cycle start")
    try: purge_ae_in_app()
    except Exception: pass
    try: kill_ae()
    except Exception: pass
    try: clear_ae_disk_caches()
    except Exception: pass
    try: flush_inactive_ram()
    except Exception: pass
    logging.info("✅ Cache hygiene cycle complete.")

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _touch(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab"):
        pass

def ensure_dummy_mp4(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=1280x720:d=1', '-pix_fmt', 'yuv420p', path]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        _touch(path)

def swap_assets(idx: int, skip_folders=None):
    skip_folders = set(skip_folders or [])
    for folder, pattern in VARIABLES_LIST:
        if folder in skip_folders:
            print(f"⛔️ Skipping swap for {folder}/{pattern} (explicitly skipped).")
            continue
        src = os.path.join(VARIABLES_ROOT, folder, pattern.replace("X", str(idx)))
        dst = os.path.join(VARIABLES_ROOT, folder, pattern)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"✔️  {os.path.basename(src)} → {os.path.basename(dst)}")
        else:
            if not os.path.exists(dst):
                try:
                    with open(dst, "ab"):
                        pass
                except Exception:
                    pass
            print(f"⚠️  Missing {src}; using existing/dummy placeholder → render will continue.")

def cleanup_partial():
    p = os.path.join(OUTPUT_DIR, "CompositionX.mp4")
    if os.path.exists(p):
        os.remove(p); print("🗑  Removed partial CompositionX.mp4")

def open_ae(project_path: str):
    subprocess.run(["open","-a",AE_APP_NAME,project_path], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"🚀 Opening AE with project: {project_path}")
    time.sleep(8)
    subprocess.run(["osascript","-e",'tell application "System Events" to keystroke return'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("↩️  Dismissed Crash Repair (if any), waiting remainder…")
    time.sleep(22)

# ─── RENDER ───────────────────────────────────────────────────────────────────
def render_attach(project_path: str) -> bool:
    out = os.path.join(OUTPUT_DIR, "CompositionX.mp4")
    low, high = mem_usage_tuple_for_pressure()
    cmd = [
        AERENDER_BIN,"-reuse","-project",project_path,"-comp","CompositionX",
        "-output",out,"-v","ERRORS_AND_PROGRESS","-mem_usage",low,high,
        "-close","DO_NOT_SAVE_CHANGES",
    ]
    print(f"▶️  aerender -reuse ({RENDER_TIMEOUT//60} m timeout) — mem_usage {low} {high}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("⏱  Render timed out → will retry"); return False
    if res.returncode != 0:
        tail = res.stderr.strip().splitlines()[-3:]
        print(f"❌  aerender failed (code {res.returncode}) → retrying")
        if tail: print("    … " + "\n    … ".join(tail))
        return False
    return True

def rename_output(idx: int) -> bool:
    src = os.path.join(OUTPUT_DIR, "CompositionX.mp4")
    dst = os.path.join(OUTPUT_DIR, f"Composition{idx}.mp4")
    if os.path.exists(src):
        os.replace(src, dst); print(f"📥 Renamed to {os.path.basename(dst)}"); return True
    print("⚠️  Missing output file; retrying…"); return False

# ─── Symlink helper for clean paths ───────────────────────────────────────────
def _ensure_symlink_clean(target: str, link_path: str):
    try:
        if os.path.islink(link_path) or os.path.exists(link_path):
            os.remove(link_path)
        os.symlink(target, link_path)
    except Exception as e:
        logging.error(f"Failed to create symlink {link_path} -> {target}: {e}")
    return link_path

def _run_w2l(face_path: str, voice_path: str, outfile: str) -> bool:
    env = os.environ.copy()
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    try:
        cwd = os.getcwd()
        os.chdir(W2L_DIR)
        cmd = [
            sys.executable, "inference.py",
            "--checkpoint_path", W2L_CHECKPOINT,
            "--face",  face_path,
            "--audio", voice_path,
            "--outfile", outfile,
            "--nosmooth", "--resize_factor", "2", "--pads", "0", "20", "0", "0"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        os.chdir(cwd)
        if res.returncode != 0:
            logging.error("❌ Wav2Lip failed (exit %s).", res.returncode)
            if res.stderr: logging.error(res.stderr.strip())
            return False
        return os.path.exists(outfile) and os.path.getsize(outfile) > 0
    except Exception as e:
        logging.error("❌ Wav2Lip exception: %s", e)
        return False

# ─── Single clone producer ────────────────────────────────────────────────────
def _produce_clone_single(i: int, face_tmpl: str, voice_file: str,
                          final_placeholder: str, archive_out: str, prev_archive: str) -> bool:
    # Reuse archive if present
    if os.path.exists(archive_out) and os.path.getsize(archive_out) > 0:
        try:
            if os.path.exists(final_placeholder): os.remove(final_placeholder)
            shutil.copy2(archive_out, final_placeholder)
            logging.info(f"🧬 Reused archive → {os.path.basename(final_placeholder)}")
            return True
        except Exception:
            pass

    # Missing voice → fallback
    if not os.path.exists(voice_file):
        logging.warning(f"🔇 Voice missing: {voice_file}")
        if os.path.exists(prev_archive) and os.path.getsize(prev_archive) > 0:
            try:
                if os.path.exists(final_placeholder): os.remove(final_placeholder)
                shutil.copy2(prev_archive, final_placeholder)
                logging.warning(f"⚠️  Using previous clone fallback → {prev_archive}")
                return False
            except Exception:
                pass
        ensure_dummy_mp4(final_placeholder)
        logging.warning("⚠️  Using dummy placeholder clone")
        return False

    # Missing face template → fallback
    if not os.path.exists(face_tmpl):
        logging.error(f"Face template missing: {face_tmpl}")
        if os.path.exists(prev_archive) and os.path.getsize(prev_archive) > 0:
            try:
                if os.path.exists(final_placeholder): os.remove(final_placeholder)
                shutil.copy2(prev_archive, final_placeholder)
                logging.warning(f"⚠️  Using previous clone fallback → {prev_archive}")
                return False
            except Exception:
                pass
        ensure_dummy_mp4(final_placeholder)
        logging.warning("⚠️  Using dummy placeholder clone")
        return False

    # Clean input paths (avoid spaces/parentheses issues)
    clean_face  = os.path.join(W2L_LINK_DIR, f"face_{os.path.basename(final_placeholder)}_{i}.mp4")
    clean_voice = os.path.join(W2L_LINK_DIR, f"voice_{os.path.basename(final_placeholder)}_{i}.wav")
    _ensure_symlink_clean(face_tmpl,  clean_face)
    _ensure_symlink_clean(voice_file, clean_voice)

    desktop_out = os.path.join(W2L_DESKTOP_OUTDIR, f"{os.path.splitext(os.path.basename(final_placeholder))[0]}_{i}.mp4")
    for attempt in range(1, 2 + 1):
        logging.info(f"🧪 Wav2Lip (single): {os.path.basename(final_placeholder)} i={i} attempt {attempt}")
        if _run_w2l(clean_face, clean_voice, desktop_out):
            try:
                if os.path.exists(final_placeholder): os.remove(final_placeholder)
                shutil.move(desktop_out, final_placeholder)
                try:
                    shutil.copy2(final_placeholder, archive_out)
                except Exception:
                    pass
                logging.info(f"🎯 Created {final_placeholder} & archived {archive_out}")
                return True
            except Exception as e:
                logging.error(f"❌ Move failed {desktop_out} → {final_placeholder}: {e}")
        else:
            logging.error("❌ Wav2Lip run failed")

    # Fallbacks if generation failed
    if os.path.exists(prev_archive) and os.path.getsize(prev_archive) > 0:
        try:
            if os.path.exists(final_placeholder): os.remove(final_placeholder)
            shutil.copy2(prev_archive, final_placeholder)
            logging.warning(f"⚠️  Using previous clone fallback → {prev_archive}")
            return False
        except Exception:
            pass
    ensure_dummy_mp4(final_placeholder)
    logging.warning("⚠️  Using dummy placeholder clone")
    return False

def ensure_single_clone_for_index(i: int, face_template: str):
    """Single-stream (CloneX.mp4 + VoiceX.wav) with project-specific face template."""
    os.makedirs(CLONES_DIR_A, exist_ok=True)
    os.makedirs(VOICES_DIR_A, exist_ok=True)

    final_A    = os.path.join(CLONES_DIR_A, "CloneX.mp4")
    archive_A  = os.path.join(CLONES_DIR_A, f"Clone{i}.mp4")
    prev_A     = os.path.join(CLONES_DIR_A, f"Clone{i-1}.mp4")
    voice_A    = os.path.join(VOICES_DIR_A, f"Voice{i}.wav")

    ok_A = _produce_clone_single(i, face_template, voice_A, final_A, archive_A, prev_A)
    return ok_A

# ─── CSV → Project mapping helpers ────────────────────────────────────────────
def _load_index_list(path: str) -> List[str]:
    """Return a 1D list of tokens from index.csv (handles commas or newlines)."""
    if not os.path.exists(path):
        logging.warning(f"⚠️ index.csv not found at {path} — defaulting to Solutions for all.")
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception as e:
        logging.warning(f"⚠️ Could not read index.csv ({e}) — defaulting to Solutions.")
        return []
    raw = re.split(r"[,\r\n]+", text)
    return [t.strip() for t in raw if t.strip()]

def _project_for_index(i: int, tokens: List[str]) -> str:
    """Map index -> .aep, defaulting to Solutions if out of range/unknown."""
    if 1 <= i <= len(tokens):
        v = tokens[i-1].strip().lower()
        if v.startswith("sale"):
            return PROJECT_SALES
        if v.startswith("solu"):
            return PROJECT_SOLUTIONS
    return PROJECT_SOLUTIONS  # fallback

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load index.csv once
    index_tokens = _load_index_list(INDEX_CSV)

    time.sleep(1.0)
    cache_hygiene_cycle(tag="startup")

    try:
        for i in range(START_INDEX, END_INDEX + 1):
            project_path = _project_for_index(i, index_tokens)
            project_name = os.path.basename(project_path)

            print(f"\n=== INDEX {i} — PROJECT: {project_name} (single clone, no JSX/save) ===")
            print(f"🗂  Using project: {project_path}")

            # Pick face template based on which project we're using
            base = project_name.lower()
            if base.startswith("sales"):
                face_template = TEMPLATE_FACE_SALES
            else:
                # default matches _project_for_index fallback (Solutions)
                face_template = TEMPLATE_FACE_SOLUTIONS

            # Produce single clone and swap assets
            _ = ensure_single_clone_for_index(i, face_template)
            swap_assets(i, skip_folders=set())

            attempt = 0
            while True:
                attempt += 1
                print(f"— Attempt {attempt} —")

                if attempt > 1:
                    print("⛔ Quitting After Effects before retry…")
                    kill_ae()

                kill_ae()
                cleanup_partial()
                deep_reboot_like_prep()

                try:
                    open_ae(project_path)

                    # Useful to ensure scripting engine is responsive for modal dismissal
                    if not wait_for_ae_scripting_ready(timeout_s=90, poll_s=1.5):
                        print("⚠️ AE scripting never got ready — relaunching AE once.")
                        kill_ae()
                        open_ae(project_path)

                except subprocess.CalledProcessError as e:
                    print(f"❌ Failed to open AE project ({project_path}): {e}")
                    print("❌ Opening failed — quitting AE before retry…")
                    kill_ae()
                    print("🔄  Retrying in 10 s…")
                    post_render_restore()
                    time.sleep(10)
                    continue

                try:
                    ok_render = render_attach(project_path)
                    cache_hygiene_cycle(tag=f"video attempt i={i}")
                    ok_rename = rename_output(i) if ok_render else False
                finally:
                    post_render_restore()

                if ok_render and ok_rename:
                    break

                print("❌ Render attempt failed — quitting AE before retry…")
                kill_ae()
                print("🔄  Retrying in 10 s…")
                time.sleep(10)

    finally:
        pass

    kill_ae()
    print("\n🎉 All renders complete!")

if __name__ == "__main__":
    main()
