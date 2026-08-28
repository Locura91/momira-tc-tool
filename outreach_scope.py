"""
outreach_scope.py — what a tour operator ought to be selling in a country, before searching.

CONFIRMED PRODUCT-OWNER REQUEST: "the first step is a complete Country search. This search is
selecting listing first the most important touristic regions and on a second list it is listing
the most touristic themes according to this country... The goal of the presearch must be, that
the first step is showing all possible touristic spots, that we as tour operator should have in
our program."

THE PROBLEM IT SOLVES: supplier discovery starts from a country and a keyword, and the keyword
is whatever happened to be on your mind. Search for "Nile Cruise in Egypt" and you find Nile
cruise operators - and you never find out that you have nothing in Siwa, no Suez Canal day trip,
and no St Catherine's. The gap is invisible, because a search only ever reports on what you
thought to ask for. This step names the whole board first, so the choosing is deliberate.

WHAT IT PRODUCES: two lists - PLACES (touristic regions worth having in a programme) and THEMES
(the kinds of product that country is actually known for). Tick what you want and each
combination becomes a supplier search.

IT IS ALWAYS SKIPPABLE. Someone who already knows they need Hurghada transfers should not have
to walk past a map of Egypt to say so.

CACHED PER COUNTRY, and editable. Egypt's touristic regions do not change between Tuesdays, so
the answer is stored and reused - and because it is stored, a place the model forgot can be
added by hand once and stays added.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-28-audit-followup-decisions"

from datetime import datetime, timezone
from typing import Any, Dict, List

import platform_store

_NAMESPACE = "outreach_country_scope"

# Deliberately generous. This is a completeness exercise: the whole point is to surface the
# place you had forgotten, and a list truncated to the obvious ten cannot do that.
_TARGET_PLACES = 18
_TARGET_THEMES = 16

SCOPE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "places": {
            "type": "array",
            "description": "Touristic regions, cities and sites a tour operator should cover.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The place, as a traveller would name it"},
                    "region": {"type": "string", "description": "Broader area, e.g. 'Red Sea', 'Upper Egypt'"},
                    "why": {"type": "string", "description": "One short line: what a visitor goes there for"},
                },
                "required": ["name", "why"],
            },
        },
        "themes": {
            "type": "array",
            "description": "Kinds of product/experience this country is known for.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The theme, e.g. 'Nile Cruise', 'Snorkeling'"},
                    "where": {"type": "string", "description": "Where in the country it is normally sold - free text, for the human to read"},
                    "why": {"type": "string", "description": "One short line on why it matters commercially"},
                    "places": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Which of the PLACES listed above (exact name match) this theme is "
                                       "actually sold at - e.g. 'Nile Cruise' -> ['Luxor', 'Aswan'], never "
                                       "'Sharm El Sheikh'. Leave empty ONLY for a theme that is genuinely "
                                       "sold everywhere in the country and isn't tied to specific places "
                                       "(e.g. 'Airport Transfer', 'Custom Private Tour').",
                    },
                },
                "required": ["name", "why", "places"],
            },
        },
        "notes": {"type": "string", "description": "Anything a buyer should know - seasonality, safety, access."},
    },
    "required": ["places", "themes"],
}

_SYSTEM_PROMPT = f"""You are briefing a tour operator's product buyer who is deciding what to put in
their programme for one country. They are not a tourist: they need the commercial map of the country,
not a top-ten list.

Produce TWO lists.

PLACES - up to {_TARGET_PLACES} touristic regions, cities or sites that a serious operator's programme
for this country should cover. Include the obvious anchors AND the ones a newcomer forgets: secondary
coastal resorts, oasis and desert regions, religious or archaeological sites away from the main
circuit, places that exist mainly as a day trip from somewhere bigger. Order them roughly by how
important they are to a programme, most important first.

THEMES - up to {_TARGET_THEMES} kinds of product this country is actually known for and that a
supplier could sell you: the standard excursions and experiences, not marketing abstractions. "Nile
Cruise", "Pyramid Tour", "Snorkeling", "Desert Oases Tour", "Museum visit", "Dinner cruise" are
themes. "Adventure", "Luxury" and "Authentic experiences" are not - they cannot be searched for and
no supplier sells them under that name.

MATCH EVERY THEME TO ITS REAL PLACES - this is what turns a place x theme grid into a usable daily
worklist instead of a pile of nonsense combinations (nobody sells "Snorkeling" in Cairo, or a "Nile
Cruise" out of Sharm El Sheikh). For each theme, set "places" to the exact names (copied verbatim
from your own PLACES list above) of every place that theme is genuinely sold at - a theme sold in
several places (e.g. "Desert Safari" out of both Hurghada and Marsa Alam) lists all of them. Only
leave "places" empty for a theme that is truly country-wide and not tied to specific places (e.g.
"Airport Transfer", "Custom Private Tour", "Multi-day Package"). Getting this right matters more
than the free-text "where" field - "places" is what the buyer's tool actually uses to decide which
searches are worth running.

RULES:
- Real places and real products only. Never invent a site or an excursion to pad the list.
- Use the names a traveller and a local supplier would both recognise, in English.
- Keep every "why" to one short line. The buyer is scanning, not reading.
- If a place is only worth having in certain months, or is currently hard to access, say so in notes.
- If you genuinely do not know this country well, return short lists and say so in notes rather than
  filling them with plausible-sounding guesses. An operator acting on an invented site wastes a week.
You MUST call the country_scope tool exactly once. Never answer in plain text."""


def _key(country: str) -> str:
    return " ".join((country or "").strip().lower().split())


def get_cached_scope(country: str) -> Dict[str, Any]:
    """The stored scope for this country, or {} - never raises."""
    if not _key(country):
        return {}
    try:
        row = platform_store.get(_NAMESPACE, _key(country))
    except Exception:
        return {}
    return row if isinstance(row, dict) else {}


def save_scope(country: str, scope: Dict[str, Any]) -> bool:
    row = dict(scope or {})
    row["country"] = (country or "").strip()
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return platform_store.set(_NAMESPACE, _key(country), row)


def forget_scope(country: str) -> bool:
    return platform_store.delete(_NAMESPACE, _key(country))


def list_known_countries() -> List[str]:
    try:
        rows = platform_store.get_namespace(_NAMESPACE)
    except Exception:
        return []
    return sorted({(r or {}).get("country", "") for r in rows.values() if isinstance(r, dict)} - {""})


def _clean(entries, required_key="name", list_keys=()) -> List[Dict[str, Any]]:
    """Sanitize AI-returned place/theme dicts.

    Scalar fields (name, why, where...) are kept as trimmed strings. Any key named in
    `list_keys` (e.g. a theme's "places") is instead kept as a list of trimmed, deduplicated
    strings - without `list_keys` those fields would silently vanish, since they fail the
    scalar isinstance check below, and a theme that loses its "places" list falls back to
    looking country-wide when it was really just never matched."""
    out, seen = [], set()
    for entry in (entries or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get(required_key) or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        cleaned = {k: str(v or "").strip() for k, v in entry.items()
                   if k not in list_keys and isinstance(v, (str, int, float))}
        for lk in list_keys:
            raw = entry.get(lk)
            items = []
            if isinstance(raw, list):
                item_seen = set()
                for v in raw:
                    s = str(v or "").strip()
                    if s and s.lower() not in item_seen:
                        item_seen.add(s.lower())
                        items.append(s)
            cleaned[lk] = items
        out.append(cleaned)
    return out


def suggest_country_scope(country: str, model: str = "claude-sonnet-5",
                          refresh: bool = False) -> Dict[str, Any]:
    """The places and themes worth covering in a country.

    Served from the cache unless `refresh` - the touristic map of a country does not change
    week to week, and re-asking would also throw away anything added by hand."""
    country = (country or "").strip()
    if not country:
        return {"places": [], "themes": [], "notes": "", "error": "No country given."}

    if not refresh:
        cached = get_cached_scope(country)
        if cached.get("places") or cached.get("themes"):
            cached["from_cache"] = True
            return cached

    # Imported lazily so this module can be used (and tested) without an API key present.
    from ai_extractor import _get_anthropic_client, _stream_claude_tool_call, friendly_error_message

    try:
        client = _get_anthropic_client()
        result, _stop = _stream_claude_tool_call(
            client, model, 8192, _SYSTEM_PROMPT,
            f"Country: {country}\n\nGive the buyer's map of this country.",
            tool_name="country_scope", input_schema=SCOPE_TOOL_SCHEMA)
    except Exception as e:
        return {"places": [], "themes": [], "notes": "",
                "error": f"Couldn't research {country} - {friendly_error_message(e)}"}

    cleaned_places = _clean((result or {}).get("places"))[:_TARGET_PLACES]
    known_place_names = {p["name"].strip().lower() for p in cleaned_places if p.get("name")}
    cleaned_themes = _clean((result or {}).get("themes"), list_keys=("places",))[:_TARGET_THEMES]
    # Drop any place name the model invented (typo, or a place that didn't make the final,
    # truncated places list) rather than let a theme silently point at a place that isn't there.
    for theme in cleaned_themes:
        theme["places"] = [p for p in theme.get("places", [])
                            if p.strip().lower() in known_place_names]
    scope = {
        "places": cleaned_places,
        "themes": cleaned_themes,
        "notes": str((result or {}).get("notes") or "").strip(),
    }
    if scope["places"] or scope["themes"]:
        save_scope(country, scope)
    else:
        scope["error"] = (f"No places or themes came back for {country}. Try the country's common "
                          f"English name, or skip this step and search directly.")
    return scope


def add_place(country: str, name: str, why: str = "") -> bool:
    """Add a place the model missed. It stays added - that is the point of caching."""
    return _add(country, "places", {"name": name, "why": why or "added by hand"})


def add_theme(country: str, name: str, why: str = "", places: List[str] = None) -> bool:
    """Add a theme the model missed. `places` ties it to specific places (like the AI-suggested
    ones); leave it empty/None for a genuinely country-wide theme."""
    entry = {"name": name, "why": why or "added by hand"}
    entry["places"] = [str(p).strip() for p in (places or []) if str(p).strip()]
    return _add(country, "themes", entry)


def _add(country: str, bucket: str, entry: Dict[str, str]) -> bool:
    name = str(entry.get("name") or "").strip()
    if not name:
        return False
    scope = get_cached_scope(country) or {"places": [], "themes": []}
    existing = scope.get(bucket) or []
    if any(str(e.get("name", "")).strip().lower() == name.lower()
           for e in existing if isinstance(e, dict)):
        return False
    scope[bucket] = existing + [entry]
    scope.setdefault("places", [])
    scope.setdefault("themes", [])
    return save_scope(country, scope)


def remove_entry(country: str, bucket: str, name: str) -> bool:
    scope = get_cached_scope(country)
    if not scope:
        return False
    before = scope.get(bucket) or []
    after = [e for e in before
             if str((e or {}).get("name", "")).strip().lower() != (name or "").strip().lower()]
    if len(after) == len(before):
        return False
    scope[bucket] = after
    return save_scope(country, scope)


def group_themes_by_place(places: List[Dict[str, Any]], themes: List[Dict[str, Any]]
                          ) -> "tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """Sort themes under the places they are actually sold at.

    Returns (per_place, countrywide):
      per_place       - one entry per place, in the original place order, each carrying the
                         list of themes whose "places" names it (case-insensitive match).
      countrywide      - themes with no "places" at all, or whose named places don't match any
                         place we know about (an AI slip, or a place the human never added) -
                         these still need somewhere to live rather than silently vanishing.

    This is what turns the old flat "tick any place, tick any theme, get the full cross
    product" screen into a worklist where each place only ever shows the themes that are
    genuinely sold there - no "Snorkeling" next to Cairo."""
    place_list = [p for p in (places or []) if isinstance(p, dict) and p.get("name")]
    theme_list = [t for t in (themes or []) if isinstance(t, dict) and t.get("name")]
    place_names_by_key = {p["name"].strip().lower(): p["name"].strip() for p in place_list}

    per_place = [{"place": p, "themes": []} for p in place_list]
    slot_by_key = {p["name"].strip().lower(): slot for p, slot in zip(place_list, per_place)}
    countrywide = []

    for theme in theme_list:
        theme_places = [str(p).strip() for p in (theme.get("places") or []) if str(p).strip()]
        matched_any = False
        for tp in theme_places:
            slot = slot_by_key.get(tp.lower())
            if slot is not None:
                slot["themes"].append(theme)
                matched_any = True
        if not matched_any:
            countrywide.append(theme)

    return per_place, countrywide


def planned_searches(country: str, pairs: List[tuple]) -> List[Dict[str, str]]:
    """The searches an explicit (place, theme) selection implies.

    `pairs` is a list of (place_name_or_"", theme_name_or_"") tuples - built by the caller from
    whatever the human ticked in the place-grouped screen (a countrywide theme pairs with "",
    a place ticked with no theme pairs with ""). Taking explicit pairs rather than two flat
    lists is the whole point of the place/theme grouping: no blind cross product, no
    "Snorkeling in Cairo" nobody asked for."""
    country = (country or "").strip()
    if not country:
        return []
    seen, out = set(), []
    for place, theme in (pairs or []):
        place = str(place or "").strip()
        theme = str(theme or "").strip()
        if not place and not theme:
            continue
        key = (place.lower(), theme.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"country": country, "city": place, "keyword": theme})
    return out
