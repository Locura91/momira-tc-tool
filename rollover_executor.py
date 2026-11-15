"""
rollover_executor.py — Package rollover via Playwright (headless Chromium).

Uses a real browser to bypass Cloudflare protection that blocks raw HTTP requests.
Flow:
  1. Launch headless Chromium, navigate to momira.travel login
  2. Log in with TC operator credentials
  3. Navigate to the idea/package page
  4. Click "Book directly without changes"
  5. Set departure date in the date picker
  6. Click "Start trip" → extract tripId from the resulting page
"""
import os
import re

MODULE_BUILD = "2026-11-11-playwright"

_BASE = "https://momira.travel"
_LOGIN_URL = f"{_BASE}/login.xhtml"

_BOOK_BUTTON_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "direkt buchen ohne änderungen",
    "réserver directement sans modifications",
]


def execute_rollover(
    idea_url: str,
    departure_date: str,   # DD.MM.YYYY
    flight_class: str = "ECONOMY",
) -> dict:
    """
    Execute a package rollover using Playwright headless Chromium.

    Returns:
        {"success": True,  "url": "...", "trip_id": "N"}
        {"success": False, "error": "...", "detail": "..."}
    """
    username = os.getenv("TRAVELC_USERNAME", "")
    password = os.getenv("TRAVELC_PASSWORD", "")
    if not username or not password:
        return {"success": False, "error": "TC web credentials not configured (TRAVELC_USERNAME / TRAVELC_PASSWORD)"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "Playwright is not installed on this server."}

    browser = None
    pw = None
    try:
        pw = sync_playwright().start()
        chromium_path = os.getenv("CHROMIUM_PATH", "")
        launch_kwargs = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        }
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()

        # ── Step 1: Login ────────────────────────────────────────────────
        page.goto(_LOGIN_URL, wait_until="networkidle", timeout=30000)

        username_field = page.locator("input[id*='username'], input[name*='username']").first
        password_field = page.locator("input[id*='password'], input[name*='password']").first
        login_button = page.locator("button[id*='loginButton'], input[id*='loginButton'], a[id*='loginButton']").first

        if not username_field.is_visible(timeout=10000):
            return {"success": False, "error": "Login page did not load — Cloudflare may be blocking",
                    "detail": page.url}

        username_field.fill(username)
        password_field.fill(password)
        login_button.click()

        page.wait_for_load_state("networkidle", timeout=15000)

        if "login" in page.url.lower():
            error_el = page.locator(".error, .ui-messages-error").first
            msg = error_el.text_content() if error_el.is_visible() else "Login failed"
            return {"success": False, "error": f"TC login failed: {msg}"}

        # ── Step 2: Navigate to the idea page ────────────────────────────
        page.goto(idea_url, wait_until="networkidle", timeout=30000)

        # ── Step 3: Click "Book directly without changes" ────────────────
        book_btn = None
        for text in _BOOK_BUTTON_TEXTS:
            candidates = page.locator(f"a:has-text('{text}'), button:has-text('{text}'), span:has-text('{text}')")
            if candidates.count() > 0:
                book_btn = candidates.first
                break

        if not book_btn:
            btn_by_id = page.locator("[id*='directBook']")
            if btn_by_id.count() > 0:
                book_btn = btn_by_id.first

        if not book_btn:
            return {"success": False, "error": "Could not find 'Book directly without changes' button",
                    "detail": f"URL: {page.url}"}

        book_btn.click()
        page.wait_for_timeout(2000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # ── Step 4: Set departure date ───────────────────────────────────
        date_input = page.locator("input[id*='departureDateMultidestination'], input[id*='departureDate']").first
        if not date_input.is_visible(timeout=5000):
            date_input = page.locator("input[id*='departure']").first

        if not date_input.is_visible(timeout=5000):
            return {"success": False, "error": "Departure date field not found after clicking 'Book directly'",
                    "detail": f"URL: {page.url}"}

        date_input.click(click_count=3)
        date_input.fill(departure_date)
        date_input.press("Tab")
        page.wait_for_timeout(500)

        # ── Step 5: Click "Start trip" / search ──────────────────────────
        start_btn = page.locator("[id*='startTrip']").first
        if not start_btn.is_visible(timeout=3000):
            start_btn = page.locator("button:has-text('Start'), a:has-text('Start'), button:has-text('Buscar'), a:has-text('Search')").first

        if not start_btn.is_visible(timeout=3000):
            return {"success": False, "error": "Could not find 'Start trip' button",
                    "detail": f"URL: {page.url}"}

        start_btn.click()
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # ── Step 6: Extract tripId ───────────────────────────────────────
        final_url = page.url
        if "tripId=" in final_url:
            trip_id = final_url.split("tripId=")[1].split("&")[0]
            return {"success": True, "url": final_url, "trip_id": trip_id}

        m = re.search(r"tripId=(\d+)", page.content())
        if m:
            trip_url = f"{_BASE}/summary.xhtml?tripId={m.group(1)}"
            return {"success": True, "url": trip_url, "trip_id": m.group(1)}

        m2 = re.search(r"travel-map\.xhtml\?tripId=(\d+)", page.content())
        if m2:
            trip_url = f"{_BASE}/travel-map.xhtml?tripId={m2.group(1)}"
            return {"success": True, "url": trip_url, "trip_id": m2.group(1)}

        return {
            "success": False,
            "error": "Rollover completed but no tripId found in response",
            "detail": f"Final URL: {final_url}, page title: {page.title()}",
        }

    except Exception as e:
        return {"success": False, "error": f"Playwright error: {e}"}
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()
