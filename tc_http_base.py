"""
tc_http_base.py — shared low-level HTTP plumbing for Travel Compositor's two independently
maintained client classes (api_client.TravelCompositorAPI, used by the Upload & Update tool, and
travelcompositor_api.TravelCompositorAPI, used by the translation-sync engine - see
translation_tool.py's "NOTE ON THE TWO API CLIENTS" for why the two classes are deliberately kept
separate).

CONSOLIDATED 2026-10-02 (weekly duplicate-code audit; product-owner request, verbatim: "please
start the weekly check ... with the focus on duplicates ... we need to check completely"). The
two classes' ENDPOINT methods (get_closed_tour, create_ticket, resolve_destination, ...) are
deliberately NOT shared here - they serve different callers (the upload tool vs. the sync engine)
and are meant to keep evolving independently, exactly as the 2026-09-27 consolidation of their
duplicated response-handling already respected. But their low-level HTTP transport - auth, token
refresh, retry-on-transient-failure, network-error handling, and the generic
response-to-dict/list conversion - was a ~150-line byte-for-byte fork, not a deliberate
divergence: travelcompositor_api.py's own prior docstring on _request said its retry policy was
"ported here rather than re-derived, so both clients treat Travel Compositor's transient-failure
behavior identically" - i.e. the project already wanted this one layer to move in lockstep, it
was just doing so by hand-copying (and had already drifted once, per that same docstring, before
being re-synced on 2026-09-13). This base class makes that lockstep structural instead of a habit
that can be forgotten.

One real difference found and folded in here: api_client.py's _handle_response/
_handle_list_response guarded res.json() against a malformed/truncated 2xx body (via _json());
travelcompositor_api.py's did not - its own prior docstring explicitly flagged this as an
accepted gap, not a considered choice ("this class has no _json() safe-parsing wrapper of its
own"). Both clients now get the same protection.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests


class TravelCompositorHTTPBase:
    """Not meant to be instantiated directly - api_client.TravelCompositorAPI and
    travelcompositor_api.TravelCompositorAPI each subclass this for the shared plumbing below,
    then add their own endpoint methods and (for api_client.py) their own extra cache fields."""

    def __init__(self):
        self.api_base_url = os.getenv("TRAVELC_BASE_URL", "https://online.travelcompositor.com/resources").rstrip("/")
        self.microsite_id = os.getenv("TRAVELC_MICROSITE_ID", "momiratravel")
        self.username = os.getenv("TRAVELC_USERNAME", "")
        self.password = os.getenv("TRAVELC_PASSWORD", "")
        self.auth_token: Optional[str] = None
        self._destination_cache: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # AUTH
    # ------------------------------------------------------------------
    def authenticate(self, force: bool = False) -> str:
        """
        Logs in via POST /authentication/authenticate to obtain an active auth-token.
        Set force=True to bypass the cached token and get a fresh one (e.g. after a 401).
        """
        if self.auth_token and not force:
            return self.auth_token

        url = f"{self.api_base_url}/authentication/authenticate"
        payload = {
            "username": self.username,
            "password": self.password,
            "micrositeId": self.microsite_id
        }
        headers = {"Content-Type": "application/json"}

        print(f"🔑 Authenticating via POST {url}...")
        res = requests.post(url, json=payload, headers=headers, timeout=10)

        if res.status_code == 200:
            self.auth_token = res.headers.get("auth-token") or res.headers.get("Auth-Token")
            if not self.auth_token and res.text:
                try:
                    data = res.json()
                    self.auth_token = data.get("token") or data.get("authToken") or data.get("auth-token")
                except Exception:
                    self.auth_token = res.text.strip('"')

            print("✅ Auth successful! Token acquired.")
            return self.auth_token
        else:
            print(f"❌ Auth failed (Status {res.status_code}): {res.text}")
            res.raise_for_status()

    def get_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            self.authenticate()
        return {
            "auth-token": self.auth_token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    # CONFIRMED REAL ISSUE (internal audit): retrying on ANY status >= 400
    # meant a genuine validation error (e.g. 400 "modality code cannot
    # contain '/'", 404 "closed tour not found", 409 "code already taken")
    # got retried 6 times / ~10-12s with the exact same payload before the
    # human ever saw it - those are FINAL answers, not transient hiccups,
    # since retrying an unchanged payload against the same validation rule
    # can never succeed. Worse, blanket-retrying a CREATE call on any error
    # risks creating a DUPLICATE resource if the first attempt actually
    # succeeded server-side but the success response was lost/timed-out
    # client-side (a real double-booking risk for a POST create, not just a
    # wasted wait). Only retry on codes that genuinely mean "try again
    # later, nothing about the request itself was wrong": 408 (request
    # timeout), 429 (rate limited), and 500/502/503/504 (server-side
    # transient failure) - the "eventual-consistency lag right after a
    # related object was just created" scenario this retry was originally
    # added for shows up as one of these, not as a 400/404/409.
    # 599 is not a real HTTP status - it's a synthetic marker (see
    # _network_error_response) meaning "the request never got a real HTTP
    # response at all" (timeout, DNS failure, connection refused, SSL
    # error, ...), included here so a raised network exception gets the
    # exact same transient-retry treatment as a 5xx from the server.
    _TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504, 599}

    @staticmethod
    def _network_error_response(exc: Exception) -> requests.Response:
        """
        CONFIRMED REAL GAP (internal audit): requests.request() itself was
        called completely unguarded - a genuine network-level failure
        (timeout, DNS failure, connection refused, SSL error - anything
        that means the request never even reached the server, as opposed to
        the server responding with an error status) raised straight through
        _request() uncaught, crashing the WHOLE Streamlit page with a raw
        traceback and losing any in-progress edits, instead of the clean
        "{'error': ..., 'message': ...}" dict every get_*/create_*/update_*
        method's caller already expects and handles gracefully.

        Rather than adding a try/except at every one of the ~20 call sites
        across this file (easy to miss one, as an audit already did), this
        builds a real requests.Response with a synthetic 599 status code
        (a conventional-but-non-standard code meaning "network error, no
        real HTTP response") and a JSON body shaped exactly like a normal
        API error response - every existing caller's `if res.status_code
        != 200: return {"error": res.status_code, "message": res.text}`
        keeps working completely unchanged, and _request's own retry loop
        (see _TRANSIENT_STATUS_CODES) treats it as transient automatically.
        """
        res = requests.Response()
        res.status_code = 599
        res._content = json.dumps({
            "error": "network_error",
            "message": f"{type(exc).__name__}: {exc}",
        }).encode("utf-8")
        return res

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Wraps requests.request() with:
          1. Automatic re-authentication if the token has expired (401) -
             without this, an expired token mid-session looks like a random
             "connection failure" instead of an auth issue.
          2. For WRITE calls (POST/PUT) only: automatic retry on a TRANSIENT
             failure (see _TRANSIENT_STATUS_CODES - up to 6 attempts, 2s
             apart). A NON-transient write failure (400/404/409/422/etc - a
             genuine problem with the request itself) returns immediately on
             the first attempt instead of being retried - see
             _TRANSIENT_STATUS_CODES's docstring for why.
             Retries deliberately NOT applied to GET calls: those are often
             used as fast "does this exist" checks where a real 404/4xx is an
             expected, final answer, not a transient failure worth retrying
             6 times (~12s) for.
          3. A raised network-level exception (timeout, DNS failure,
             connection refused, SSL error - see _network_error_response)
             is caught and converted into a synthetic error Response rather
             than propagating uncaught - every caller already handles a
             non-200 Response gracefully, so this closes off an entire
             class of "network blip crashes the whole page" failures
             without needing a try/except at every individual call site.
        """
        kwargs.setdefault("timeout", 15)
        # Callers may pass extra headers (e.g. get_closed_tours'/get_tickets'
        # pagination 'first'/'limit' headers) - merge them with the real
        # auth headers rather than overwriting, and re-merge fresh every
        # attempt so a re-authenticated token is always actually used.
        extra_headers = kwargs.pop("headers", None) or {}
        is_write = method.upper() in ("POST", "PUT")
        max_attempts = 6 if is_write else 1
        last_res = None

        for attempt in range(max_attempts):
            try:
                res = requests.request(method, url, headers={**self.get_headers(), **extra_headers}, **kwargs)
            except requests.exceptions.RequestException as e:
                res = self._network_error_response(e)

            if res.status_code == 401:
                print("♻️  Auth token expired/rejected — re-authenticating and retrying once...")
                self.authenticate(force=True)
                try:
                    res = requests.request(method, url, headers={**self.get_headers(), **extra_headers}, **kwargs)
                except requests.exceptions.RequestException as e:
                    res = self._network_error_response(e)

            if res.status_code < 400:
                return res

            last_res = res
            is_transient = res.status_code in self._TRANSIENT_STATUS_CODES
            if is_write and is_transient and attempt < max_attempts - 1:
                print(f"⚠️ {method} {url} returned {res.status_code} (transient) "
                      f"(attempt {attempt + 1}/{max_attempts}) - retrying in 2s...")
                time.sleep(2)
            elif is_write and not is_transient:
                # Final answer - retrying an unchanged payload against the
                # same validation error can never succeed, so fail fast
                # instead of burning ~10-12s the human is waiting on.
                break

        return last_res

    def _json(self, res: requests.Response) -> Any:
        """CONFIRMED FIX (2026-08-19 audit, originally api_client.py-only - folded into this
        shared base 2026-10-02 so travelcompositor_api.py's client gets the same protection,
        closing a gap its own prior docstring explicitly flagged as accepted-but-not-deliberate):
        every call site used to call res.json() directly with no guard. _network_error_response
        already turns a request that never got a real HTTP response into a synthetic error
        Response every caller handles gracefully - but a 2xx response with a malformed or
        truncated body (a proxy hiccup, an empty body) is a different failure from the SAME
        class, and used to raise json.JSONDecodeError straight through to a raw traceback on
        screen instead of a friendly error. Centralized here so every .json() call gets the same
        treatment without a try/except at each site."""
        try:
            return res.json()
        except ValueError as e:
            raise RuntimeError(
                f"Travel Compositor returned a {res.status_code} response that wasn't valid "
                f"JSON ({e}). Response body (first 300 chars): {res.text[:300]!r}"
            ) from e

    def _handle_response(self, res: requests.Response, ok_codes=(200,)) -> Any:
        """CONSOLIDATED 2026-09-27 (within each file), then moved into this shared base
        2026-10-02: the "check status, print+return an error dict, otherwise parse JSON" pattern
        used to be hand-copied at ~30-40 get_*/create_*/update_* call sites PER FILE - identical
        except which status codes count as success (GET/most calls: only 200; POST/PUT
        create/update calls: 200 or 201). Behavior is UNCHANGED from before this refactor for
        both callers: same "\\n❌ API Error (...)" message, same
        {"error": status_code, "message": text} shape on failure, same JSON-decode-guarded
        success path (see _json's docstring for the one behavior change this folds in for
        travelcompositor_api.py's caller)."""
        if res.status_code not in ok_codes:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def _handle_list_response(self, res: requests.Response) -> List[Dict[str, Any]]:
        """Same consolidation as _handle_response, for endpoints whose caller expects a bare
        list back - [] (not an {"error": ...} dict) on failure or on a non-list response body,
        exactly as before this refactor."""
        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return []
        data = self._json(res)
        return data if isinstance(data, list) else []
