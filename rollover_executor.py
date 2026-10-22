"""
rollover_executor.py — Server-side package rollover via momira.travel JSF form.

Flow:
1. GET the idea page → extract javax.faces.ViewState
2. POST the search form with the chosen departure date
3. Follow redirect → extract tripId from summary.xhtml?tripId=N
4. Return the summary URL — caller opens it in a new tab

No browser automation, no bookmarklet. Pure requests + BeautifulSoup.
"""
import re
import requests
from bs4 import BeautifulSoup

MODULE_BUILD = "2026-10-22-rollover-executor"

_BASE = "https://momira.travel"
_TIMEOUT_GET  = 15
_TIMEOUT_POST = 30

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def execute_rollover(
    idea_id: str,
    departure_date: str,   # DD.MM.YYYY
    idea_url: str = "",    # full canonical URL preferred; falls back to /en/idea/{id}/
    adults: int = 2,
    rooms: int = 1,
) -> dict:
    """
    Execute a package rollover server-side.

    Returns:
        {"success": True,  "url": "https://momira.travel/summary.xhtml?tripId=N", "trip_id": "N"}
        {"success": False, "error": "...", "final_url": "..."}
    """
    session = requests.Session()
    session.headers.update(_HEADERS)

    # Prefer the canonical ideaUrl from the TC API response (already has correct slug).
    # Fall back to bare ID URL if not available — momira.travel may redirect to slug.
    page_url = idea_url.rstrip("/") + "/" if idea_url else f"{_BASE}/en/idea/{idea_id}/"

    # ── Step 1: GET the idea page ─────────────────────────────────────────────
    try:
        resp = session.get(page_url, timeout=_TIMEOUT_GET, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Could not load idea page: {e}", "tried_url": page_url}

    # Use the final URL after any redirects for the POST
    page_url = resp.url

    soup = BeautifulSoup(resp.text, "html.parser")

    viewstate_tag = soup.find("input", {"name": "javax.faces.ViewState"})
    if not viewstate_tag:
        return {"success": False, "error": "javax.faces.ViewState not found on page — page layout may have changed"}
    viewstate = viewstate_tag.get("value", "")

    # ── Step 2: POST the search form ──────────────────────────────────────────
    # Field names captured from momira.travel DevTools (POST body of the
    # "Book directly without changes" click on the search modal).
    post_data = {
        "mySeparateForm_SUBMIT": "1",
        "javax.faces.ViewState": viewstate,
        "modal-init-compositor-all:departureDateMultidestination:input": departure_date,
        "modal-init-compositor-all:j_id_53_f_1sv:ClassOption": "ECONOMY",
        "modal-init-compositor-all:directSubmit": "",
    }

    try:
        resp2 = session.post(
            page_url,
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": page_url},
            allow_redirects=True,
            timeout=_TIMEOUT_POST,
        )
    except Exception as e:
        return {"success": False, "error": f"POST failed: {e}"}

    final_url = resp2.url

    # ── Step 3: Extract tripId ────────────────────────────────────────────────
    if "tripId=" in final_url:
        trip_id = final_url.split("tripId=")[1].split("&")[0]
        return {"success": True, "url": final_url, "trip_id": trip_id}

    # Sometimes the redirect URL is embedded in the response body
    match = re.search(r"summary\.xhtml\?tripId=(\d+)", resp2.text)
    if match:
        trip_id = match.group(1)
        return {"success": True, "url": f"{_BASE}/summary.xhtml?tripId={trip_id}", "trip_id": trip_id}

    return {
        "success": False,
        "error": "No tripId found in response — the form POST may have failed or field names changed",
        "final_url": final_url,
    }
