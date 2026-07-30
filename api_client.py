import os
import difflib
import requests
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

load_dotenv()


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

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Wraps requests.request() with automatic re-authentication if the
        token has expired (401). Without this, an expired token mid-session
        looks like a random "connection failure" instead of an auth issue.
        """
        kwargs.setdefault("timeout", 15)
        res = requests.request(method, url, headers=self.get_headers(), **kwargs)

        if res.status_code == 401:
            print("♻️  Auth token expired/rejected — re-authenticating and retrying once...")
            self.authenticate(force=True)
            res = requests.request(method, url, headers=self.get_headers(), **kwargs)

        return res

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
        data = res.json()

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
                data = res.json()
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
                data = res.json()
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
        data = res.json()
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
        data = res.json()
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
        merged_headers = {**self.get_headers(), "first": str(first), "limit": str(limit)}
        res = requests.request("GET", url, headers=merged_headers, timeout=15)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

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
        return res.json()

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
        return res.json()

    def create_closed_tour(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /closedtour/{supplierId} — creates main tour (draft, active: False)."""
        url = f"{self.api_base_url}/closedtour/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def create_closed_tour_option(self, supplier_id: str, closed_tour_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /closedtour/{supplierId}/{closedTourCode} — pushes modality/pricing option."""
        url = f"{self.api_base_url}/closedtour/{supplier_id}/{closed_tour_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

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
        return res.json()

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
        return res.json()

    # ------------------------------------------------------------------
    # TICKET UPLOADS (excursions - single destination, no overnight)
    # Confirmed against real Swagger + live GET examples.
    # ------------------------------------------------------------------
    def get_tickets(self, supplier_id: str, first: int = 0, limit: int = 50) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId} — returns paginated list of tickets for this supplier."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        merged_headers = {**self.get_headers(), "first": str(first), "limit": str(limit)}
        res = requests.request("GET", url, headers=merged_headers, timeout=15)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def get_ticket(self, supplier_id: str, ticket_code: str) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId}/{ticketCode} — returns the full existing ticket."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def get_ticket_option(self, supplier_id: str, ticket_code: str, option_code: str) -> Dict[str, Any]:
        """Executes GET /tickets/{supplierId}/{ticketCode}/{optionCode} — returns a specific ticket modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}/{option_code}"
        res = self._request("GET", url)

        if res.status_code != 200:
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def create_ticket(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /tickets/{supplierId} — creates a new ticket."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def create_ticket_option(self, supplier_id: str, ticket_code: str, payload: dict) -> Dict[str, Any]:
        """Executes POST /tickets/{supplierId}/{ticketCode} — creates a new ticket option/modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("POST", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def update_ticket(self, supplier_id: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /tickets/{supplierId} — updates an EXISTING ticket's details."""
        url = f"{self.api_base_url}/tickets/{supplier_id}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()

    def update_ticket_option(self, supplier_id: str, ticket_code: str, payload: dict) -> Dict[str, Any]:
        """Executes PUT /tickets/{supplierId}/{ticketCode} — updates an EXISTING ticket option/modality."""
        url = f"{self.api_base_url}/tickets/{supplier_id}/{ticket_code}"
        res = self._request("PUT", url, json=payload)

        if res.status_code not in (200, 201):
            print(f"\n❌ API Error ({res.status_code}):\n{res.text}")
            return {"error": res.status_code, "message": res.text}
        return res.json()
