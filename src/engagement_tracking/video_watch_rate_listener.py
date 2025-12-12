"""
video_watch_rate_listener.py

Role in GTM System
-----------------
Event-driven engagement listener used inside an automated outbound GTM pipeline.

This service is NOT a standalone scraper.
It is triggered asynchronously via webhook events (typically from Zapier)
after a prospect interacts with a personalized outbound video.

End-to-End Workflow
-------------------
1. Outbound email is sent (via GTM automation)
2. Prospect clicks and watches a personalized video
3. Video platform emits a view event
4. Zapier receives the event and POSTs to this service
5. This service:
   - Authenticates into the video platform
   - Navigates shadow-DOM / iframe-based UI
   - Extracts high-signal engagement metrics (e.g. watch rate)
6. Structured results are returned to Zapier
7. Zapier routes the signal to:
   - Slack alerts
   - CRM updates
   - Lead scoring & follow-up logic

Why this exists
---------------
Granular engagement metrics are often unavailable or unreliable via public APIs.
This service programmatically extracts high-intent signals from authenticated UI
surfaces in a controlled, fault-tolerant way.

Key Characteristics
-------------------
- Webhook-driven (Flask)
- Shadow DOM–aware Selenium automation
- Same-origin iframe traversal
- Hard execution timeouts using multiprocessing
- Aggressive process cleanup and deterministic fallbacks

Note
----
This repository version is sanitized for portfolio use.
Credentials and client-specific configuration are injected externally.
"""

# loom_webhook.py – Flask + Selenium + 50-second hard timeout
# ------------------------------------------------------------
# Shadow-DOM aware selectors + watchdog/Flask structure.

import time
import sys
import logging
import subprocess
import platform
from multiprocessing import Process, Queue
from queue import Empty as QueueEmpty
from typing import Any, Dict, List

from flask import Flask, request, jsonify

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    NoAlertPresentException,
)

# ===== Logging =======================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s %(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ===== Flask =========================================================
app = Flask(__name__)

# ===== Selectors (kept for reference/backups) ========================
VIDEO_TITLE_XPATH    = "//li[contains(@class,'creatorTitleBar')]//span"
VIDEO_TITLE_SELECTOR = "li[class*='creatorTitleBar'] span"
NEW_COPY_TITLE_XPATH = "/html/body/main/div/div[2]/header/div/div/div/ul/li[4]/div/div[1]/div/div[1]/span"

# ===== Timeout helper ===============================================
def _kill_chrome_family() -> None:
    """Terminate any leftover Chrome / chromedriver processes."""
    system = platform.system().lower()
    try:
        if system in ("linux", "darwin"):
            for name in ("chromedriver", "chrome", "Google Chrome", "Google Chrome Helper"):
                subprocess.run(["pkill", "-9", "-f", name],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for name in ("chromedriver", "chrome"):
                subprocess.run(["killall", "-9", name],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "windows":
            for name in ("chromedriver.exe", "chrome.exe"):
                subprocess.run(["taskkill", "/F", "/IM", name, "/T"],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        logger.exception("Failed to kill Chrome family")

def _runner(q: Queue, func, *args, **kwargs):
    try:
        q.put(func(*args, **kwargs))
    except Exception:
        logger.exception("Sub-process crashed in %s", func.__name__)
        q.put(None)
    finally:
        _kill_chrome_family()

def run_with_timeout_proc(func, timeout: int, fallback: Dict[str, Any], *args, **kwargs):
    q: Queue = Queue()
    p = Process(target=_runner, args=(q, func, *args), kwargs=kwargs, daemon=True)
    p.start()
    p.join(timeout)

    if p.is_alive():
        logger.error("%s exceeded %s s – terminating", func.__name__, timeout)
        p.terminate(); p.join(3)
        if p.is_alive():
            p.kill(); p.join(2)
        _kill_chrome_family()
        return fallback

    try:
        return q.get_nowait()
    except QueueEmpty:
        logger.error("%s ended without result – using fallback", func.__name__)
        return fallback

# ===== Utility: slow typing =========================================
def manual_type(element, text, delay=0.07):
    for ch in text:
        element.send_keys(ch); time.sleep(delay)

# ===== Shadow DOM helpers ===========================================
# These helpers “see” into any open shadow roots (and same-origin iframes).

_DEEP_QUERY_JS = r"""
const selector = arguments[0];
const root = arguments[1] || document;
function* walk(node) {
  if (!node) return;
  yield node;
  const kids = node.querySelectorAll ? node.querySelectorAll('*') : [];
  for (const el of kids) {
    if (el.shadowRoot) {
      yield el.shadowRoot;
      yield* walk(el.shadowRoot);
    }
  }
}
for (const scope of walk(root)) {
  try {
    const found = scope.querySelector(selector);
    if (found) return found;
  } catch (e) {}
}
return null;
"""

_DEEP_QUERY_ALL_JS = r"""
const selector = arguments[0];
const root = arguments[1] || document;
const out = [];
function* walk(node) {
  if (!node) return;
  yield node;
  const kids = node.querySelectorAll ? node.querySelectorAll('*') : [];
  for (const el of kids) {
    if (el.shadowRoot) {
      yield el.shadowRoot;
      yield* walk(el.shadowRoot);
    }
  }
}
for (const scope of walk(root)) {
  try {
    scope.querySelectorAll(selector).forEach(el => out.push(el));
  } catch(e) {}
}
return out;
"""

# Find first element whose textContent includes a substring (shadow-safe)
_FIND_BY_TEXT_JS = r"""
const needle = (arguments[0] || "").toLowerCase();
const root   = arguments[1] || document;
function* walk(node) {
  if (!node) return;
  yield node;
  const kids = node.querySelectorAll ? node.querySelectorAll('*') : [];
  for (const el of kids) {
    if (el.shadowRoot) {
      yield el.shadowRoot;
      yield* walk(el.shadowRoot);
    }
  }
}
for (const scope of walk(root)) {
  if (!scope.querySelectorAll) continue;
  const all = scope.querySelectorAll("*");
  for (const el of all) {
    let txt = "";
    try { txt = (el.textContent || "").toLowerCase(); } catch(e) { txt = ""; }
    if (txt && txt.includes(needle)) return el;
  }
}
return null;
"""

# Boolean "has text" (shadow-safe)
_HAS_TEXT_JS = r"""
const needle = (arguments[0] || "").toLowerCase();
const root   = arguments[1] || document;
function* walk(node) {
  if (!node) return;
  yield node;
  const kids = node.querySelectorAll ? node.querySelectorAll('*') : [];
  for (const el of kids) {
    if (el.shadowRoot) {
      yield el.shadowRoot;
      yield* walk(el.shadowRoot);
    }
  }
}
for (const scope of walk(root)) {
  if (!scope.querySelectorAll) continue;
  const all = scope.querySelectorAll("*");
  for (const el of all) {
    let txt = "";
    try { txt = (el.textContent || "").toLowerCase(); } catch(e) { txt = ""; }
    if (txt && txt.includes(needle)) return true;
  }
}
return false;
"""

# NEW: Get visible text of the top document body
_BODY_TEXT_JS = r"""
try {
  const b = document && document.body;
  return (b and ('innerText' in b) ? b.innerText : (b ? b.textContent : '')) || '';
} catch (e) { return ''; }
""".replace("and","&&")  # keep semantics identical to original innerText logic

def _deep_find(driver, css: str, timeout: int = 12):
    """Wait for first match anywhere in light DOM + open shadow roots."""
    w = WebDriverWait(driver, timeout)
    return w.until(lambda d: d.execute_script(_DEEP_QUERY_JS, css))

def _deep_find_all(driver, css: str, timeout: int = 12):
    w = WebDriverWait(driver, timeout)
    return w.until(lambda d: d.execute_script(_DEEP_QUERY_ALL_JS, css))

def _deep_click(driver, css: str, timeout: int = 12):
    el = _deep_find(driver, css, timeout)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    return el

def _switch_into_first_same_origin_iframe(driver) -> bool:
    """If the content is inside a same-origin iframe, switch to it."""
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
    for idx, f in enumerate(frames):
        try:
            driver.switch_to.frame(f)
            driver.execute_script("return document.title;")  # switchable → same-origin
            logger.debug("Switched into iframe #%d", idx)
            return True
        except Exception:
            driver.switch_to.default_content()
    return False

def _try_all_frames_deep(driver, css: str, timeout: int = 12):
    """Search main doc, then each same-origin iframe using the deep query."""
    el = driver.execute_script(_DEEP_QUERY_JS, css)
    if el:
        return el
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
    for idx in range(len(frames)):
        try:
            driver.switch_to.frame(idx)
            el = driver.execute_script(_DEEP_QUERY_JS, css)
            if el:
                return el
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    w = WebDriverWait(driver, timeout)
    return w.until(lambda d: _try_all_frames_deep(d, css, 0))  # last shot (timeout=0)

# ===== Title grabber (shadow-aware) =================================
def _grab_video_title(driver, wait, max_wait=10) -> str:
    candidates: List[str] = [
        "li[class*='creatorTitleBar'] span",
        "header h1 span",
        "header h1",
        "[data-testid*='title']",
        "h1, h2",
    ]
    start = time.time()
    while time.time() - start < max_wait:
        for css in candidates:
            try:
                el = driver.execute_script(_DEEP_QUERY_JS, css)
                if el:
                    text = (el.text or "").strip()
                    if text:
                        return text
            except Exception:
                pass
        time.sleep(0.25)
    try:
        return (driver.title.split("|", 1)[0].strip() or "Unknown title")
    except Exception:
        return "Unknown title"

# ===== Share-page readiness =========================================
def _dismiss_copy_prompt_if_present(driver, accept=False) -> bool:
    try:
        alert = driver.switch_to.alert
        text = (alert.text or "").lower()
        if "copy to clipboard" in text or "loom.com says" in text:
            (alert.accept() if accept else alert.dismiss())
            time.sleep(0.2)
            return True
    except NoAlertPresentException:
        pass
    except Exception:
        try: driver.switch_to.default_content()
        except Exception: pass
    return False

def _wait_until_authenticated_on_share(driver, max_wait=12):
    """Wait until the Loom share page shows the owner UI (actions menu present)."""
    w = WebDriverWait(driver, max_wait)
    w.until(lambda d: "loom.com/share/" in d.current_url)

    def _owner_ui(drv):
        for css in [
            "#toggleActions",
            "[data-testid='toggleActions']",
            "button[aria-label*='actions' i]",
            "div[role='combobox'][aria-haspopup='listbox'] button",
        ]:
            try:
                if drv.execute_script(_DEEP_QUERY_JS, css):
                    return True
            except Exception:
                pass
        try:
            return bool(drv.execute_script(_HAS_TEXT_JS, "actions") or
                        drv.execute_script(_HAS_TEXT_JS, "share"))
        except Exception:
            return False

    w.until(_owner_ui)

# ===== Views tab (shadow-aware) =====================================
def click_views_tab(driver, wait, timeout=12):
    """
    Click the 'Views' tab in the right panel (inside shadow roots).
    Verification: aria-selected=true OR presence of 'Average Completion Rate'.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    selectors = [
        "button[data-testid='sidebar-tab-Views']",
        "[role='tab'][aria-controls*='Views']",
        "button[aria-label*='Views' i]",
    ]

    btn = None
    for sel in selectors:
        try:
            btn = _try_all_frames_deep(driver, sel, timeout=3)
            if btn:
                break
        except Exception:
            pass

    if not btn:
        try:
            btn = driver.execute_script(_FIND_BY_TEXT_JS, "Views")
        except Exception:
            btn = None

    if not btn:
        raise TimeoutException("Could not locate the Views tab")

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    except Exception:
        pass
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)

    # Verify selection or panel text
    end = time.time() + timeout
    while time.time() < end:
        try:
            try:
                if btn.get_attribute("aria-selected") == "true":
                    break
            except Exception:
                pass
            if driver.execute_script(_HAS_TEXT_JS, "Average Completion Rate"):
                break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        raise TimeoutException("Views tab did not become active")

    time.sleep(0.3)

# ===== ACR extractor (robust: use visible body text) =================
def _get_acr_text(driver, timeout=12) -> str:
    """
    Read body innerText from the TOP document and pull the % following
    'Average Completion Rate'. Works even if markup is in shadow roots.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    end = time.time() + timeout
    last_len = 0
    import re
    pattern = re.compile(r"Average\s*Completion\s*Rate[\s:]*([0-9]{1,3}(?:\.[0-9]+)?\s?%)", re.I)

    while time.time() < end:
        try:
            txt = driver.execute_script(_BODY_TEXT_JS) or ""
            last_len = len(txt)
            m = pattern.search(txt)
            if m:
                return m.group(1).replace(" ", "")  # normalize '65 %' -> '65%'
        except Exception:
            pass
        time.sleep(0.25)

    logger.debug("ACR text not found; last body text length=%s", last_len)
    return "Error"

# ===== Helpers for loom_copy (page/menu/tab/title) ===================
def _wait_page_ready_share(driver, max_wait=12):
    """Waits until header or toggleActions is present on /share page."""
    w = WebDriverWait(driver, max_wait)
    w.until(lambda d: "loom.com/share/" in d.current_url)
    def _ready(d):
        try:
            if d.execute_script(_DEEP_QUERY_JS, "header"):
                return True
        except Exception:
            pass
        try:
            if d.execute_script(_DEEP_QUERY_JS, "[data-testid='toggleActions']"):
                return True
        except Exception:
            pass
        return False
    w.until(_ready)

def _open_more_actions_and_click_duplicate(driver, timeout=12):
    """Opens the 'More actions' menu and clicks 'Duplicate'."""
    end = time.time() + timeout

    # Open More actions (priority order)
    opened = False
    while time.time() < end and not opened:
        for sel in ("button[data-testid='toggleActions']",
                    "button#toggleActions[aria-label='Toggle actions']"):
            try:
                btn = driver.execute_script(_DEEP_QUERY_JS, sel)
                if btn:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    try: btn.click()
                    except Exception: driver.execute_script("arguments[0].click();", btn)
                    opened = True
                    break
            except Exception:
                pass
        if not opened:
            # Fallback: look for any element with text 'More actions'
            try:
                t = driver.execute_script(_FIND_BY_TEXT_JS, "More actions")
                if t:
                    try: t.click()
                    except Exception: driver.execute_script("arguments[0].click();", t)
                    opened = True
                    break
            except Exception:
                pass
        if not opened:
            time.sleep(0.2)

    if not opened:
        raise TimeoutException("Could not open More actions menu")

    # Dismiss any stray alerts (clipboard/etc.)
    _dismiss_copy_prompt_if_present(driver, accept=True)

    # Click 'Duplicate' (priority order)
    dup_clicked = False
    end2 = time.time() + 6
    while time.time() < end2 and not dup_clicked:
        try:
            # 1) Click the span if possible; else climb up to a clickable ancestor
            span = driver.find_element(By.XPATH, "//span[normalize-space()='Duplicate']")
            try:
                span.click(); dup_clicked = True; break
            except Exception:
                pass
            anc = span
            for _ in range(5):
                anc = anc.find_element(By.XPATH, "..")
                role = anc.get_attribute("role") or ""
                tag  = anc.tag_name.lower()
                if tag == "button" or role == "menuitem":
                    try: anc.click(); dup_clicked = True; break
                    except Exception: driver.execute_script("arguments[0].click();", anc); dup_clicked = True; break
            if dup_clicked: break
        except Exception:
            # 2) //*[role='menuitem' and .//span[normalize-space()='Duplicate']]
            try:
                mi = driver.find_element(By.XPATH, "//*[@role='menuitem' and .//span[normalize-space()='Duplicate']]")
                try: mi.click()
                except Exception: driver.execute_script("arguments[0].click();", mi)
                dup_clicked = True; break
            except Exception:
                time.sleep(0.2)

    if not dup_clicked:
        raise TimeoutException("Could not click Duplicate")

def _wait_new_tab_and_switch(driver, timeout=12):
    """Waits for a new tab after clicking Duplicate and switches to it."""
    w = WebDriverWait(driver, timeout)
    before = driver.window_handles
    if len(before) < 1:
        before = [driver.current_window_handle]
    w.until(lambda d: len(d.window_handles) > len(before))
    after = driver.window_handles
    new_handle = next(h for h in after if h not in before)
    driver.switch_to.window(new_handle)
    time.sleep(0.5)
    return new_handle, before

def _focus_title_container(driver, timeout=8):
    """Clicks the title container (hash class may change)."""
    end = time.time() + timeout
    last_exc = None
    while time.time() < end:
        try:
            # Example hashed class seen in the wild:
            el = driver.execute_script(_DEEP_QUERY_JS, "div.static-title_staticTitleEditableContainer_YeN")
            if not el:
                # Broader attempt around header/title
                el = driver.execute_script(_DEEP_QUERY_JS, "header h1, header div[role='textbox'], header [contenteditable='true']")
            if el:
                try: driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                except Exception: pass
                try: el.click()
                except Exception: driver.execute_script("arguments[0].click();", el)
                return True
        except Exception as e:
            last_exc = e
        time.sleep(0.2)
    if last_exc:
        raise last_exc
    return False

def _edit_title_prioritized(driver, new_title: str, timeout=10):
    """
    After title area is focused, find an editable input in priority order and set text.
    Priority:
      1) div[contenteditable='true'][role='textbox']
      2) //h1[@contenteditable='true']
      3) input[type='text']
      4) input[placeholder*='title' i]
      Fallback: type into active element.
    """
    end = time.time() + timeout
    key_mod = Keys.COMMAND if platform.system().lower() == "darwin" else Keys.CONTROL

    while time.time() < end:
        try:
            el = driver.execute_script(_DEEP_QUERY_JS, "div[contenteditable='true'][role='textbox']")
            if not el:
                try:
                    el = driver.find_element(By.XPATH, "//h1[@contenteditable='true']")
                except Exception:
                    el = None
            if not el:
                el = driver.execute_script(_DEEP_QUERY_JS, "input[type='text']")
            if not el:
                el = driver.execute_script(_DEEP_QUERY_JS, "input[placeholder*='title' i]")

            if el:
                try:
                    el.send_keys(key_mod, "a")
                    el.send_keys(new_title)
                    el.send_keys(Keys.ENTER)
                    return True
                except Exception:
                    try:
                        driver.execute_script("""
                          const el = arguments[0], txt = arguments[1];
                          if ('value' in el) el.value = txt;
                          else if (el.setRangeText) { el.setRangeText(txt); }
                          else { el.textContent = txt; }
                        """, el, new_title)
                        el.send_keys(Keys.ENTER)
                        return True
                    except Exception:
                        pass
            else:
                try:
                    active = driver.switch_to.active_element
                    active.send_keys(key_mod, "a")
                    active.send_keys(new_title)
                    active.send_keys(Keys.ENTER)
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.2)
    return False

def _verify_title_text(driver, expected: str, timeout=10):
    """Verifies the visible title equals the exact expected text."""
    norm = (expected or "").strip()
    if not norm:
        return False
    end = time.time() + timeout
    while time.time() < end:
        try:
            xp1 = f"//span[contains(@class,'css-') and normalize-space() = {repr(norm)}]"
            try:
                el = driver.find_element(By.XPATH, xp1)
                if el.is_displayed(): return True
            except Exception:
                pass
            xp2 = f"//*[self::h1 or self::div or self::span][normalize-space() = {repr(norm)}]"
            try:
                el2 = driver.find_element(By.XPATH, xp2)
                if el2.is_displayed(): return True
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(0.25)
    return False

# ===== Workflow A: get_watch_rate (UNCHANGED) =======================
def get_watch_rate(username: str, password: str, video_url: str) -> dict:
    chrome_opts = Options()
    chrome_opts.headless = False
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--window-size=1440,900")

    driver = webdriver.Chrome(options=chrome_opts)
    wait = WebDriverWait(driver, 15)

    try:
        driver.get("https://www.loom.com/login")
        email = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']")))
        email.clear(); manual_type(email, username)
        pwd = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        manual_type(pwd, password); pwd.send_keys(Keys.RETURN)
        wait.until(lambda d: "/login" not in d.current_url)
        time.sleep(1)

        driver.get(video_url)
        _wait_until_authenticated_on_share(driver, max_wait=12)
        time.sleep(0.5)

        _switch_into_first_same_origin_iframe(driver)
        title = _grab_video_title(driver, wait, max_wait=8)

        # Operate Views panel from top doc
        try: driver.switch_to.default_content()
        except Exception: pass
        click_views_tab(driver, wait, timeout=12)
        try: driver.switch_to.default_content()
        except Exception: pass

        rate = _get_acr_text(driver, timeout=12)

    except Exception:
        logger.exception("get_watch_rate failed")
        title, rate = "Error", "Error"
    finally:
        try: driver.switch_to.default_content()
        except Exception: pass
        driver.quit()

    return {"username": username, "video_url": video_url, "video_title": title, "watch_rate": rate}

# ===== Workflow B: loom_copy (duplicate + exact-title rename) =======
def loom_copy(username: str, password: str, video_url: str) -> dict:
    logger.info("Starting loom_copy for %s", video_url)

    chrome_opts = Options()
    chrome_opts.headless = False
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--window-size=1440,900")

    driver = webdriver.Chrome(options=chrome_opts)
    wait = WebDriverWait(driver, 15)

    result = {
        "username": username,
        "original_video_url": video_url,
        "original_video_title": None,
        "new_video_url": "Error",
    }

    try:
        # Login
        driver.get("https://www.loom.com/login")
        manual_type(wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']"))), username)
        pwd = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        manual_type(pwd, password); pwd.send_keys(Keys.RETURN)
        wait.until(lambda d: "/login" not in d.current_url)

        # Go to original video share page
        driver.get(video_url)
        _wait_page_ready_share(driver, max_wait=12)
        _dismiss_copy_prompt_if_present(driver, accept=True)

        # Capture original title (use this EXACT title for the duplicate)
        try:
            _switch_into_first_same_origin_iframe(driver)
        except Exception:
            pass
        try:
            orig_title = _grab_video_title(driver, wait, max_wait=8)
            result["original_video_title"] = orig_title
            logger.info("Original title: %s", orig_title)
        finally:
            try: driver.switch_to.default_content()
            except Exception: pass

        # Open menu → Duplicate
        before_handles = driver.window_handles[:]
        _open_more_actions_and_click_duplicate(driver, timeout=12)

        # New tab appears; switch to it (one-shot; no internal retries)
        try:
            new_handle, old_handles = _wait_new_tab_and_switch(driver, timeout=12)
        except Exception:
            logger.error("Duplicate did not open a new tab in time")
            return result  # new_video_url stays "Error"

        logger.debug("Switched to duplicated tab: %s", new_handle)
        _wait_page_ready_share(driver, max_wait=12)
        _dismiss_copy_prompt_if_present(driver, accept=True)

        # Close the original tab(s) and keep only the current one
        try:
            curr = driver.current_window_handle
            for h in old_handles:
                if h != curr:
                    try:
                        driver.switch_to.window(h)
                        driver.close()
                    except Exception:
                        pass
            driver.switch_to.window(curr)
        except Exception:
            pass

        # Record duplicated URL
        try:
            dup_url = driver.current_url
            if "loom.com/share/" in dup_url:
                result["new_video_url"] = dup_url
        except Exception:
            pass

        # Rename: set EXACTLY to the original title
        desired_title = result.get("original_video_title") or ""
        if desired_title.strip():
            _focus_title_container(driver, timeout=8)
            ok = _edit_title_prioritized(driver, desired_title, timeout=10)
            if not ok:
                logger.warning("Title edit attempt may have failed")

            # Strict verification: must equal original title
            if not _verify_title_text(driver, desired_title, timeout=10):
                logger.warning("Title verification failed (expected exact match)")

    except Exception:
        logger.exception("Error in loom_copy")
    finally:
        try: driver.switch_to.default_content()
        except Exception: pass
        driver.quit()
        logger.debug("Driver quit in loom_copy")

    return result

# ===== Flask endpoint ===============================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    action = data.get("action", "loom_scraper")
    username = data.get("username")
    password = data.get("password")
    video_url = data.get("video_url")
    if not username or not password or not video_url:
        return jsonify({"error": "Missing required fields"}), 400

    if action == "loom_scraper":
        fallback = {
            "username": username,
            "video_url": video_url,
            "video_title": "Error",
            "watch_rate": "Error",
            "timed_out": True,
        }
        result = run_with_timeout_proc(get_watch_rate, 50, fallback, username, password, video_url)

    elif action == "loom_copy":
        fallback = {
            "username": username,
            "original_video_url": video_url,
            "original_video_title": "Error",
            "new_video_url": "Error",
            "timed_out": True,
        }
        result = run_with_timeout_proc(loom_copy, 50, fallback, username, password, video_url)
    else:
        return jsonify({"error": f"Unrecognized action: {action}"}), 400

    if isinstance(result, dict) and "timed_out" not in result:
        result["timed_out"] = False
    return jsonify(result), 200

# ===== Local dev runner =============================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)x
