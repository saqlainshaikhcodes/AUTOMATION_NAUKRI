#!/usr/bin/env python3
"""
Ek baar Mac par chalao: browser khulega, Naukri me login karo (OTP aaye to
email se dekhke daal do). Login hote hi session state.json me save hota hai
aur base64 form clipboard me copy ho jaata hai -> GitHub Secret me paste karo.

Run:  python3 save_session.py
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from main import UA  # same user-agent jo main.py use karta hai (session match ke liye)

load_dotenv()
STATE = Path("state.json")
USER = os.getenv("NAUKRI_USER", "")
PASS = os.getenv("NAUKRI_PASS", "")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent=UA,
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    page = context.new_page()
    for attempt in range(1, 4):
        try:
            page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded", timeout=90_000)
            break
        except Exception as e:
            print(f"Login page load fail (try {attempt}/3): {str(e).splitlines()[0]}")
            if attempt == 3:
                sys.exit("Naukri ka login page nahi khul raha. Normal browser me naukri.com check karo "
                         "(VPN/WiFi), thodi der baad dobara chalao.")
            page.wait_for_timeout(5000)

    # Email/password khud bhar do, baaki (OTP etc.) aap browser me karo
    try:
        page.locator("#usernameField, input[placeholder*='Email' i]").first.fill(USER, timeout=20000)
        page.locator("input[type='password']").first.fill(PASS, timeout=10000)
        page.locator("button[type='submit'], .btn-login, .btn-primary").first.click()
    except Exception:
        print("Auto-fill nahi hua, browser me khud login karo.")

    print("\nBrowser me login poora karo (OTP aaye to email se daalo).")
    print("Naukri ka dashboard dikhne tak wait kar raha hoon (max 5 min)...")
    page.wait_for_function(
        "() => !window.location.href.includes('nlogin')", timeout=300_000
    )
    page.wait_for_timeout(5000)
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if "nlogin" in page.url:
        sys.exit("Login poora nahi hua, dobara chalao.")

    context.storage_state(path=str(STATE))
    browser.close()

b64 = base64.b64encode(STATE.read_bytes()).decode()
try:
    subprocess.run(["pbcopy"], input=b64.encode(), check=True)
    print("\n✅ state.json save hua aur base64 clipboard me copy ho gaya.")
except Exception:
    Path("state.b64").write_text(b64)
    print("\n✅ state.json save hua. Base64 'state.b64' file me hai, uska content copy karo.")
print("Ab GitHub -> Settings -> Secrets -> Actions -> New secret:")
print("   Name: NAUKRI_STATE_B64   Value: (clipboard paste)")
