"""
# Automated Video Distribution & Engagement Handoff
*(Video Platform Integration – Loom shown as reference implementation)*

## BUSINESS WORKFLOW (END-TO-END)

This script represents the **distribution layer** of a scalable GTM personalization system.

It is triggered once personalized video assets have been rendered upstream  
(e.g. by a batch video rendering / orchestration pipeline).

## High-level flow

1. Detect newly rendered personalized video assets on disk  
2. Upload each asset programmatically to a video hosting platform  
   - The upload filename is used as the initial video title  
   - This avoids brittle UI-based renaming and ensures deterministic titles  
3. Retrieve distribution artifacts:
   - Public video URL  
   - Generated GIF thumbnail (used in outbound messaging)  
4. Persist distribution metadata into structured storage (CSV)  
5. Send a webhook payload to downstream automation (Make / Zapier)  
   - Triggers email, CRM, sequencing, or Slack workflows  
6. Safely advance processing state and clean up local artifacts  

## WHY THIS EXISTS

At scale, GTM teams break down not at content creation, but at execution.

This script removes all manual steps between personalized media production
and outbound delivery:

- No manual uploads  
- No manual title edits  
- No manual copying of URLs or thumbnails  
- No manual triggering of campaigns  

The result is a fully automated handoff from personalized media production  
directly into live GTM execution systems.

## IMPORTANT NOTES

- This script is intentionally defensive and session-hardened  
- UI automation is minimized wherever possible  
- While Loom is used here as a reference platform, the pattern generalizes  
  to any video hosting or distribution system with authenticated upload  
- Client-specific paths, credentials, and identifiers have been anonymized  
  for portfolio and demonstration purposes
"""
import os
import re
import time
import math
import json
import platform
import pandas as pd
import traceback
from typing import Optional
import signal
import warnings
import shutil
import tempfile

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    WebDriverException,
    InvalidSessionIdException,
)
from selenium.webdriver import ActionChains

from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# (Optional) Quiet urllib3 LibreSSL warning on macOS
try:
    import urllib3
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

# ──────────────── CONFIG (env-overridable, redacted) ────────────────
# NOTE:
# All defaults below are intentionally generic placeholders.
# Real values are expected to be provided via environment variables
# in production deployments.

VIDEO_FOLDER_PATH = os.getenv(
    'VIDEO_FOLDER_PATH',
    '/path/to/rendered/videos'  # e.g. /mnt/render/output
)

CSV_FILE_PATH = os.getenv(
    'CSV_FILE_PATH',
    '/path/to/metadata/input.csv'
)

TRACKING_FILE = os.getenv(
    'TRACKING_FILE',
    'last_processed_index.txt'
)

# Headless after login (env overrides)
RUN_HEADLESS_AFTER_LOGIN = False
_env_headless = os.getenv('HEADLESS')
if _env_headless is not None:
    try:
        RUN_HEADLESS_AFTER_LOGIN = (int(_env_headless) != 0)
        print(f"🔧 HEADLESS env override → RUN_HEADLESS_AFTER_LOGIN={RUN_HEADLESS_AFTER_LOGIN}")
    except ValueError:
        pass

# ───────── Workspace / Platform URLs (redacted) ─────────
LOOM_ROOT_URL = os.getenv(
    'LOOM_ROOT_URL',
    'https://www.video-platform.example'
)

WORKSPACE_URL = os.getenv(
    'WORKSPACE_URL',
    'https://www.video-platform.example/workspaces/default'
)

# ───────── Title selectors (UI-dependent, non-sensitive) ─────────
# These are kept as-is since they represent technical behavior,
# not confidential business data.

TITLE_STATIC_SEL = os.getenv(
    'TITLE_STATIC_SEL',
    'div.static-title_staticTitleEditableContainer, '
    'div.common_textContainer, '
    'span[role="heading"], '
    'h1'
)

TITLE_INPUT_SEL = os.getenv(
    'TITLE_INPUT_SEL',
    'input[type="text"], [contenteditable="true"]'
)

NEW_TITLE_XPATH: Optional[str] = os.getenv('NEW_TITLE_XPATH', '')

TITLE_STATIC_XPATH = os.getenv(
    'TITLE_STATIC_XPATH',
    '//h1 | //span[@role="heading"]'
)

TITLE_INPUT_XPATH = os.getenv(
    'TITLE_INPUT_XPATH',
    '//input[@type="text"] | //*[@contenteditable="true"]'
)

# ───────── Webhook / Automation Endpoint (redacted) ─────────
# In production this points to Make / Zapier / internal automation.
ZAPIER_WEBHOOK_URL = os.getenv(
    'MAKE_WEBHOOK_URL',
    'https://automation-endpoint.example/webhook')

# Toggle this manually: True = send webhook, False = skip webhook
WEBHOOK_ENABLED = True  # 👈 set to False to turn OFF

# Session limits
COOKIES_FILE = os.getenv('COOKIES_FILE', 'loom_cookies.json')
MAX_VIDEOS_PER_SESSION = int(os.getenv('MAX_VIDEOS_PER_SESSION', '100'))
MAX_SESSION_MINUTES = int(os.getenv('MAX_SESSION_MINUTES', '240'))
_session_start_ts = time.time()
_session_video_count = 0

# OS keys
IS_MAC = (platform.system() == 'Darwin')
CMD_KEY = Keys.COMMAND if IS_MAC else Keys.CONTROL

# ───────── GIF RETRY TUNING ─────────
GIF_MAX_ATTEMPTS = int(os.getenv('GIF_MAX_ATTEMPTS', '6'))
GIF_WAIT_BETWEEN_ATTEMPTS = float(os.getenv('GIF_WAIT_BETWEEN_ATTEMPTS', '8'))
GIF_TIMEOUT = float(os.getenv('GIF_TIMEOUT', '45'))
GIF_POLL = float(os.getenv('GIF_POLL', '0.25'))

# Embed retry tuning
EMBED_RETRIES = int(os.getenv('EMBED_RETRIES', '6'))
EMBED_DELAY_BETWEEN_RETRIES = float(os.getenv('EMBED_DELAY_BETWEEN_RETRIES', '2'))

# File readiness (optional ahead safety: require Composition(N+1).mp4)
AHEAD_SAFETY = bool(int(os.getenv('AHEAD_SAFETY', '0')))

# Upload locate tries
LOCATE_MAX_ATTEMPTS = int(os.getenv('LOCATE_MAX_ATTEMPTS', '3'))

# Boot/hardening
MAX_BOOT_TRIES = int(os.getenv('MAX_BOOT_TRIES', '4'))
DRIVER_BOOT_RETRY_DELAY = float(os.getenv('DRIVER_BOOT_RETRY_DELAY', '2.5'))
RECONNECT_BACKOFF = [1, 2, 4, 8]

# Skip old "Copy of " strip by default (since we title via filename now)
SKIP_COPY_OF_STRIP = bool(int(os.getenv('SKIP_COPY_OF_STRIP', '1')))

# ───────── Utilities ─────────

def _is_connectivity_blowup(exc: Exception) -> bool:
    msg = (f"{exc}".lower())
    return any(s in msg for s in [
        'connection refused',
        'chrome not reachable',
        'invalid session id',
        'disconnected: not connected to devtools',
        'cannot connect to the service',
        'httpconnectionpool',
    ])

def _sanitize_title(name: str) -> str:
    name = (name or '').strip()
    name = re.sub(r"\s+", " ", name)
    return re.sub(r"[:/\\<>|?*\u0000-\u001F]", "-", name)[:200]

def _sanitize_title_filename(name: str) -> str:
    # Filename-safe version; also remove quotes
    name = (name or '').strip()
    name = re.sub(r"\s+", " ", name)
    safe = re.sub(r'[:/\\<>|?*\u0000-\u001F"]', "-", name)
    return safe[:200]

def make_temp_titled_copy(src_path: str, desired_title: str) -> str:
    base_dir = tempfile.gettempdir()
    fname = _sanitize_title_filename(desired_title) + ".mp4"
    tmp_path = os.path.join(base_dir, fname)
    shutil.copy2(src_path, tmp_path)
    print(f"🗂️  Created temp title-named copy: {tmp_path}")
    return tmp_path

# ───────── Zapier/Webhook helper ─────────

def send_zapier_webhook(payload: dict, retries=(0, 2, 5)) -> bool:
    if not WEBHOOK_ENABLED:
        print("⚙️ Webhook disabled (WEBHOOK_ENABLED=0); skipping POST.")
        # Treat as success so the main loop can advance and delete files as usual
        return True

    clean = {}
    for k, v in payload.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            clean[k] = None
        else:
            clean[k] = v
    try:
        out = json.dumps(clean, default=str)
    except Exception as e:
        print(f"⚠️  Failed to serialize for logging: {e}")
        out = str(clean)
    print("➡️  Sending payload to Make webhook:")
    print(out)

    for delay in retries:
        if delay:
            time.sleep(delay)
        try:
            resp = requests.post(ZAPIER_WEBHOOK_URL, json=clean, timeout=15)
            print(f"⬅️  Webhook status: {resp.status_code}")
            print(f"⬅️  Webhook body:   {resp.text}")
            if resp.ok:
                print(f"✅  Webhook sent successfully ({resp.status_code}).")
                return True
        except requests.RequestException as e:
            print(f"⚠️  Webhook request error: {e}")
    print("⚠️  Webhook failed after retries.")
    return False

# ───────── Cookies ─────────

def save_cookies_to_disk(cookies, path=COOKIES_FILE):
    try:
        with open(path, 'w') as f:
            json.dump(cookies, f)
        print(f"💾 Saved {len(cookies)} cookies to {path}")
    except Exception as e:
        print(f"⚠️ Failed to save cookies: {e}")

def load_cookies_from_disk(path=COOKIES_FILE):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            cookies = json.load(f)
        print(f"📥 Loaded {len(cookies)} cookies from {path}")
        return cookies
    except Exception as e:
        print(f"⚠️ Failed to load cookies: {e}")
        return []

# ───────── Driver ─────────

def init_driver(headless=True):
    last_exc = None
    for attempt in range(1, MAX_BOOT_TRIES + 1):
        try:
            opts = Options()
            opts.add_argument('--disable-popup-blocking')
            opts.add_argument('--autoplay-policy=no-user-gesture-required')
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.add_argument('--window-size=1920,1080')
            opts.add_argument('--remote-debugging-pipe')
            if headless:
                opts.add_argument('--headless=new')
            service = Service(ChromeDriverManager().install())
            d = webdriver.Chrome(service=service, options=opts)
            d.set_page_load_timeout(60)
            d.implicitly_wait(0)
            time.sleep(0.6)
            return d
        except Exception as e:
            last_exc = e
            print(f"⚠️  Chrome boot attempt {attempt}/{MAX_BOOT_TRIES} failed: {e}")
            time.sleep(DRIVER_BOOT_RETRY_DELAY)
    raise RuntimeError(f"Could not start Chrome after {MAX_BOOT_TRIES} tries") from last_exc

def apply_cookies(driver, cookies, base_url=LOOM_ROOT_URL) -> bool:
    if not cookies:
        return True
    try:
        driver.get(base_url)
    except Exception as e:
        print(f"⚠️ driver.get({base_url}) failed in apply_cookies: {e}")
        return False
    for c in cookies:
        c = {k: v for k, v in c.items() if k in {
            'name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'expiry', 'sameSite'
        }}
        if 'expiry' in c and not isinstance(c['expiry'], (int, float)):
            try:
                c['expiry'] = int(c['expiry'])
            except Exception:
                c.pop('expiry', None)
        try:
            driver.add_cookie(c)
        except Exception as e:
            if _is_connectivity_blowup(e):
                print(f"🛑 Connectivity issue while add_cookie: {e}")
                return False
            pass
    return True

def is_driver_alive(driver) -> bool:
    try:
        _ = driver.current_window_handle
        return True
    except Exception:
        return False

def enter_workspace(driver, timeout=30):
    try:
        print(f"Navigating to workspace: {WORKSPACE_URL}")
        driver.get(WORKSPACE_URL)
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Add video')]"))
            )
            print("Workspace ready (found 'Add video').")
            return
        except TimeoutException:
            pass
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, "(//main//ul//li//article//a)[1]"))
            )
            print("Workspace ready (found at least one video card).")
            return
        except TimeoutException:
            raise TimeoutException('Workspace grid did not appear in time.')
    except Exception as e:
        print(f"Error while entering Loom workspace via URL: {e}")
        raise

def _build_driver_with_cookies(*, headless: bool) -> webdriver.Chrome:
    mode_label = 'headless' if headless else 'VISIBLE'
    backoffs = RECONNECT_BACKOFF[:]
    while True:
        print(f"🧩 Booting {mode_label} Chrome with cookies…")
        d = init_driver(headless=headless)

        try:
            if not d.window_handles:
                d.execute_script("window.open('about:blank','_blank');")
                WebDriverWait(d, 5).until(lambda drv: len(drv.window_handles) > 0)
                d.switch_to.window(d.window_handles[0])
        except Exception as e:
            print(f"⚠️ Initial window bootstrap failed: {e}")

        ok = apply_cookies(d, load_cookies_from_disk())
        if not ok:
            try:
                d.quit()
            except Exception:
                pass
            if backoffs:
                delay = backoffs.pop(0)
                print(f"♻️ Retrying driver+cookies in {delay}s…")
                time.sleep(delay)
                continue
        else:
            try:
                enter_workspace(d, timeout=30)
                return d
            except Exception as e:
                print(f"⚠️ enter_workspace failed after boot: {e}")
        try:
            d.quit()
        except Exception:
            pass
        if backoffs:
            delay = backoffs.pop(0)
            print(f"♻️ Retrying full boot in {delay}s…")
            time.sleep(delay)
            continue
        raise RuntimeError('Failed to build driver and enter workspace after multiple attempts.')

def revive_driver_if_needed(driver_ref):
    global _session_start_ts, _session_video_count
    need_rotate = (
        (_session_video_count >= MAX_VIDEOS_PER_SESSION) or
        ((time.time() - _session_start_ts) > MAX_SESSION_MINUTES * 60)
    )
    if driver_ref.get('driver') is None or (not is_driver_alive(driver_ref['driver'])) or need_rotate:
        if need_rotate:
            print('♻️ Rotating browser session (time/video threshold).')
        else:
            print('🧯 Detected dead/empty WebDriver session. Rebuilding…')
        try:
            if driver_ref.get('driver') is not None:
                driver_ref['driver'].quit()
        except Exception:
            pass
        d = _build_driver_with_cookies(headless=RUN_HEADLESS_AFTER_LOGIN)
        driver_ref['driver'] = d
        _session_start_ts = time.time()
        _session_video_count = 0
        print(f"✅ New driver ready (headless={RUN_HEADLESS_AFTER_LOGIN}).")
    return True

# ───────── Overlay / modal utilities ─────────

def wait_until_invisible(driver, by, value, timeout=15):
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((by, value))
        )
        return True
    except TimeoutException:
        return False

def clear_uppy_overlay(driver):
    try:
        close_btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
        )
        try:
            close_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", close_btn)
        time.sleep(0.5)
    except TimeoutException:
        pass
    try:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        time.sleep(0.25)
    except Exception:
        pass
    if wait_until_invisible(driver, By.CSS_SELECTOR, "div.uppy-Dashboard-overlay", timeout=5):
        print('Uppy overlay disappeared.')
        return
    try:
        driver.execute_script("""
            const els = document.querySelectorAll('div.uppy-Dashboard-overlay');
            els.forEach(el => el.remove());
        """)
        print('Force-removed Uppy overlay via JS.')
    except Exception:
        pass

# ───────── Page utilities ─────────

def on_video_page(driver) -> bool:
    try:
        url = (driver.current_url or '').lower()
        return any(seg in url for seg in ['/share/', '/v/', '/video/', '/videos/'])
    except Exception:
        return False

def refresh_if_video(driver, pause=2.0):
    if on_video_page(driver):
        print('🔄 Refreshing video page to advance processing…')
        try:
            driver.refresh()
            time.sleep(pause)
        except Exception as e:
            print(f'Refresh failed (ignored): {e}')
    else:
        print('⛔ Not refreshing (not on a video page).')

# ───────── Upload / card handling ─────────

def wait_for_upload_to_complete(driver, initial_delay=5, retry_interval=1, max_wait=300):
    """
    Wait for Loom upload to complete.

    initial_delay: wait before we start checking
    retry_interval: how often we poll UI
    max_wait: total time allowed (seconds) before we give up → here: 5 minutes
    """
    print(f"Waiting for initial upload delay of {initial_delay} second(s)...")
    time.sleep(initial_delay)
    elapsed_time = 0

    XPATH_TITLE = "//div[contains(@class,'uppy-DashboardContent-title')]"
    CSS_TITLE   = "div.uppy-DashboardContent-title, [class*='DashboardContent-title']"
    XPATH_ARIA  = "//*[@aria-live='polite' or @aria-live='assertive']"

    while elapsed_time < max_wait:
        try:
            # Title nodes
            for el in driver.find_elements(By.XPATH, XPATH_TITLE):
                txt = (el.text or '').strip().lower()
                if 'upload complete' in txt or 'complete' in txt:
                    print("Upload complete detected via title.")
                    return True

            for el in driver.find_elements(By.CSS_SELECTOR, CSS_TITLE):
                txt = (el.text or '').strip().lower()
                if 'upload complete' in txt or 'complete' in txt:
                    print("Upload complete detected via CSS title.")
                    return True

            for el in driver.find_elements(By.XPATH, XPATH_ARIA):
                txt = (el.text or '').strip().lower()
                if 'upload complete' in txt or 'complete' in txt:
                    print("Upload complete detected via aria-live.")
                    return True

            overlay_gone = wait_until_invisible(driver, By.CSS_SELECTOR, 'div.uppy-Dashboard-overlay', timeout=1)
            if overlay_gone:
                print('Uppy overlay disappeared; treating as upload complete.')
                return True

            print(f"Upload not complete after {elapsed_time}s, retrying in {retry_interval}s…")
            time.sleep(retry_interval)
            elapsed_time += retry_interval

        except Exception as e:
            print(f"Non-fatal wait error: {e}")
            time.sleep(retry_interval)
            elapsed_time += retry_interval

    print('Upload did not complete within the maximum allowed time (5 minutes). Returning to main loop.')
    return False

def click_add_video_button(driver):
    add_video_button_xpath = "//button[contains(., 'Add video')]"
    WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, add_video_button_xpath))
    ).click()
    print("'Add video' button clicked successfully.")

def click_upload_video_option(driver):
    upload_video_option_xpath = "//span[contains(., 'Upload a video')]/.."
    WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, upload_video_option_xpath))
    ).click()
    print("'Upload a video' option clicked successfully.")

def upload_files(driver, file_path):
    file_input = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))
    )
    file_input.send_keys(file_path)
    print(f'File path sent to file input successfully: {file_path}')

def click_upload_files_button(driver):
    labels = [
        "//button[contains(., 'Upload 1 file')]",
        "//button[contains(., 'Upload file')]",
        "//button[@type='submit' and not(@disabled)]",
        "//button[normalize-space()='Upload']",
    ]
    for xpath in labels:
        try:
            button_element = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            try:
                button_element.click()
                print(f"Upload button clicked via {xpath}.")
                return True
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                print(f"Normal click failed: {e}. Trying JS click…")
                try:
                    driver.execute_script('arguments[0].click();', button_element)
                    print('Upload button clicked via JS.')
                    return True
                except Exception:
                    pass
        except TimeoutException:
            continue
    print('Failed to click the Upload button via known XPaths; sending ENTER to active element.')
    try:
        driver.switch_to.active_element.send_keys(Keys.ENTER)
        return True
    except Exception:
        return False

def attempt_upload_process(driver, file_path):
    try:
        click_add_video_button(driver)
        click_upload_video_option(driver)
        upload_files(driver, file_path)
        time.sleep(2)
        click_upload_files_button(driver)

        # Safety ENTER after clicking "Upload 1 file"
        try:
            time.sleep(1.0)
            driver.switch_to.active_element.send_keys(Keys.ENTER)
            print("↩️ Sent safety ENTER 1s after clicking 'Upload 1 file'.")
        except Exception as e:
            print(f"⚠️ Could not send safety ENTER: {e}")

        existing_handles = set(driver.window_handles)
        if not wait_for_upload_to_complete(driver):
            return False

        time.sleep(0.5)
        new_handles = set(driver.window_handles)
        difference = new_handles - existing_handles
        if difference:
            for handle in list(difference):
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                except Exception:
                    pass
            try:
                main_tab = list(existing_handles)[0]
                driver.switch_to.window(main_tab)
            except Exception:
                pass
        try:
            close_popup_button = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
            )
            try:
                close_popup_button.click()
            except Exception:
                driver.execute_script('arguments[0].click();', close_popup_button)
            print("Closed the 'Upload complete' popup.")
        except TimeoutException:
            print('No "Upload complete" popup to close or it was already closed.')

        clear_uppy_overlay(driver)
        close_extra_tabs(driver)
        enter_workspace(driver, timeout=30)
        return True

    except (InvalidSessionIdException, WebDriverException) as e:
        if _is_connectivity_blowup(e) or isinstance(e, InvalidSessionIdException):
            print("🧨 Upload step lost the browser session—will force rebuild.")
            try:
                driver.quit()
            except Exception:
                pass
            return False
        print(f"Error during upload process: {e}")
        return False

    except Exception as e:
        print(f"Error during upload process: {e}")
        try:
            enter_workspace(driver, timeout=30)
        except Exception:
            pass
        time.sleep(5)
        return False

# ───────── Grid helpers ─────────

def _wait_for_grid_stable(driver, checks=3, pause=0.2, timeout=6):
    end = time.time() + timeout
    last = None
    stable = 0
    while time.time() < end:
        try:
            cards = driver.find_elements(By.XPATH, "//main//ul//li//article//a[@aria-label]")
            cnt = len(cards)
        except Exception:
            cnt = -1
        if cnt == last and cnt >= 0:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
            last = cnt
        time.sleep(pause)
    return False

def _find_card_href_by_title(driver, title_exact: str) -> Optional[str]:
    # 1) Exact aria-label
    try:
        el = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, f"//a[@aria-label={json.dumps(f'Open video: {title_exact}')} ]"))
        )
        href = el.get_attribute('href')
        if href:
            return href
    except TimeoutException:
        pass

    # 2) Partial match
    try:
        els = driver.find_elements(By.XPATH, "//a[@aria-label]")
        for e in els:
            label = (e.get_attribute('aria-label') or '').strip()
            if title_exact.lower() in label.lower():
                href = e.get_attribute('href')
                if href:
                    return href
    except Exception:
        pass

    # 3) Fallback to first card anchor
    try:
        e = WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.XPATH, "(//main//ul//li//article//a)[1]"))
        )
        href = e.get_attribute('href')
        if href:
            return href
    except TimeoutException:
        pass

    return None

def locate_and_click_uploaded_video(driver, video_name, retry_interval=3, max_attempts=LOCATE_MAX_ATTEMPTS):
    print(f"Attempting to locate the uploaded video with the name: {video_name}…")
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[Locate attempt {attempt}/{max_attempts}] Re-entering workspace…")
            enter_workspace(driver, timeout=30)
            _wait_for_grid_stable(driver, checks=3, pause=0.15, timeout=4)

            href = _find_card_href_by_title(driver, video_name)
            if not href:
                time.sleep(0.4)
                _wait_for_grid_stable(driver, checks=2, pause=0.12, timeout=2)
                href = _find_card_href_by_title(driver, video_name)

            if not href:
                raise TimeoutException("Could not find a card href for the uploaded video.")

            print(f"Video HREF: {href}")
            try:
                driver.get(href)
            except WebDriverException as e:
                print(f"Direct navigation failed ({e}); trying JS window.location…")
                driver.execute_script("window.location = arguments[0];", href)

            WebDriverWait(driver, 10).until(
                lambda d: any(seg in (d.current_url or '') for seg in ['/share/', '/v/', '/video/', '/videos/'])
            )
            print('Navigated to the video in the same tab successfully.')
            return True

        except (TimeoutException, NoSuchElementException, StaleElementReferenceException, WebDriverException) as e:
            print(f"Error locating/opening video (Attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                print(f"Retrying in {retry_interval} seconds…")
                time.sleep(retry_interval)

    print(f"Failed to locate the uploaded video '{video_name}' after {max_attempts} attempts.")
    return False

# ───────── Share / Embed (GIF) helpers ─────────

def safe_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
        element.click()
    except ElementClickInterceptedException:
        driver.execute_script('arguments[0].click();', element)

def click_share_button(driver):
    try:
        share_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='share-modal-button']"))
        )
        safe_click(driver, share_button)
        print('Clicked the Share button successfully.')
        time.sleep(1)
        return True
    except (TimeoutException, StaleElementReferenceException) as e:
        print(f"Error clicking Share button: {e}")
        return False

def click_embed_button(driver, retries=EMBED_RETRIES, delay_between_retries=EMBED_DELAY_BETWEEN_RETRIES):
    attempt = 0
    while attempt < retries:
        try:
            print(f"Waiting for the Embed button to become clickable (Attempt {attempt+1})")
            embed_btn = WebDriverWait(driver, 12).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Embed')]"))
            )
            safe_click(driver, embed_btn)
            print('Clicked the Embed button successfully.')
            time.sleep(1)
            return True
        except (TimeoutException, StaleElementReferenceException) as e:
            print(f"Error clicking Embed button (Attempt {attempt+1}): {e}")
            attempt += 1
            if attempt < retries:
                if on_video_page(driver):
                    refresh_if_video(driver, pause=1.2)
                try:
                    try:
                        close_btn = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
                        )
                        safe_click(driver, close_btn)
                        time.sleep(0.3)
                    except TimeoutException:
                        pass
                    click_share_button(driver)
                except Exception:
                    pass
                time.sleep(delay_between_retries)
    print(f"Failed to click Embed button after {retries} attempts.")
    return False

def get_gif_url(driver, timeout=GIF_TIMEOUT, poll=GIF_POLL):
    try:
        dialog = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, 'dialog'))
        )
    except TimeoutException:
        print('Embed dialog never became visible.')
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            img = dialog.find_element(By.CSS_SELECTOR, "img[alt='Video thumbnail']")
            src = (img.get_attribute('src') or '').strip()
            if src and '.gif' in src.lower():
                print(f"GIF URL found: {src}")
                return src
        except NoSuchElementException:
            pass
        time.sleep(poll)
    print(f"Timed-out after {timeout}s waiting for GIF URL.")
    return None

def fetch_gif_url_with_retries(driver, max_attempts=GIF_MAX_ATTEMPTS, wait_between_attempts=GIF_WAIT_BETWEEN_ATTEMPTS):
    for attempt in range(1, max_attempts + 1):
        print(f"[GIF] Attempt {attempt}/{max_attempts}")
        clear_uppy_overlay(driver)
        if not click_share_button(driver):
            print("  ↳ Couldn’t open Share modal.")
            return None
        if not click_embed_button(driver):
            print("  ↳ Couldn’t click Embed tab.")
            return None
        gif_url = get_gif_url(driver)
        if gif_url:
            return gif_url
        if attempt < max_attempts:
            print(f"  ↳ GIF still processing. Waiting {wait_between_attempts}s, then retrying…")
            try:
                close_btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
                )
                safe_click(driver, close_btn)
            except TimeoutException:
                pass
            refresh_if_video(driver, pause=1.0)
            time.sleep(wait_between_attempts)
    print('[GIF] Gave up after maximum retries.')
    return None

# ───────── Shadow-DOM helpers ─────────

def deep_query(driver, selector):
    script = r"""
    const sel = arguments[0];
    const seen = new Set();
    function* allNodes(root) {
      yield root;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let cur;
      while ((cur = walker.nextNode())) {
        yield cur;
        if (cur.shadowRoot && !seen.has(cur.shadowRoot)) {
          seen.add(cur.shadowRoot);
          yield* allNodes(cur.shadowRoot);
        }
      }
    }
    for (const scope of allNodes(document)) {
      const el = scope.querySelector?.(sel);
      if (el) return el;
    }
    return null;
    """
    return driver.execute_script("return (function(){%s})();" % script, selector)

def deep_query_all(driver, selector):
    script = r"""
    const sel = arguments[0];
    const out = [];
    const seen = new Set();
    function collect(root) {
      out.push(...root.querySelectorAll(sel));
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let cur;
      while ((cur = walker.nextNode())) {
        if (cur.shadowRoot && !seen.has(cur.shadowRoot)) {
          seen.add(cur.shadowRoot);
          collect(cur.shadowRoot);
        }
      }
    }
    collect(document);
    return out;
    """
    return driver.execute_script(script, selector)

def wait_for_deep(driver, selector, timeout=8, poll=0.1):
    deadline = time.time() + timeout
    el = None
    while time.time() < deadline:
        try:
            el = deep_query(driver, selector)
            if el:
                return el
        except Exception:
            pass
        time.sleep(poll)
    return None

def wait_for_element(driver, *, css_selector=None, xpath=None, timeout=12, poll=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if css_selector:
            try:
                el = deep_query(driver, css_selector)
                if el:
                    return el
            except Exception:
                pass
        if xpath:
            try:
                el = driver.find_element(By.XPATH, xpath)
                if el:
                    return el
            except Exception:
                pass
        time.sleep(poll)
    return None

# ───────── Title read helpers (no rename used) ─────────

def _read_current_title(driver) -> str:
    try:
        el = wait_for_deep(driver, TITLE_STATIC_SEL, timeout=8)
        if el:
            txt = (el.get_attribute('innerText') or el.text or '').strip()
            if txt:
                return txt
    except Exception:
        pass
    try:
        if NEW_TITLE_XPATH:
            el = driver.find_element(By.XPATH, NEW_TITLE_XPATH)
            txt = (el.text or '').strip()
            if txt:
                return txt
    except Exception:
        pass
    try:
        doc_title = (driver.title or '').strip()
        if ' | ' in doc_title:
            return doc_title.split(' | ', 1)[0].strip()
        return doc_title
    except Exception:
        return ''

def rename_copy_remove_prefix(driver, max_retries=3):
    """Kept for compatibility; disabled by default via SKIP_COPY_OF_STRIP."""
    current = _read_current_title(driver)
    print(f"Current title detected: '{current}'")
    if not current.startswith('Copy of '):
        print("Title does not start with 'Copy of ' — skip.")
        return True
    if SKIP_COPY_OF_STRIP:
        print("SKIP_COPY_OF_STRIP=1 → skipping UI rename/strip.")
        return True
    # If you *really* want strip behavior, you can implement it here.
    return True

# ───────── unified upload→open→(optional strip)→get video+gif ─────────

def upload_open_and_capture(driver, file_path, card_name, *, do_rename=False, new_name=None, require_rename=False):
    """
    Returns (video_url, gif_url, rename_ok)
    NOTE: do_rename is ignored in this strategy; we never UI-rename.
    """
    if not attempt_upload_process(driver, file_path):
        print(f"❌ Upload failed for {file_path}")
        return None, None, False

    if not locate_and_click_uploaded_video(driver, card_name):
        print(f"❌ Could not open '{card_name}' after upload.")
        return None, None, False

    video_url = driver.current_url
    print(f"Video URL ({card_name}): {video_url}")

    # Optionally strip "Copy of " (disabled by default)
    try:
        if not rename_copy_remove_prefix(driver, max_retries=3):
            if require_rename:
                print('❌ Could not strip "Copy of " and rename is required.')
                return None, None, False
    except Exception as e:
        print(f"Auto-strip 'Copy of ' step error (ignored unless required): {e}")
        if require_rename:
            return None, None, False

    print(f"Sharing and retrieving GIF URL for {card_name}…")
    gif_url = fetch_gif_url_with_retries(driver)
    try:
        enter_workspace(driver, timeout=30)
    except WebDriverException:
        pass
    if not gif_url:
        return video_url, None, True
    return video_url, gif_url, True

# ───────── Helpers: tabs & index ─────────

def close_extra_tabs(driver):
    while len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
        driver.close()
        driver.switch_to.window(driver.window_handles[0])

def get_last_processed_index():
    if os.path.exists(TRACKING_FILE):
        try:
            with open(TRACKING_FILE, 'r') as f:
                return int((f.read() or '0').strip())
        except Exception:
            return 0
    return 0

def update_last_processed_index(index):
    try:
        with open(TRACKING_FILE, 'w') as f:
            f.write(str(index))
        print(f"Tracking file updated with last processed index: {index}")
    except Exception as e:
        print(f"Failed to update tracking file: {e}")

# ───────── Login bootstrap (manual once) ─────────

print('Launching non-headless Chrome for manual Loom login...')
temp_driver = init_driver(headless=False)
try:
    WebDriverWait(temp_driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    temp_driver.get(LOOM_ROOT_URL)
except Exception:
    temp_driver.get(LOOM_ROOT_URL)
try:
    WebDriverWait(temp_driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    temp_driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ENTER)
    print('⚡ Sent ENTER to open the login modal (if present).')
except Exception:
    pass
try:
    WebDriverWait(temp_driver, 30).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name='email']"))
    )
except TimeoutException:
    print('Proceeding—login fields not detected, maybe already signed in.')
input('Please log in to Loom in the visible Chrome window, then press Enter here to continue... ')
save_cookies_to_disk(temp_driver.get_cookies())
try:
    temp_driver.quit()
except Exception:
    pass

# Main driver (headless configurable)
driver = _build_driver_with_cookies(headless=RUN_HEADLESS_AFTER_LOGIN)
print(f"✅ Driver is ready. Headless={RUN_HEADLESS_AFTER_LOGIN}. Starting processing loop...\n")

# ───────── Graceful shutdown ─────────
_stop_flag = False

def _handle_sigint(signum, frame):
    global _stop_flag
    print('\n👋 Caught interrupt. Will exit after current iteration…')
    _stop_flag = True

signal.signal(signal.SIGINT, _handle_sigint)

# ───────── MAIN LOOP ─────────
try:
    while not _stop_flag:
        driver_ref = {'driver': driver}
        revive_driver_if_needed(driver_ref)
        driver = driver_ref['driver']

        print('Searching for new video files in the folder...')
        last_processed_index = get_last_processed_index()
        next_index = last_processed_index + 1

        comp_path = os.path.join(VIDEO_FOLDER_PATH, f"Composition{next_index}.mp4")
        next_comp_exists = os.path.exists(os.path.join(VIDEO_FOLDER_PATH, f"Composition{next_index + 1}.mp4"))

        ready = os.path.exists(comp_path) and (next_comp_exists if AHEAD_SAFETY else True)
        if ready:
            try:
                print(f"New Composition detected and ready: {comp_path}")
                row_index = next_index - 1
                data = pd.read_csv(CSV_FILE_PATH)

                # Validate CSV
                if 'Rename to' not in data.columns:
                    raise RuntimeError("CSV missing required 'Rename to' column.")
                if row_index >= len(data):
                    print('Row index beyond CSV length; waiting for CSV to catch up…')
                    time.sleep(8)
                    continue

                # Build target title and create a temp title-named copy for the Composition
                new_name = str(data.at[row_index, 'Rename to']).strip() or f"Composition{next_index}"
                print(f"Target title for Composition{next_index}: '{new_name}'")
                temp_comp_path = make_temp_titled_copy(comp_path, new_name)

                try:
                    # Upload the temp-named file; search for the card using the final title
                    comp_video_url, comp_gif_url, _ = upload_open_and_capture(
                        driver,
                        temp_comp_path,
                        new_name,            # card_name matches temp filename → Loom title
                        do_rename=False,
                        new_name=None,
                        require_rename=False
                    )
                finally:
                    try:
                        if os.path.exists(temp_comp_path):
                            os.remove(temp_comp_path)
                            print(f"🧹 Removed temp file: {temp_comp_path}")
                    except Exception:
                        pass

                # If upload or GIF failed, retry the same index almost immediately (small pause only)
                if not (comp_video_url and comp_gif_url):
                    print('⚠️ Composition step incomplete (URL/GIF). Retrying index next loop.')
                    enter_workspace(driver, timeout=30)
                    print("⏳ Waiting 6 seconds before retrying this upload...")
                    time.sleep(6)
                    continue

                ###########################
                # DUPLICATE SAFETY CHECKS #
                ###########################

                # Pull previously stored URLs (NOT including current blank row)
                existing_video_urls = set(
                    str(u).strip()
                    for u in data['SNIPPET_1'].dropna()
                ) if 'SNIPPET_1' in data.columns else set()

                existing_gif_urls = set(
                    str(u).strip()
                    for u in data['SNIPPET_2'].dropna()
                ) if 'SNIPPET_2' in data.columns else set()

                video_dup = comp_video_url.strip() in existing_video_urls
                gif_dup   = comp_gif_url.strip()   in existing_gif_urls

                if video_dup or gif_dup:
                    print("⚠️ Detected duplicate URL(s) in existing CSV:")
                    if video_dup:
                        print(f"   ❗ Video URL already exists in SNIPPET_1: {comp_video_url}")
                    if gif_dup:
                        print(f"   ❗ GIF URL already exists in SNIPPET_2: {comp_gif_url}")
                    print("↩️ Discarding current browser session and retrying this index from a fresh session.")

                    # Hard reset of the browser so the next loop is truly “fresh”
                    try:
                        if driver is not None:
                            driver.quit()
                            print("🧯 Closed current WebDriver session due to duplicate detection.")
                    except Exception as e:
                        print(f"⚠️ Error while quitting driver (ignored): {e}")
                    driver = None  # so revive_driver_if_needed will rebuild from scratch

                    time.sleep(6)
                    continue

                ###########################

                # Atomic CSV write
                data.at[row_index, 'SNIPPET_1'] = comp_video_url
                data.at[row_index, 'SNIPPET_2'] = comp_gif_url
                data.to_csv(CSV_FILE_PATH, index=False)
                print('✅ CSV updated atomically with Composition video + GIF URLs.')

                row_payload = data.loc[row_index].to_dict()
                if send_zapier_webhook(row_payload):
                    print('✅ Forwarded row data to Make webhook.')
                    update_last_processed_index(next_index)  # advance only on full success

                    # Delete original source file after verified success
                    try:
                        if os.path.exists(comp_path):
                            os.remove(comp_path)
                            print(f"🗑️ Deleted source video after successful upload: {comp_path}")
                        else:
                            print(f"ℹ️ Source video already missing: {comp_path}")
                    except Exception as e:
                        print(f"⚠️ Failed to delete source video '{comp_path}': {e}")

                else:
                    print('⚠️ Webhook failed, retrying same index next loop.')

                try:
                    enter_workspace(driver, timeout=30)
                except WebDriverException:
                    pass
                _session_video_count += 1

            except Exception as e:
                print(f"❌ Unexpected error: {e}. Will retry this index...")
                if _is_connectivity_blowup(e):
                    print('🧨 Browser session unstable, rebuilding…')
                    try:
                        if driver is not None:
                            driver.quit()
                    except Exception:
                        pass
                    driver = None
                time.sleep(5)
        else:
            missing = []
            if not os.path.exists(comp_path): missing.append(f"Composition{next_index}.mp4")
            if AHEAD_SAFETY and not next_comp_exists: missing.append(f"Composition{next_index + 1}.mp4 (ahead-safety)")
            if missing:
                print('⏳ Waiting for files: ' + ", ".join(missing))
            else:
                print('⏳ Waiting for next render…')
        time.sleep(8)

except KeyboardInterrupt:
    print('\n👋 KeyboardInterrupt — stopping loop.')
finally:
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass
    print('🧹 Driver closed. Exiting.')
