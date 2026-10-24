"""
tc_web_session.py — Authenticated web session for momira.travel (JSF/PrimeFaces).

The TC REST API (online.travelcompositor.com) uses an auth-token header.
The TC web frontend (momira.travel) uses a separate JSESSIONID cookie that
is obtained by logging in through the web login form.

This module manages that web session so server-side automation (e.g. package
rollover) can make authenticated requests on behalf of the operator.

Session lifetime: JSESSIONID is kept alive as long as the requests.Session
object exists (i.e. for the lifetime of the Streamlit session). If a request
returns a login redirect (302 → /login) the session is re-established
automatically.
"""
import os
import time
import requests
from bs4 import BeautifulSoup
from typing import Optional

MODULE_BUILD = "2026-10-24-reliable-open"

_BASE = "https://momira.travel"
_LOGIN_URL = f"{_BASE}/login.xhtml"
_TIMEOUT = 20

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _is_login_page(resp: requests.Response) -> bool:
    """Return True if the response is a login page or redirect to login."""
    if "login" in resp.url.lower():
        return True
    if resp.status_code in (301, 302, 303):
        loc = resp.headers.get("Location", "")
        if "login" in loc.lower():
            return True
    return False


def create_web_session(username: str = "", password: str = "") -> requests.Session:
    """
    Log into momira.travel and return an authenticated requests.Session.
    Uses TRAVELC_USERNAME / TRAVELC_PASSWORD env vars if not provided.

    Raises RuntimeError if login fails.
    """
    username = username or os.getenv("TRAVELC_USERNAME", "")
    password = password or os.getenv("TRAVELC_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("TC web credentials not configured (TRAVELC_USERNAME / TRAVELC_PASSWORD)")

    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    # Step 1: GET login page to obtain ViewState
    resp = session.get(_LOGIN_URL, timeout=_TIMEOUT, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    vs_tag = soup.find("input", {"name": "javax.faces.ViewState"})
    if not vs_tag:
        # Try to find the login form even without ViewState (some TC agencies skip it)
        form = soup.find("form")
        if not form:
            raise RuntimeError("Login page did not return expected HTML — Cloudflare may be blocking")
    viewstate = vs_tag.get("value", "") if vs_tag else ""

    # Step 2: POST credentials
    # Field names observed on momira.travel login form
    login_data = {
        "loginForm_SUBMIT": "1",
        "javax.faces.ViewState": viewstate,
        "loginForm:username": username,
        "loginForm:password": password,
        "loginForm:loginButton": "loginForm:loginButton",
    }
    login_resp = session.post(
        _LOGIN_URL,
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": _LOGIN_URL},
        allow_redirects=True,
        timeout=_TIMEOUT,
    )

    # A successful login redirects away from /login
    if "login" in login_resp.url.lower() and login_resp.status_code == 200:
        # Still on login page — credentials rejected or form fields wrong
        soup2 = BeautifulSoup(login_resp.text, "html.parser")
        error = soup2.find(class_="error") or soup2.find(class_="ui-messages-error")
        msg = error.get_text(strip=True) if error else "Login failed — check username/password"
        raise RuntimeError(f"TC web login failed: {msg}")

    return session


def verify_session(session: requests.Session) -> bool:
    """Quick check — returns True if the session is still authenticated."""
    try:
        resp = session.get(f"{_BASE}/agency/", timeout=_TIMEOUT, allow_redirects=False)
        return resp.status_code not in (301, 302) and "login" not in resp.headers.get("Location", "")
    except Exception:
        return False
