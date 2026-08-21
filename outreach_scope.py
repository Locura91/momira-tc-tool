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
MODULE_BUILD = "2026-08-21-rollover-cheapest-and-scope"

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
                    "where": {"type": "string", "description": "Where in the country it is normally sold"},
                    "why": {"type": "string", "description": "One short line on why it matters commercially"},
                },
                "required": ["name", "why"],
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


def _clean(entries, required_key="name") -> List[Dict[str, str]]:
    out, seen = [], set()
    for entry in (entries or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get(required_key) or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({k: str(v or "").strip() for k, v in entry.items() if isinstance(v, (str, int, float))})
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

    scope = {
        "places": _clean((result or {}).get("places"))[:_TARGET_PLACES],
        "themes": _clean((result or {}).get("themes"))[:_TARGET_THEMES],
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


def add_theme(country: str, name: str, why: str = "") -> bool:
    return _add(country, "themes", {"name": name, "why": why or "added by hand"})


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


def planned_searches(country: str, places: List[str], themes: List[str]) -> List[Dict[str, str]]:
    """The searches a selection implies - one per place x theme.

    Returned as a list rather than run directly so the count can be shown BEFORE anything runs:
    six places and five themes is thirty searches, which is a long wait nobody agreed to."""
    country = (country or "").strip()
    places = [p for p in (places or []) if str(p).strip()]
    themes = [t for t in (themes or []) if str(t).strip()]
    if not country:
        return []
    if not themes:
        return [{"country": country, "city": p, "keyword": ""} for p in places] or []
    if not places:
        return [{"country": country, "city": "", "keyword": t} for t in themes]
    return [{"country": country, "city": p, "keyword": t} for p in places for t in themes]
