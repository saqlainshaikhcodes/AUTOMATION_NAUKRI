#!/usr/bin/env python3
"""
Naukri profile refresher.

Roz login karke (1) resume re-upload aur (2) resume headline me chhota refresh
karta hai taaki profile "recently updated" rahe aur recruiter search me upar aaye.

Env vars:
  NAUKRI_USER   (required)
  NAUKRI_PASS   (required)
  RESUME_PATH   default: Saqlain_Shaikh_Senior_Full_Stack_Developer.pdf
  HEADLESS      default: true
  TEST_MODE     default: false  (slow-mo + har step ka screenshot; kaam phir bhi poora hota hai)
  MAX_RETRIES   default: 3
  RUN_APPLY     default: false (true = login+refresh ke baad Naukri par auto-apply, apply.py dekho)
"""
import os
import sys

import json
import time
import random
import logging
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

load_dotenv()


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# --- CONFIG ---------------------------------------------------------------
USER = (os.getenv("NAUKRI_USER") or "").strip()
PASS = os.getenv("NAUKRI_PASS") or ""
RESUME = Path(os.getenv("RESUME_PATH", "Saqlain_Shaikh_Senior_Full_Stack_Developer.pdf"))
HEADLESS = _flag("HEADLESS", "true")
TEST_MODE = _flag("TEST_MODE", "false")
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "3")))
RUN_APPLY = _flag("RUN_APPLY", "false")
_APPLY_DONE = False  # retry loop me apply dobara na chale
# Saved browser session (cookies). Isse OTP wala login baar-baar nahi karna padta.
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
# Naukri headline limit = 250 chars
HEADLINE = os.getenv(
    "HEADLINE",
    "Senior Full Stack Developer | 5+ Yrs | React Native, React.js, Next.js, Node.js, Express | "
    "MongoDB, MySQL, PostgreSQL | 10+ Apps Shipped | Stripe, Razorpay & Payment Gateways | "
    "Biometric KYC | AWS, CI/CD | Immediate Joiner",
).strip()
if len(HEADLINE) > 249:
    raise SystemExit(f"HEADLINE {len(HEADLINE)} chars hai, limit 250 (trailing '.' ke liye 249)")

LOGIN_URL = "https://www.naukri.com/nlogin/login"
PROFILE_URL = "https://www.naukri.com/mnjuser/profile"
DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("naukri")


class BlockedError(RuntimeError):
    """Captcha / OTP / security check - retry se theek nahi hoga."""


# --- HELPERS --------------------------------------------------------------
def pause(lo: float = 1.0, hi: float = 3.0) -> None:
    time.sleep(random.uniform(lo, hi))


def shot(page: Page, name: str, force: bool = False) -> None:
    if TEST_MODE or force:
        try:
            page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=True)
        except Exception as e:  # screenshot kabhi bhi main kaam ko fail na kare
            log.warning("screenshot %s fail: %s", name, e)


def check_blocked(page: Page) -> None:
    """Captcha / OTP / verification page detect karo."""
    try:
        body = page.inner_text("body", timeout=3000).lower()
    except Exception:
        return
    markers = ("captcha", "enter otp", "otp sent to", "verify it's you", "verification code", "unusual activity")
    hit = next((m for m in markers if m in body), None)
    if hit:
        shot(page, "blocked", force=True)
        raise BlockedError(f"Naukri ne security check maanga ('{hit}'). Manual login ki zaroorat.")


def open_profile(page: Page) -> None:
    page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PWTimeout:
        log.warning("networkidle timeout, aage badh rahe hain")
    if "nlogin" in page.url:
        raise RuntimeError("Session expire ho gaya (login page par redirect)")
    check_blocked(page)
    pause(2, 4)


# --- STEPS ----------------------------------------------------------------
def login(page: Page) -> None:
    log.info("Login start")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

    user_input = page.locator("#usernameField, input[placeholder*='Email' i]").first
    user_input.wait_for(state="visible", timeout=20_000)
    pause(1, 2)
    user_input.fill(USER)

    pass_input = page.locator("input[type='password']").first
    pass_input.wait_for(state="visible", timeout=10_000)
    pause(0.5, 1.5)
    pass_input.fill(PASS)
    shot(page, "01_login_filled")

    pause(1, 2)
    page.locator("button[type='submit'], .btn-login, .btn-primary").first.click()

    try:
        page.wait_for_function(
            "() => !window.location.href.includes('nlogin')", timeout=45_000
        )
    except PWTimeout:
        check_blocked(page)
        err = ""
        try:
            err = page.locator(".server-err, .err-msg, [class*='error' i]").first.inner_text(timeout=2000)
        except Exception:
            pass
        raise RuntimeError(f"Login nahi hua. Page message: {err.strip() or 'n/a'}")

    pause(3, 5)
    check_blocked(page)
    log.info("Login OK")
    shot(page, "02_after_login")


def saved_state_ok() -> bool:
    """state.json hai aur valid JSON (cookies wala) hai?"""
    if not STATE_FILE.exists():
        return False
    try:
        data = json.loads(STATE_FILE.read_text())
        return bool(data.get("cookies"))
    except Exception as e:
        log.warning("state.json kharab hai, ignore kar raha hoon: %s", e)
        return False


def ensure_logged_in(page: Page) -> None:
    """Pehle saved session try karo; expire ho to password se login."""
    if saved_state_ok():
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
        pause(3, 5)
        check_blocked(page)
        if "nlogin" not in page.url:
            log.info("Saved session se login OK")
            return
        log.info("Saved session expire ho gaya, password se login")
    login(page)


def upload_resume(page: Page) -> None:
    log.info("Resume upload start: %s", RESUME.name)
    open_profile(page)

    uploads = []

    def on_response(resp):
        req = resp.request
        if req.method in ("POST", "PUT") and any(
            k in resp.url.lower() for k in ("upload", "attachcv", "resume", "cv")
        ):
            uploads.append(resp.status)

    page.on("response", on_response)
    try:
        file_input = page.locator("input#attachCV, input[type='file']").first
        file_input.wait_for(state="attached", timeout=20_000)
        file_input.set_input_files(str(RESUME.resolve()))

        toast = page.locator(
            "text=/successfully uploaded/i, text=/resume has been uploaded/i, text=/Uploaded on/i"
        ).first
        deadline = time.time() + 40
        ok = False
        while time.time() < deadline:
            if any(200 <= s < 300 for s in uploads):
                ok = True
                break
            try:
                if toast.is_visible():
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(1)
    finally:
        page.remove_listener("response", on_response)

    pause(2, 4)
    shot(page, "03_resume_after", force=True)
    if not ok:
        raise RuntimeError("Resume upload confirm nahi hua (na success message, na 2xx response)")
    log.info("Resume upload OK")


def refresh_headline(page: Page) -> None:
    log.info("Headline refresh start")
    open_profile(page)

    edit = page.locator(
        ".resumeHeadline .edit, .resumeHeadline .icon.edit, "
        "div.widgetHead:has-text('Resume headline') .edit"
    ).first
    edit.scroll_into_view_if_needed(timeout=15_000)
    edit.click()
    pause(1.5, 3)

    box = page.locator(
        "textarea#resumeHeadlineTxt, textarea[name='resumeHeadline'], .resumeHeadline textarea"
    ).first
    box.wait_for(state="visible", timeout=10_000)
    current = box.input_value().strip()
    # Target headline (Full Stack). Naukri same text pe "update" nahi maanta,
    # isliye roz trailing '.' alternate karte hain - current se jo alag ho wahi set hota hai.
    base = HEADLINE.rstrip(".").strip()
    new_text = base if current != base else base + "."
    box.fill(new_text)
    log.info("Headline: %d chars", len(new_text))
    shot(page, "04_headline_edited")

    pause(1, 2)
    page.locator("button:has-text('Save')").last.click()
    pause(3, 5)
    shot(page, "05_profile_after", force=True)
    log.info("Headline refresh OK")


# --- ORCHESTRATION --------------------------------------------------------
def run_once() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=300 if TEST_MODE else 0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            ctx_kwargs = dict(
                user_agent=UA,
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            if saved_state_ok():
                ctx_kwargs["storage_state"] = str(STATE_FILE)
            context = browser.new_context(**ctx_kwargs)
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = context.new_page()
            page.set_default_timeout(30_000)

            try:
                ensure_logged_in(page)
            except Exception:
                shot(page, "login_error", force=True)
                raise

            results = {}
            for step in (upload_resume, refresh_headline):
                try:
                    step(page)
                    results[step.__name__] = None
                except BlockedError:
                    raise
                except Exception as e:
                    log.error("%s fail: %s", step.__name__, e)
                    shot(page, f"{step.__name__}_error", force=True)
                    results[step.__name__] = e

            # --- AUTO APPLY (login + resume/headline refresh ke baad) ---
            global _APPLY_DONE
            if RUN_APPLY and not _APPLY_DONE:
                _APPLY_DONE = True
                try:
                    import apply as applier  # late import: sirf RUN_APPLY par
                    summary = applier.run(page, context, RESUME)
                    log.info("Apply summary: %s", summary)
                    if summary.get("abort"):
                        print(f"::warning::Apply stopped early: {summary['abort']}", flush=True)
                except Exception as e:  # apply ka error login/refresh ko fail na kare
                    log.error("apply crash: %s", e, exc_info=True)
                    shot(page, "apply_error", force=True)
                    print(f"::warning::Apply crashed: {e}", flush=True)

            try:
                context.storage_state(path=str(STATE_FILE))  # fresh session save
            except Exception as e:
                log.warning("session save fail: %s", e)

            failed = [k for k, v in results.items() if v]
            if len(failed) == len(results):
                raise RuntimeError(f"Dono steps fail: {failed}")
            if failed:
                # Partial success: ek step chala = profile update ho gaya, par GitHub me dikhe
                print(f"::warning::Partial success, fail hua: {failed}", flush=True)
        finally:
            browser.close()


def main() -> int:
    if not USER or not PASS:
        log.error("NAUKRI_USER / NAUKRI_PASS set nahi hain")
        return 2
    if not RESUME.is_file():
        log.error("Resume file nahi mili: %s", RESUME)
        return 2

    log.info("Start | headless=%s test_mode=%s retries=%d", HEADLESS, TEST_MODE, MAX_RETRIES)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("Attempt %d/%d", attempt, MAX_RETRIES)
            run_once()
            log.info("DONE")
            return 0
        except BlockedError as e:
            log.error("%s", e)
            return 3  # retry bekaar
        except Exception as e:
            log.error("Attempt %d fail: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(30 * attempt)

    log.error("Sab attempts fail")
    return 1  # GitHub failure email bhejega


if __name__ == "__main__":
    sys.exit(main())
