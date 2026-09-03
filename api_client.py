import os
import time
import json
import difflib
import re
import requests
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

load_dotenv()

# CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this module - the actual publish path
# for every product type - had no MODULE_BUILD stamp at all, so app.py's own partial-deploy
# detector (_module_build_mismatches) was structurally blind to it: a stale api_client.py could
# sit alongside a freshly-deployed app.py with no warning, even though it's the single most
# consequential file to have out of sync (every publish call goes through it). Stamped now, and
# the detector's module list is auto-discovered (see app.py) so any future module that adds a
# MODULE_BUILD is picked up automatically instead of needing a second hand-maintained list entry.
MODULE_BUILD = "2026-09-03-time-window-fix-what-to-bring-duration-unit"


class TravelCompositorAPI:
    """
    Single, shared client for all Travel Compositor API interactions:
    authentication, destination resolution, and closed-tour uploads.

    This replaces the destination-resolution logic that used to be
    duplicated (and inconsistent) across main.py, get_tc_destinations.py,
    and step2_parser.py.
    """

    def __init__(self):
        self.api_base_url = os.getenv("TRAVELC_BASE_URL", "https://online.travelcompositor.com/resources").rstrip("/")
        self.microsite_id = os.getenv("TRAVELC_MICROSITE_ID", "momiratravel")
        self.username = os.getenv("TRAVELC_USERNAME", "")
        self.password = os.getenv("TRAVELC_PASSWORD", "")
        self.auth_token: Optional[str] = None
        self._destination_cache: Optional[List[Dict[str, Any]]] = None
        self._transfer_zone_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._transport_base_cache: Optional[List[Dict[str, Any]]] = None

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
             apart). This used to be a hand-written loop duplicated in
             app.py around ONLY create_ticket_option and
             create_closed_tour_option - confirmed decision was to extend
             the same protection to every write call (create_ticket,
             create_closed_tour, update_ticket, update_ticket_option,
             update_closed_tour, update_closed_tour_option too), since
             Travel Compositor's API can return a transient failure right
             after a related object was just created (eventual-consistency
             lag). Centralizing it here - instead of leaving app.py's old
             loops in place - avoids retrying twice (once in app.py, once
             here) and multiplying the attempt count/wait time.
          A NON-transient write failure (400/404/409/422/etc - a genuine
          problem with the request itself) returns immediately on the first
          attempt instead of being retried - see _TRANSIENT_STATUS_CODES's
          docstring for why.
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
        """CONFIRMED FIX (2026-08-19 audit): every call site used to call res.json() directly
        with no guard. _network_error_response already turns a request that never got a real
        HTTP response into a synthetic error Response every caller handles gracefully - but a
        2xx response with a malformed or truncated body (a proxy hiccup, an empty body) is a
        different failure from the SAME class, and used to raise json.JSONDecodeError straight
        through to a raw traceback on screen instead of a friendly error. Centralized here so
        every .json() call gets the same treatment without a try/except at each site."""
        try:
            return res.json()
        except ValueError as e:
            raise RuntimeError(
                f"Travel Compositor returned a {res.status_code} response that wasn't valid "
                f"JSON ({e}). Response body (first 300 chars): {res.text[:300]!r}"
            ) from e

    # ------------------------------------------------------------------
    # DESTINATIONS  (the consolidated, correct resolver)
    # ------------------------------------------------------------------
    def _get_all_destinations(self, lang: str = "EN") -> List[Dict[str, Any]]:
        """
        Fetches and caches the FULL destination list for the microsite.
        The Travel Compositor API has NO free-text search parameter
        (only countryCode / iata filters exist) - this is the only
        reliable way to match destinations by name.
        """
        if self._destination_cache is not None:
            return self._destination_cache

        url = f"{self.api_base_url}/destination/{self.microsite_id}"
        res = self._request("GET", url, params={"lang": lang})
        res.raise_for_status()
        data = self._json(res)

        destinations = data.get("destination", []) if isinstance(data, dict) else data
        self._destination_cache = destinations or []
        print(f"📥 Cached {len(self._destination_cache)} destinations for '{self.microsite_id}'.")
        return self._destination_cache

    def get_destination_country(self, query_term: str) -> Optional[str]:
        """
        Looks up a destination's own 'country' field (per Travel Compositor's
        DestinationVO schema) using the SAME cached full destination list
        already fetched by resolve_destination()/_get_all_destinations() - no
        extra network call. This is Travel Compositor's own authoritative
        country data (used to power business rules like the Indonesia/Vesak
        Day stop-sale default), preferred over free-text geocoding services
        when a match exists. Mirrors resolve_destination()'s own match order
        (exact name, then substring). Returns None if no match is found or
        the matched record has no country set - callers should fall back to
        another signal (e.g. OpenStreetMap) in that case, since Travel
        Compositor's own destination list won't include every small place a
        DMC document might mention.
        """
        clean_query = (query_term or "").strip()
        if not clean_query:
            return None
        try:
            destinations = self._get_all_destinations()
        except requests.RequestException:
            return None

        query_lower = clean_query.lower()
        for dest in destinations:
            if dest.get("name", "").strip().lower() == query_lower:
                return dest.get("country")
        for dest in destinations:
            if query_lower in dest.get("name", "").lower():
                return dest.get("country")
        return None

    def find_destinations_in_text(self, text: str, min_name_length: int = 4) -> List[Dict[str, Any]]:
        """
        Scans arbitrary text (e.g. a scraped web page heading or paragraph)
        for mentions of any real Travel Compositor destination name, using
        the full cached destination list. Matches on word boundaries to
        avoid false positives from short/common names.

        Returns matches in the order their destination NAME first appears
        in the text (useful for reconstructing itinerary order).
        """
        import re
        destinations = self._get_all_destinations()
        text_lower = text.lower()

        candidates = []
        for dest in destinations:
            name = dest.get("name", "")
            code = dest.get("code")
            if not code or len(name) < min_name_length:
                continue
            match = re.search(r'\b' + re.escape(name.lower()) + r'\b', text_lower)
            if match:
                candidates.append((match.start(), code, name))

        candidates.sort(key=lambda c: c[0])
        seen = set()
        results = []
        for _, code, name in candidates:
            if code not in seen:
                seen.add(code)
                results.append({"code": code, "name": name})
        return results

    def resolve_destination_geolocation(self, query_term: str) -> Dict[str, Any]:
        """
        Reuses the SAME destination search/cache as resolve_destination(), but
        for Tickets, which need latitude/longitude instead of a destination code.

        NOT YET CONFIRMED against live data whether Travel Compositor's
        destination records actually include coordinates - this attempts a
        few common field name variants (latitude/longitude, lat/lng, lat/lon)
        and clearly reports if none were found, so the human knows they may
        need to enter coordinates manually as a fallback.

        Returns: {"latitude": float|None, "longitude": float|None, "name": str,
                   "valid": bool, "source": str}
        """
        clean_query = (query_term or "").strip()
        if not clean_query:
            return {"latitude": None, "longitude": None, "name": None, "valid": False, "source": "empty_query"}

        def _extract_coords(d: dict):
            for lat_key, lng_key in [("latitude", "longitude"), ("lat", "lng"), ("lat", "lon")]:
                if d.get(lat_key) is not None and d.get(lng_key) is not None:
                    try:
                        return float(d[lat_key]), float(d[lng_key])
                    except (TypeError, ValueError):
                        continue
            return None, None

        # 1. Direct code lookup (in case the human already has a TC destination code)
        code_candidate = clean_query.upper()
        url_direct = f"{self.api_base_url}/destination/{self.microsite_id}/{code_candidate}"
        try:
            res = self._request("GET", url_direct, params={"lang": "EN"})
            if res.status_code == 200:
                data = self._json(res)
                if isinstance(data, dict) and data.get("code"):
                    lat, lng = _extract_coords(data)
                    return {
                        "latitude": lat, "longitude": lng, "name": data.get("name", clean_query),
                        "valid": lat is not None and lng is not None, "source": "direct_code"
                    }
        except requests.RequestException:
            pass

        # 2. Name search against the cached full list (same cache resolve_destination uses)
        try:
            destinations = self._get_all_destinations()
        except requests.RequestException:
            destinations = []

        query_lower = clean_query.lower()
        for dest in destinations:
            if dest.get("name", "").strip().lower() == query_lower:
                lat, lng = _extract_coords(dest)
                return {
                    "latitude": lat, "longitude": lng, "name": dest.get("name"),
                    "valid": lat is not None and lng is not None, "source": "exact_name"
                }
        matches = [d for d in destinations if query_lower in d.get("name", "").lower()]
        if matches:
            lat, lng = _extract_coords(matches[0])
            return {
                "latitude": lat, "longitude": lng, "name": matches[0].get("name"),
                "valid": lat is not None and lng is not None, "source": "partial_name"
            }

        return {"latitude": None, "longitude": None, "name": clean_query, "valid": False, "source": "not_found"}

    # ------------------------------------------------------------------
    # TRANSFER ZONES  (real TC-native coordinates, but scoped to whatever
    # a given supplier has already set up - a supplementary geolocation
    # source, not a replacement for the free OpenStreetMap fallback below)
    # ------------------------------------------------------------------
    def get_transfer_zones(self, supplier_id: str, zone_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches and caches GET /transfer/zones/{supplierId} - the list of
        pickup/dropoff zones (ContractTransferZoneVO: id, geolocation,
        zoneType [AIRPORT|PORT|DESTINATION|POINT], name, code, terminal,
        zoneRadius) a TRANSFER supplier has configured.

        CONFIRMED SCOPE LIMIT: this is per-supplier data, not a general
        place database - a supplier that only sells tours/tickets (never
        transfers) will have no zones at all, and that's a completely
        normal/expected result, not an error. Any failure (404, timeout,
        non-2xx, malformed body) is swallowed and returns an empty list so
        callers can silently fall back to another geolocation source rather
        than crashing over what's an expected gap in coverage.

        Cached per (supplier_id, zone_type) for the life of this client
        instance, since the zone list for one supplier doesn't change
        mid-session and may be looked up repeatedly (once per Ticket city,
        once per meeting point, etc.).
        """
        cache_key = f"{supplier_id}::{zone_type or 'ALL'}"
        if cache_key in self._transfer_zone_cache:
            return self._transfer_zone_cache[cache_key]

        zones: List[Dict[str, Any]] = []
        try:
            url = f"{self.api_base_url}/transfer/zones/{supplier_id}"
            params = {"zoneType": zone_type} if zone_type else None
            res = self._request("GET", url, params=params)
            if res.status_code == 200:
                data = res.json()
                zones = data.get("zone", []) if isinstance(data, dict) else (data or [])
        except requests.RequestException:
            zones = []
        except ValueError:
            # res.json() failed to parse - treat exactly like "no zones found".
            zones = []

        self._transfer_zone_cache[cache_key] = zones or []
        print(f"📥 Cached {len(self._transfer_zone_cache[cache_key])} transfer zone(s) for supplier '{supplier_id}'"
              + (f" (zoneType={zone_type})" if zone_type else "") + ".")
        return self._transfer_zone_cache[cache_key]

    def _find_best_transfer_zone(self, supplier_id: str, query_term: str) -> Optional[Dict[str, Any]]:
        """
        Shared matching logic behind both resolve_transfer_zone_geolocation()
        (coordinates only, used by Tickets) and resolve_transfer_zone() (full
        zone dict including 'id', used by Transfers for zone-based/area
        routing) - factored out so both stay in sync instead of drifting.

        Matches on zone name (exact match first, then substring), searching
        ALL zone types together since a place name could legitimately be any
        of AIRPORT/PORT/DESTINATION/POINT - but exact matches against a
        DESTINATION zone are preferred when the same name matches more than
        one zone type. Returns the raw zone dict, or None if no match / no
        zones configured for this supplier.
        """
        clean_query = (query_term or "").strip()
        if not clean_query or not supplier_id:
            return None

        try:
            zones = self.get_transfer_zones(supplier_id)
        except Exception:
            zones = []
        if not zones:
            return None

        query_lower = clean_query.lower()

        def _rank(zone: dict) -> tuple:
            # Lower rank sorts first: exact match beats substring; DESTINATION
            # zoneType beats other types when otherwise tied.
            name = (zone.get("name") or "").strip().lower()
            exact = 0 if name == query_lower else 1
            is_destination = 0 if zone.get("zoneType") == "DESTINATION" else 1
            return (exact, is_destination)

        candidates = [z for z in zones if query_lower == (z.get("name") or "").strip().lower()
                      or query_lower in (z.get("name") or "").lower()]
        if not candidates:
            return None

        candidates.sort(key=_rank)
        return candidates[0]

    def resolve_transfer_zone_geolocation(self, supplier_id: str, query_term: str) -> Dict[str, Any]:
        """
        Tries to resolve a place name to real coordinates using the GIVEN
        supplier's own transfer zones, before the caller falls back to free
        OpenStreetMap geocoding (per the confirmed team decision: try
        Travel Compositor's own data first, only use the free geocoder as a
        fallback when TC has nothing for this specific supplier).

        Returns the SAME shape as resolve_destination_geolocation() /
        geocoding_client.geocode(), so callers can use either interchangeably:
        {"latitude": float|None, "longitude": float|None, "name": str,
         "valid": bool, "source": "transfer_zone"}
        """
        clean_query = (query_term or "").strip()
        if not clean_query or not supplier_id:
            return {"latitude": None, "longitude": None, "name": query_term, "valid": False, "source": "transfer_zone_skipped"}

        def _extract_coords(zone: dict):
            geo = zone.get("geolocation") or {}
            lat, lng = geo.get("latitude"), geo.get("longitude")
            if lat is None or lng is None:
                return None, None
            try:
                return float(lat), float(lng)
            except (TypeError, ValueError):
                return None, None

        best = self._find_best_transfer_zone(supplier_id, clean_query)
        if not best:
            zones_exist = bool(self.get_transfer_zones(supplier_id))
            return {"latitude": None, "longitude": None, "name": clean_query, "valid": False,
                    "source": "transfer_zone_not_found" if zones_exist else "transfer_zone_none_for_supplier"}

        lat, lng = _extract_coords(best)
        return {
            "latitude": lat, "longitude": lng, "name": best.get("name", clean_query),
            "valid": lat is not None and lng is not None, "source": "transfer_zone",
        }

    def resolve_transfer_zone(self, supplier_id: str, query_term: str) -> Dict[str, Any]:
        """
        Like resolve_transfer_zone_geolocation(), but also returns the zone's
        own TC 'id' - needed for TRANSFER products' departureLocationId/
        arrivalLocationId fields (zone-based/area routing, e.g. a Bali-style
        rate sheet where "South Bali (Tuban/Kuta/...)" is a named area rather
        than one specific GPS point - see builder.py's build_transfer_payload).

        Returns: {"zone_id": int|None, "latitude": float|None, "longitude": float|None,
                   "name": str, "zone_radius": float|None, "valid": bool, "source": str}
        """
        clean_query = (query_term or "").strip()
        if not clean_query or not supplier_id:
            return {"zone_id": None, "latitude": None, "longitude": None, "name": query_term,
                    "zone_radius": None, "valid": False, "source": "transfer_zone_skipped"}

        best = self._find_best_transfer_zone(supplier_id, clean_query)
        if not best:
            zones_exist = bool(self.get_transfer_zones(supplier_id))
            return {"zone_id": None, "latitude": None, "longitude": None, "name": clean_query,
                    "zone_radius": None, "valid": False,
                    "source": "transfer_zone_not_found" if zones_exist else "transfer_zone_none_for_supplier"}

        geo = best.get("geolocation") or {}
        try:
            lat = float(geo.get("latitude")) if geo.get("latitude") is not None else None
            lng = float(geo.get("longitude")) if geo.get("longitude") is not None else None
        except (TypeError, ValueError):
            lat, lng = None, None
        return {
            "zone_id": best.get("id"), "latitude": lat, "longitude": lng,
            "name": best.get("name", clean_query), "zone_radius": best.get("zoneRadius"),
            "valid": best.get("id") is not None, "source": "transfer_zone",
        }

    def resolve_destination(self, query_term: str) -> Dict[str, Any]:
        """
        Resolves ANY destination input:
          - Exact/custom codes (e.g. 'ASW', 'EDF-2') -> direct GET by ID
          - Free-text names (e.g. 'Edfu', 'Kom Ombo') -> local match against
            the cached full destination list (exact -> substring -> fuzzy)

        Returns: {"tc_code": str, "name": str, "valid": bool, ...}
        """
        clean_query = (query_term or "").strip()
        if not clean_query:
            return {"tc_code": None, "name": None, "valid": False}

        # 1. Direct code lookup
        code_candidate = clean_query.upper()
        url_direct = f"{self.api_base_url}/destination/{self.microsite_id}/{code_candidate}"
        try:
            res = self._request("GET", url_direct, params={"lang": "EN"})
            if res.status_code == 200:
                data = self._json(res)
                if isinstance(data, dict) and data.get("code"):
                    code, name = data["code"], data.get("name", data["code"])
                    print(f"✅ RESOLVED (by code): '{clean_query}' -> {code} ({name})")
                    return {"tc_code": code, "name": name, "valid": True, "match_type": "code"}
        except requests.RequestException as e:
            print(f"⚠️ Direct code lookup failed for '{clean_query}': {e}")

        # 2. Name matching against the cached full list
        try:
            destinations = self._get_all_destinations()
        except requests.RequestException as e:
            print(f"⚠️ Could not fetch destination list: {e}")
            destinations = []

        query_lower = clean_query.lower()

        # 2a. Exact name match
        for dest in destinations:
            if dest.get("name", "").strip().lower() == query_lower:
                code, name = dest.get("code"), dest.get("name")
                print(f"✅ RESOLVED (exact name): '{clean_query}' -> {code} ({name})")
                return {"tc_code": code, "name": name, "valid": True, "match_type": "exact_name"}

        # 2b. Substring match
        matches = [d for d in destinations if query_lower in d.get("name", "").lower()]
        if matches:
            best = matches[0]
            code, name = best.get("code"), best.get("name")
            print(f"✅ RESOLVED (partial name, {len(matches)} candidates): '{clean_query}' -> {code} ({name})")
            return {"tc_code": code, "name": name, "valid": True, "match_type": "partial_name", "alternatives": len(matches)}

        # 2c. Fuzzy fallback (typos)
        names = [d.get("name", "") for d in destinations if d.get("name")]
        close = difflib.get_close_matches(clean_query, names, n=1, cutoff=0.75)
        if close:
            best = next(d for d in destinations if d.get("name") == close[0])
            code, name = best.get("code"), best.get("name")
            print(f"✅ RESOLVED (fuzzy): '{clean_query}' -> {code} ({name})")
            return {"tc_code": code, "name": name, "valid": True, "match_type": "fuzzy"}

        print(f"⚠️ Destination '{clean_query}' not found anywhere. Flagging as invalid.")
        return {"tc_code": code_candidate, "name": clean_query, "valid": False, "match_type": "none"}

    def lookup_destination_code(self, destination_id: str) -> str:
        """
        Backwards-compatible shim so existing callers (e.g. builder.py)
        keep working. Internally now uses the full resolver instead of
        a bare direct-code-only GET.
        """
        result = self.resolve_destination(destination_id)
        return result["tc_code"]

    def resolve_destinations_bulk(self, terms: List[str]) -> List[Dict[str, Any]]:
        """Resolve a list of names/codes in one call, de-duplicated, in order."""
        results = []
        seen_codes = set()
        for term in terms:
            r = self.resolve_destination(term)
            if r["tc_code"] and r["tc_code"] not in seen_codes:
                seen_codes.add(r["tc_code"])
                results.append(r)
        return results

    def test_connection_destination(self, test_dest_id: str = "ASW") -> Dict[str, Any]:
        """Quick manual sanity check of the destination endpoint."""
        result = self.resolve_destination(test_dest_id)
        if result["valid"]:
            print(f"✅ Connection OK. {test_dest_id} -> {result['tc_code']} ({result['name']})")
        else:
            print(f"❌ Could not resolve '{test_dest_id}'.")
        return result

    # ------------------------------------------------------------------
    # CLOSED TOUR UPLOADS
    # ------------------------------------------------------------------
    def get_all_suppliers(self) -> List[Dict[str, Any]]:
        """
        Executes GET /suppliers — returns the full list of ContractSupplierVO
        for this operator (each has 'id', 'commercialName', 'legalName', etc).
        Used to build a human-friendly supplier picker instead of requiring
        people to know/type numeric supplier IDs by heart.
        """
        url = f"{self.api_base_url}/suppliers"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return []
        data = self._json(res)
        return data if isinstance(data, list) else []

    def get_all_users(self) -> List[Dict[str, Any]]:
        """
        Executes GET /user/{micrositeId} — returns real, formally-registered
        users for this microsite. Used to check whether a userId we send in
        payloads (e.g. 'momiratravel-Christian') actually corresponds to a
        real account, or is being silently ignored/replaced.
        """
        url = f"{self.api_base_url}/user/{self.microsite_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return []
        data = self._json(res)
        return data if isinstance(data, list) else []

    def get_closed_tours(self, supplier_id: str, first: int = 0, limit: int = 100) -> Dict[str, Any]:
        """
        Executes GET /closedtour/{supplierId} (no tour code) — mirrors the
        confirmed get_tickets() list pattern. Returns whatever the API gives
        back (a bare list, or a paginated dict wrapping the list depending
        on account/version) for the caller to normalize. Used to build a
        "does a tour with this name already exist" pre-upload check, so a
        failure here should never block publishing - callers must treat any
        {"error": ...} result as "couldn't verify, skip the check" rather
        than a hard failure.
        """
        url = f"{self.api_base_url}/closedtour/{supplier_id}"
        res = self._request("GET", url, headers={"first": str(first), "limit": str(limit)})

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_closed_tour(self, supplier_id: str, closed_tour_code: str) -> Dict[str, Any]:
        """
        Executes GET /closedtour/{supplierId}/{closedTourCode} — returns the
        full existing tour (name, itinerary, modalityCodes list, etc).
        NOTE: the tour's own 'price' field is deprecated and always 0 -
        real pricing lives per-option, fetched via get_closed_tour_option().
        """
        url = f"{self.api_base_url}/closedtour/{supplier_id}/{closed_tour_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_closed_tour_option(self, supplier_id: str, closed_tour_code: str, option_code: str) -> Dict[str, Any]:
        """
        Executes GET /closedtour/{supplierId}/{closedTourCode}/{optionCode}
        — returns one specific option's full details, including its live
        priceList. Use this before updating an option, to see exactly
        what's currently there.
        """
        url = f"{self.api_base_url}/closedtour/{supplier_id}/{closed_tour_code}/{option_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_closed_tour(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /closedtour/{supplierId} — creates main tour (draft, active: False)."""
        url = f"{self.api_base_url}/closedtour/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_closed_tour_option(self, supplier_id: str, closed_tour_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /closedtour/{supplierId}/{closedTourCode} — pushes modality/pricing option."""
        url = f"{self.api_base_url}/closedtour/{supplier_id}/{closed_tour_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_closed_tour(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """
        Executes PUT /closedtour/{supplierId} — updates an EXISTING tour's
        details (name, description, itinerary, etc). The payload's 'code'
        field identifies which existing tour to update. Use create_closed_tour
        (POST) instead when creating a brand-new tour.
        """
        url = f"{self.api_base_url}/closedtour/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_closed_tour_option(self, supplier_id: str, closed_tour_code: str, payload: dict) -> Dict[str, Any]:
        """
        Executes PUT /closedtour/{supplierId}/{closedTourCode} — updates an
        EXISTING option (pricing, operational days, etc). The payload's
        'code' field identifies which existing option to update. Use
        create_closed_tour_option (POST) instead to add a brand-new option.
        """
        url = f"{self.api_base_url}/closedtour/{supplier_id}/{closed_tour_code}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    # ------------------------------------------------------------------
    # TICKET UPLOADS (excursions - single destination, no overnight)
    # Confirmed against real Swagger + live GET examples.
    # ------------------------------------------------------------------
    def get_tickets(self, supplier_id: str, first: int = 0, limit: int = 50) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId} — returns paginated list of tickets for this supplier."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        res = self._request("GET", url, headers={"first": str(first), "limit": str(limit)})

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_ticket(self, supplier_id: str, ticket_code: str) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId}/{ticketCode} — returns the full existing ticket."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_ticket_option(self, supplier_id: str, ticket_code: str, option_code: str) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId}/{ticketCode}/{optionCode} — returns a specific ticket modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}/{option_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_ticket(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /tickets/{supplierId} — creates a new ticket."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_ticket_option(self, supplier_id: str, ticket_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /tickets/{supplierId}/{ticketCode} — creates a new ticket option/modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_ticket(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /tickets/{supplierId} — updates an EXISTING ticket's details."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_ticket_option(self, supplier_id: str, ticket_code: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /tickets/{supplierId}/{ticketCode} — updates an EXISTING ticket option/modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    # ------------------------------------------------------------------
    # TRANSFER UPLOADS
    # Confirmed against the real Swagger + 13 real GET examples across 2
    # real suppliers. See transfer_matcher.py for how an existing transfer
    # is identified for an update - there is no human-assigned code on this
    # product type, only a TC-generated id like "TRANSFER-412545".
    # ------------------------------------------------------------------
    def get_transfers(self, supplier_id: str) -> Dict[str, Any]:
        """
        Executes GET /transfer/{supplierId} — returns ALL transfers for this
        supplier (no pagination/filter parameter exists in the Swagger).
        Used as the candidate pool for the departure/arrival matching
        fallback when the app has no locally-tracked id for a route yet -
        see transfer_matcher.suggest_existing_transfer_matches.
        """
        url = f"{self.api_base_url}/transfer/{supplier_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_transfer(self, supplier_id: str, transfer_id: str) -> Dict[str, Any]:
        """Executes GET /transfer/{supplierId}/{transferId} — returns one specific transfer by its TC-generated id."""
        url = f"{self.api_base_url}/transfer/{supplier_id}/{transfer_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_transfer(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /transfer/{supplierId} — creates a new transfer. Travel Compositor
        assigns and returns the new 'id' in the response - remember it via
        transfer_matcher.remember_transfer_id so future updates to this same route auto-match."""
        url = f"{self.api_base_url}/transfer/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_transfer(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """
        Executes PUT /transfer/{supplierId} — updates an EXISTING transfer.
        UNLIKE ClosedTour/Ticket's PUT, the transfer id is NOT in the URL
        path — it must be set on the payload's own 'id' field (confirmed
        via Swagger), pointing at the transfer being updated.
        """
        url = f"{self.api_base_url}/transfer/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    # ------------------------------------------------------------------
    # TRANSPORT BASES  (the location master list Transport's segments
    # reference via departureLocationCode/arrivalLocationCode, e.g.
    # "meet_aswan" - confirmed a SEPARATE master list from the general
    # Destination endpoint used by ClosedTour/Ticket, and from Transfer's
    # own Zones endpoint. Global to the account - no supplier/microsite
    # scoping in the Swagger, just pagination.)
    # ------------------------------------------------------------------
    def _get_all_transport_bases(self, lang: str = "EN") -> List[Dict[str, Any]]:
        """
        Fetches and caches the FULL active transport-base list via paginated
        GET /transportbases (first/limit required, no supplier/microsite
        scoping - confirmed via Swagger this is a global, account-wide
        list). Pages through using the response's own 'pagination' block
        (firstResult/pageResults/totalResults) until exhausted, same
        cache-once-per-session approach as _get_all_destinations().
        """
        if self._transport_base_cache is not None:
            return self._transport_base_cache

        bases: List[Dict[str, Any]] = []
        page_size = 100
        first = 0
        try:
            while True:
                url = f"{self.api_base_url}/transportbases"
                res = self._request("GET", url, params={"first": first, "limit": page_size, "lang": lang})
                if res.status_code != 200:
                    print(f"⚠️ Could not fetch transport bases (page starting {first}): {res.status_code} {res.text}")
                    break
                data = self._json(res)
                page = data.get("transportbase", []) if isinstance(data, dict) else (data or [])
                bases.extend(page)
                pagination = data.get("pagination") or {} if isinstance(data, dict) else {}
                total = pagination.get("totalResults")
                if not page or total is None or len(bases) >= total:
                    break
                first += page_size
        except requests.RequestException as e:
            print(f"⚠️ Could not fetch transport bases: {e}")

        self._transport_base_cache = bases or []
        print(f"📥 Cached {len(self._transport_base_cache)} transport base(s).")
        return self._transport_base_cache

    # An airport stands for the city or resort area it serves. CONFIRMED REAL RULE (product
    # owner): "if the document says from airport (like RMF Airport) to Hurghada, this can also
    # be a Transport from Marsa Alam to Hurghada - airport to another city also means city to
    # city." Rate sheets name the airport; Travel Compositor's transport bases are named after
    # places, so "RMF Airport" resolves to nothing while "Marsa Alam" resolves fine. Without
    # this fallback every route in an airport-origin rate sheet fails to resolve and cannot be
    # published at all.
    #
    # Codes are listed only where the document's own shorthand would otherwise be unreadable -
    # the generic rules below (drop "Airport", "International", "Intl") handle the far more
    # common "Hurghada Airport" -> "Hurghada" case on their own, for any destination anywhere.
    _AIRPORT_CITY = {
        "hrg": "Hurghada", "rmf": "Marsa Alam", "ssh": "Sharm El Sheikh", "cai": "Cairo",
        "lxr": "Luxor", "asw": "Aswan", "sph": "Sohag", "atz": "Assiut", "hbe": "Alexandria",
        "mub": "Marsa Matruh", "abs": "Abu Simbel", "tcp": "Taba", "sez": "Mahe",
        "prI": "Praslin", "dxb": "Dubai", "auh": "Abu Dhabi",
    }

    @classmethod
    def _place_alternates(cls, query: str) -> List[str]:
        """Other names the same place might be listed under, best guess first.

        Deliberately conservative, because a wrong alternate resolves to a REAL but WRONG
        transport base and publishes a route between the wrong two places - a failure that
        looks like success. Two rules only:

          * an explicit IATA code mapping ("RMF" -> "Marsa Alam"), which is a fact, not a guess;
          * dropping a trailing "Airport"/"International Airport", which is string surgery and
            so is applied ONLY when the word actually trails ("Hurghada Airport" -> "Hurghada").
            A place genuinely called "Airport Road" keeps its name - stripping there would
            leave "Road", which substring-matches almost anything.

        Bare codes are never returned as alternates: "RMF" is not a place name, and three
        letters substring-match far too easily."""
        raw = (query or "").strip()
        low = raw.lower()
        if not low:
            return []
        out = []

        tokens = re.split(r"[\s/,()\-]+", low)
        tokens = [t for t in tokens if t]
        for token in tokens:
            city = cls._AIRPORT_CITY.get(token)
            if city and city not in out:
                out.append(city)

        # "<place> airport", "<place> international airport", "<place> intl. airport"
        trailing = re.match(r"^(?P<place>.+?)[\s,\-]+(international\s+|intl\.?\s+|domestic\s+)?"
                            r"(airport|airfield|aeropuerto)\s*$", low)
        if trailing:
            place = trailing.group("place").strip(" -/,()")
            if len(place) >= 4 and place not in cls._AIRPORT_CITY:
                original = raw[:len(place)].strip(" -/,()") or place
                if original.lower() != low and original not in out:
                    out.append(original)
        return [o for o in out if o and len(o) >= 4]

    def resolve_transport_base(self, query_term: str) -> Dict[str, Any]:
        """
        Resolves a place name or a real transport-base code to a Transport
        Base record. Mirrors resolve_destination()'s two-step approach:
          1. Direct code lookup via GET /transportbases/{code} (in case the
             human/document already gives a real code, e.g. "meet_aswan").
          2. Name matching (exact -> substring) against the cached full list
             fetched by _get_all_transport_bases().

        Returns: {"code": str|None, "name": str, "type": str|None,
                   "latitude": float|None, "longitude": float|None,
                   "valid": bool, "match_type": str}
        """
        clean_query = (query_term or "").strip()
        if not clean_query:
            return {"code": None, "name": None, "type": None, "latitude": None, "longitude": None,
                    "valid": False, "match_type": "empty_query"}

        # 1. Direct code lookup
        try:
            url_direct = f"{self.api_base_url}/transportbases/{clean_query}"
            res = self._request("GET", url_direct, params={"lang": "EN"})
            if res.status_code == 200:
                data = self._json(res)
                if isinstance(data, dict) and data.get("code"):
                    geo = data.get("geolocation") or {}
                    print(f"✅ RESOLVED (by code): '{clean_query}' -> {data['code']} ({data.get('name')})")
                    return {
                        "code": data["code"], "name": data.get("name", data["code"]), "type": data.get("type"),
                        "latitude": geo.get("latitude"), "longitude": geo.get("longitude"),
                        "valid": True, "match_type": "code",
                    }
        except requests.RequestException as e:
            print(f"⚠️ Direct transport-base code lookup failed for '{clean_query}': {e}")

        # 2. Name matching against the cached full list
        try:
            bases = self._get_all_transport_bases()
        except requests.RequestException as e:
            print(f"⚠️ Could not fetch transport base list: {e}")
            bases = []

        query_lower = clean_query.lower()

        def _to_result(base: dict, match_type: str) -> Dict[str, Any]:
            geo = base.get("geolocation") or {}
            return {
                "code": base.get("code"), "name": base.get("name", clean_query), "type": base.get("type"),
                "latitude": geo.get("latitude"), "longitude": geo.get("longitude"),
                "valid": base.get("code") is not None, "match_type": match_type,
            }

        for base in bases:
            if (base.get("name") or "").strip().lower() == query_lower:
                print(f"✅ RESOLVED (exact name): '{clean_query}' -> {base.get('code')} ({base.get('name')})")
                return _to_result(base, "exact_name")

        substring_matches = [b for b in bases if query_lower in (b.get("name") or "").lower()]
        if substring_matches:
            best = substring_matches[0]
            print(f"✅ RESOLVED (substring name): '{clean_query}' -> {best.get('code')} ({best.get('name')})")
            return _to_result(best, "substring_name")

        # 3. Airport -> the city it serves. See _place_alternates.
        for alternate in self._place_alternates(clean_query):
            alt_lower = alternate.lower()
            for base in bases:
                if (base.get("name") or "").strip().lower() == alt_lower:
                    print(f"✅ RESOLVED (airport -> city): '{clean_query}' -> '{alternate}' -> "
                          f"{base.get('code')} ({base.get('name')})")
                    result = _to_result(base, "airport_city")
                    result["resolved_via"] = alternate
                    return result
            partials = [b for b in bases if alt_lower in (b.get("name") or "").lower()]
            if partials:
                best = partials[0]
                print(f"✅ RESOLVED (airport -> city, partial): '{clean_query}' -> '{alternate}' -> "
                      f"{best.get('code')} ({best.get('name')})")
                result = _to_result(best, "airport_city_substring")
                result["resolved_via"] = alternate
                return result

        print(f"⚠️ Transport base '{clean_query}' not found anywhere. Flagging as invalid.")
        return {"code": None, "name": clean_query, "type": None, "latitude": None, "longitude": None,
                "valid": False, "match_type": "not_found"}

    # ------------------------------------------------------------------
    # TRANSPORT UPLOADS
    # Confirmed against the real Swagger + real GET examples across 2 real
    # suppliers/routes. See transport_matcher.py for how an existing
    # transport is identified for an update - like Transfer, there is no
    # human-assigned code, only a TC-generated id like "TRANSPORT-412579".
    # Two-level structure: the main transport (this section) plus separate
    # Option sub-resources, one per occupancy/passenger bracket (see the
    # TRANSPORT OPTIONS section further below).
    # ------------------------------------------------------------------
    def get_transports(self, supplier_id: str) -> Dict[str, Any]:
        """Executes GET /transport/{supplierId} — returns ALL transports for this supplier.
        Used as the candidate pool for the departure/arrival matching fallback - see
        transport_matcher.suggest_existing_transport_matches."""
        url = f"{self.api_base_url}/transport/{supplier_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_transport(self, supplier_id: str, transport_id: str) -> Dict[str, Any]:
        """Executes GET /transport/{supplierId}/{transportId} — returns one specific transport
        (the parent record only - NOT its options, see get_transport_option)."""
        url = f"{self.api_base_url}/transport/{supplier_id}/{transport_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_transport(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /transport/{supplierId} — creates a new transport (the parent record
        only). Travel Compositor assigns and returns the new 'id' in the response - remember it
        via transport_matcher.remember_transport_id, then create one Option per occupancy
        bracket via create_transport_option()."""
        url = f"{self.api_base_url}/transport/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_transport(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """
        Executes PUT /transport/{supplierId} — updates an EXISTING transport's parent record.
        Like Transfer, the id is NOT in the URL path - it must be set on the payload's own 'id'
        field. Does NOT touch options - see update_transport_option()/create_transport_option().
        """
        url = f"{self.api_base_url}/transport/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    # ------------------------------------------------------------------
    # TRANSPORT OPTIONS  (one per occupancy/passenger bracket - see
    # ContractTransportOptionVO's docstring in schemas.py for the confirmed
    # additive-supplement pricing model)
    # ------------------------------------------------------------------
    def get_transport_option(self, supplier_id: str, transport_id: str, option_code: str) -> Dict[str, Any]:
        """Executes GET /transport/{supplierId}/{transportId}/{optionCode} — returns one specific
        occupancy-bracket option. Real option codes are NOT predictable from the route/bracket
        (confirmed: "ASWHRG", "PraslinLaDigue12", and ones equal to the transport's own name all
        seen in real data) - always iterate the parent's own optionCodes list rather than
        guessing a code."""
        url = f"{self.api_base_url}/transport/{supplier_id}/{transport_id}/{option_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_transport_option(self, supplier_id: str, transport_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /transport/{supplierId}/{transportId} — creates a new occupancy-bracket
        option under an existing transport."""
        url = f"{self.api_base_url}/transport/{supplier_id}/{transport_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_transport_option(self, supplier_id: str, transport_id: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /transport/{supplierId}/{transportId} — updates an EXISTING occupancy-
        bracket option. Confirmed via Swagger: the option's own 'code' field in the payload body
        identifies WHICH option gets updated (transportId in the URL just scopes to the parent
        transport) - there is no optionCode in the PUT URL, unlike GET."""
        url = f"{self.api_base_url}/transport/{supplier_id}/{transport_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    # ------------------------------------------------------------------
    # HOTEL UPLOADS
    # Confirmed against the real Swagger (Contract - Hotel) + 2 real GET
    # pulls for a live hotel (CAI-H1, supplier 48940). UNLIKE every other
    # product type, a hotel's providerCode is HUMAN-ASSIGNED (e.g. "CAI-H1"),
    # not Travel Compositor-generated - so there's no route-similarity
    # matching needed to recognize an existing hotel, see hotel_matcher.py.
    # Structure: one parent hotel record (rooms/mealPlans inline) plus three
    # separate sibling sub-resource families - Offers/Supplements (create-
    # only, no PUT endpoint exists) and Rates (has both POST and PUT, nests
    # Seasons/seasonRoomPrices/stopSales).
    # ------------------------------------------------------------------
    def get_hotels(self, supplier_id: str) -> Dict[str, Any]:
        """Executes GET /hotel/{supplierId} — returns a LIGHTWEIGHT list of all hotels for this
        supplier (confirmed real Swagger: rooms/mealPlans come back as flat string arrays here,
        not full nested objects - use get_hotel() for the full detail of one specific hotel)."""
        url = f"{self.api_base_url}/hotel/{supplier_id}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def get_hotel(self, supplier_id: str, provider_code: str) -> Dict[str, Any]:
        """Executes GET /hotel/{supplierId}/{providerCode} — returns the FULL nested hotel record
        (rooms, mealPlans, descriptions, voucherRemarks, images, facilities, offers, supplements,
        rates with their seasons/seasonRoomPrices/stopSales - everything). This is the only call
        that returns offers/supplements/rates at all - there's no dedicated GET for any of those
        sub-resources individually."""
        url = f"{self.api_base_url}/hotel/{supplier_id}/{provider_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/{supplierId} — creates a new hotel contract. Payload includes the
        hotel's own fields plus its rooms[] and mealPlans[] inline (both required, min 1 item) -
        NOT offers/supplements/rates, which are separate calls made afterward once the hotel
        exists (see create_hotel_offer/create_hotel_supplement/create_hotel_rates)."""
        url = f"{self.api_base_url}/hotel/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_hotel(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /hotel/{supplierId} — updates an EXISTING hotel contract. Unlike Transfer/
        Transport's PUT, providerCode (the identifier) is a normal required field already on this
        payload - there's no separate id-in-body quirk. This is a FULL REPLACE of the hotel-level
        record including the whole rooms[]/mealPlans[] arrays - see build_hotel_payloads()'s
        merge-on-update logic for how existing rooms/mealPlans not mentioned in a fresh document
        are preserved rather than silently dropped."""
        url = f"{self.api_base_url}/hotel/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel_room(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/room/{supplierId}/{providerCode} — adds a single room to an
        EXISTING hotel contract. This tool's builder drives room creation/updates through the
        main create_hotel()/update_hotel() calls instead (which carry the full rooms[] array
        anyway, per PUT's full-replace semantics) - this method is provided for completeness /
        direct use if a narrower single-room addition is ever needed."""
        url = f"{self.api_base_url}/hotel/room/{supplier_id}/{provider_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel_mealplan(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/mealplan/{supplierId}/{providerCode} — adds a single meal plan to
        an EXISTING hotel contract. Same note as create_hotel_room() - this tool's builder drives
        meal plans through the main create_hotel()/update_hotel() calls; provided for
        completeness / direct use if needed."""
        url = f"{self.api_base_url}/hotel/mealplan/{supplier_id}/{provider_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel_offer(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/offer/{supplierId}/{providerCode} — adds an offer to an existing
        hotel contract. CONFIRMED CREATE-ONLY - no PUT variant exists for offers (see
        ContractHotelOffersVO's docstring in schemas.py for why that's fine: offers are inherently
        date-bounded and self-expire)."""
        url = f"{self.api_base_url}/hotel/offer/{supplier_id}/{provider_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel_supplement(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/supplement/{supplierId}/{providerCode} — adds a supplement to an
        existing hotel contract. CONFIRMED CREATE-ONLY, same as offers - no PUT variant exists."""
        url = f"{self.api_base_url}/hotel/supplement/{supplier_id}/{provider_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def create_hotel_rates(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /hotel/rates/{supplierId}/{providerCode} — adds a new rate (with its
        nested seasons/seasonRoomPrices/stopSales) to an existing hotel contract. Travel
        Compositor assigns and returns 'id' (and each season's own 'id') in the response -
        remember them for update_hotel_rates() on the next refresh, see hotel_matcher.py."""
        url = f"{self.api_base_url}/hotel/rates/{supplier_id}/{provider_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)

    def update_hotel_rates(self, supplier_id: str, provider_code: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /hotel/rates/{supplierId}/{providerCode} — updates an EXISTING rate.
        The rate's own 'id' field in the payload body identifies WHICH rate gets updated
        (providerCode in the URL just scopes to the parent hotel) - same id-in-body pattern as
        Transport's option PUT."""
        url = f"{self.api_base_url}/hotel/rates/{supplier_id}/{provider_code}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return self._json(res)


# ============================================================================
# SHARED ERROR TRANSLATION - a raw TC error dict -> a message a human can act on
# ============================================================================
def describe_tc_fetch_error(detail: Any, entity_label: str = "this record") -> str:
    """CONFIRMED REAL INCIDENT (2026-08-25): translating Closed Tour TNR-01 (supplier 50370)
    failed with `{"error": 400, "message": "{\\"error\\":[\\"java.lang.NullPointerException:
    Cannot invoke \\\\\\"com.tr2.entity.AgeRange.getMin()\\\\\\" because \\\\\\"ageRange\\\\\\"
    is null\\"],\\"status\\":\\"BAD_REQUEST\\"}"}` - Travel Compositor's own server threw an
    unhandled Java exception (a genuine NullPointerException, not a validation error) while
    reading that specific record's data and returned it wrapped in a 400 rather than the more
    accurate 500. That is a bug on Travel Compositor's side in a piece of data already stored on
    their server, not anything this tool sent or anything wrong with the closed tour code entered
    - a plain GET, no payload, fails identically no matter what calls it.

    Any caller that gets a `{"status": "fetch_failed", ...}` result (sync_closed_tour.py and
    its siblings all use this exact shape - see that module's own fetch_failed returns) should
    route the `detail` dict through this before showing it, instead of leaving a human staring at
    a raw nested-JSON Java stack trace with no idea whether it's their mistake or not.

    General on purpose, not Closed-Tour-specific: the same "TC's own server 500/400'd with an
    internal exception, wrapped in our own generic fetch_failed shape" pattern can happen on any
    product's GET, so any future caller gets the same translation for free."""
    if not isinstance(detail, dict):
        return (f"Travel Compositor's server returned an unexpected error while fetching "
                f"{entity_label}. Try again in a moment - if it keeps happening, contact "
                f"Travel Compositor support.")

    raw_message = detail.get("message") or ""
    # `message` is itself often a JSON-encoded string (TC nests its own error body as text
    # inside the outer one) - fall back to the raw string if it doesn't parse, since either way
    # we're about to substring-search it for known exception signatures.
    try:
        import json as _json_module
        parsed = _json_module.loads(raw_message) if isinstance(raw_message, str) else raw_message
        inner_text = " ".join(parsed.get("error") or []) if isinstance(parsed, dict) else str(parsed)
    except (ValueError, TypeError):
        inner_text = str(raw_message)

    if "NullPointerException" in inner_text or "nullpointerexception" in inner_text.lower():
        return (f"Travel Compositor's own server hit an internal error (a NullPointerException) "
                f"while trying to read {entity_label} - this is a bug in data already stored on "
                f"their side, not a mistake in what was entered here or anything this tool sent. "
                f"The record can't be fetched at all until Travel Compositor fixes it, so "
                f"translating/updating it here isn't possible in the meantime. Contact Travel "
                f"Compositor support with this exact error (see 'Full result' below) and the "
                f"supplier/code involved, and ask them to check that record's data on their end.")

    status_code = detail.get("error")
    if status_code and int(status_code) >= 500:
        return (f"Travel Compositor's server had an internal error (HTTP {status_code}) while "
                f"fetching {entity_label}. This is usually temporary - try again in a moment. If "
                f"it keeps happening, contact Travel Compositor support.")

    return (f"Travel Compositor's server rejected the request for {entity_label} "
            f"(HTTP {status_code or '?'}). See 'Full result' below for the exact message - if it "
            f"isn't self-explanatory, contact Travel Compositor support.")
