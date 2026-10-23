"""
rollover_executor.py — Package rollover via two-step PrimeFaces AJAX.

Flow confirmed live by browser inspection (2026-10-23):
  1. Log into momira.travel as the TC operator → get JSESSIONID
  2. GET idea page (authenticated) → extract initial ViewState
  3. POST AJAX: trigger "Book directly without changes" button
     → server refreshes mySeparateForm + returns new ViewState
  4. POST AJAX: submit mySeparateForm (startTrip) with departure date
     → server redirects to summary.xhtml?tripId=N
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from tc_web_session import create_web_session

MODULE_BUILD = "2026-10-23-web-auth"

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
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_AJAX_HEADERS = {
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}


def _extract_viewstate_from_ajax(xml_text: str) -> str:
    """Extract updated ViewState from a PrimeFaces partial-response XML body."""
    # Try xml.etree first
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
        for update in root.iter("update"):
            uid = update.get("id", "")
            if "ViewState" in uid or uid == "j_id__v_0":
                return (update.text or "").strip()
    except Exception:
        pass
    # Regex fallbacks
    for pat in (
        r'<update id="javax\.faces\.ViewState[^"]*"><!\[CDATA\[([^\]]+)\]\]>',
        r'<update id="javax\.faces\.ViewState[^"]*">([^<]+)<',
        r'<update id="j_id__v_0"><!\[CDATA\[([^\]]+)\]\]>',
    ):
        m = re.search(pat, xml_text)
        if m:
            return m.group(1).strip()
    return ""


def execute_rollover(
    idea_url: str,
    departure_date: str,   # DD.MM.YYYY
    flight_class: str = "ECONOMY",
) -> dict:
    """
    Execute a package rollover via two PrimeFaces AJAX calls.

    Returns:
        {"success": True,  "url": "https://momira.travel/summary.xhtml?tripId=N", "trip_id": "N"}
        {"success": False, "error": "...", "detail": "..."}
    """
    # ── Step 0: Authenticate — get a TC web session (JSESSIONID) ────────────
    try:
        session = create_web_session()
        # Override default headers with our AJAX-compatible ones
        session.headers.update(_HEADERS)
    except Exception as e:
        return {"success": False, "error": f"TC login failed: {e}"}

    # ── Step 1: GET the idea page ─────────────────────────────────────────────
    for attempt in range(3):
        try:
            resp = session.get(idea_url, timeout=_TIMEOUT_GET, allow_redirects=True)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                return {"success": False, "error": f"Could not load page: {e}"}
            time.sleep(2 ** attempt)
    else:
        return {"success": False, "error": "Page blocked after 3 attempts (429)"}

    page_url = resp.url  # final URL after redirects
    soup = BeautifulSoup(resp.text, "html.parser")
    vs_tag = soup.find("input", {"name": "javax.faces.ViewState"})
    if not vs_tag:
        return {"success": False, "error": "ViewState not found — page may require login or layout changed"}
    viewstate = vs_tag.get("value", "")

    # ── Step 2: AJAX — trigger "Book directly without changes" ────────────────
    # This loads/updates mySeparateForm server-side and returns a fresh ViewState.
    try:
        ajax1 = session.post(
            page_url,
            data={
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": "idea-info-form:j_id_3r_4y:directBook",
                "javax.faces.partial.execute": "idea-info-form",
                "javax.faces.partial.render": "@(#mySeparateForm),carousel:destinations-photos",
                "idea-info-form:j_id_3r_4y:directBook": "idea-info-form:j_id_3r_4y:directBook",
                "idea-info-form_SUBMIT": "1",
                "javax.faces.ViewState": viewstate,
            },
            headers={**_AJAX_HEADERS, "Referer": page_url},
            timeout=_TIMEOUT_POST,
        )
    except Exception as e:
        return {"success": False, "error": f"Step 2 AJAX failed: {e}"}

    # Use refreshed ViewState from AJAX response if available
    fresh_vs = _extract_viewstate_from_ajax(ajax1.text)
    if fresh_vs:
        viewstate = fresh_vs

    # ── Step 3: AJAX — submit mySeparateForm with departure date ──────────────
    try:
        ajax2 = session.post(
            page_url,
            data={
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": "modal-init-compositor-all:j_id_53_f_1xa:startTrip",
                "javax.faces.partial.execute": "mySeparateForm",
                "javax.faces.partial.render": "mySeparateForm",
                "mySeparateForm": "mySeparateForm",
                "modal-init-compositor-all:j_id_53_f_1sv:ClassOption": flight_class,
                "modal-init-compositor-all:departureDateMultidestination:input": departure_date,
                "modal-init-compositor-all:directSubmit": "",
                "mySeparateForm_SUBMIT": "1",
                "javax.faces.ViewState": viewstate,
            },
            headers={**_AJAX_HEADERS, "Referer": page_url},
            allow_redirects=True,
            timeout=_TIMEOUT_POST,
        )
    except Exception as e:
        return {"success": False, "error": f"Step 3 AJAX failed: {e}"}

    # ── Step 4: Extract tripId ────────────────────────────────────────────────
    final_url = ajax2.url
    if "tripId=" in final_url:
        trip_id = final_url.split("tripId=")[1].split("&")[0]
        return {"success": True, "url": final_url, "trip_id": trip_id}

    # PrimeFaces may return a <redirect> element in the XML response
    redirect_match = re.search(r'<redirect url="([^"]+)"', ajax2.text)
    if redirect_match:
        redir = redirect_match.group(1)
        full = redir if redir.startswith("http") else _BASE + redir
        if "tripId=" in full:
            trip_id = full.split("tripId=")[1].split("&")[0]
            return {"success": True, "url": full, "trip_id": trip_id}

    # Check body for embedded tripId
    m = re.search(r"summary\.xhtml\?tripId=(\d+)", ajax2.text)
    if m:
        return {"success": True, "url": f"{_BASE}/summary.xhtml?tripId={m.group(1)}", "trip_id": m.group(1)}

    return {
        "success": False,
        "error": "No tripId in response — booking may require TC login or field names changed",
        "detail": ajax2.text[:800],
    }
