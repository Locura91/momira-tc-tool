"""
outreach_learned_suppliers.py — remembers suppliers a human added BY HAND in the Outreach tool,
keyed by the exact (country, theme/keyword) combination they were searching, so a supplier a
human already found and vetted once doesn't have to be found again by the automated search, or
re-typed in by hand, the next time the same combination is searched.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-30): "whenever the human is adding manually suppliers,
so the App can learn which suppliers are needed and to improve the search results." Two design
decisions, both confirmed with the product owner before building this:

  1. MATCH SCOPE — Country + Theme/Keyword, not Country alone. A supplier remembered for
     "Nile Cruise" in Egypt only resurfaces on a future "Nile Cruise" search in Egypt, not on
     every unrelated Egypt search too (e.g. "Desert Safari"). Matching is on a NORMALIZED exact
     string (see _normalize below) - deliberately NOT fuzzy, same reasoning hotel_matcher.py's
     own docstring gives for its own name-matching: a looser match risks silently attaching a
     supplier to the wrong theme, which is worse than a human having to re-add it once more.
     Only the plain Country/City/Keyword search maps a manual add to an unambiguous single
     (country, theme) pair on its own. A Country-Scope run (many place/theme combinations
     searched and merged into one review screen at once) does not have that luxury - see
     outreach_tool.py's own comment where it builds `or_session["combinations"]` for how that
     case is handled: a manual add there is remembered against EVERY (country, theme)
     combination that was actually part of that run, not the whole country's theme list - still
     bounded to what was actually searched, not blind.
  2. WHAT "IMPROVING SEARCH RESULTS" MEANS - both of:
       a) RECALL: resurface_remembered_suppliers() is merged into every future
          outreach_discovery.discover_suppliers() call for a matching (country, theme) - see
          that module's own docstring/call site. A remembered supplier now on the domain
          blocklist (outreach_memory) is filtered back out here - being added by hand once does
          not exempt a domain from later being blocked.
       b) AI VERIFICATION CALIBRATION: the same remembered suppliers are passed to
          outreach_discovery.verify_candidates() as known-good reference examples for the SAME
          country/theme, so the AI verification step (only runs when ANTHROPIC_API_KEY is
          configured) has a concrete local example of what counts as a genuine match, not just
          the generic prompt wording. This half can't be verified end-to-end without a live key
          (not available in this environment) - only that the prompt carries the examples
          correctly (see test_2026_08_30_outreach_learned_suppliers.py).

STORAGE: platform_store, same durable key/value store outreach_memory.py's blocklist uses (see
that module's own docstring for why - Streamlit Cloud's filesystem is wiped on every redeploy).
One row per (country, theme) bucket; namespace kept SEPARATE from outreach_memory's blocklist
namespace, because these are opposite-purpose lists (suppliers to surface vs. domains to skip)
and conflating them risks exactly the kind of silent cross-purpose bug outreach_memory.py's own
"SINGLE OWNER, DELIBERATELY" docstring warns about.

Functions:
    remember_supplier(country, theme, supplier) -> bool
    get_remembered_for(country, theme) -> List[dict]
    resurface_remembered_suppliers(country, theme) -> List[dict]
    list_all() -> List[dict]
    forget_supplier(country, theme, supplier_id) -> bool
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-02-ai-extractor-high-findings"

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import platform_store

_NAMESPACE = "outreach_learned_suppliers"


def _normalize(text: Optional[str]) -> str:
    """Same shape as outreach_discovery.normalize_name (alnum-only, lowercased, collapsed
    whitespace) - duplicated here rather than imported, deliberately: outreach_discovery.py
    imports FROM this module (to merge remembered suppliers into a live search - see its own
    discover_suppliers() docstring), so importing the other way back would be circular."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _bucket_key(country: str, theme: str) -> Optional[str]:
    """The storage key for one (country, theme) combination, or None when there's no country
    to key on at all - a bucket with no country is never a valid memory (there always must be a
    country by the time a search runs; only the theme half is ever legitimately blank, for a
    place-only combination with no specific theme ticked)."""
    country_norm = _normalize(country)
    if not country_norm:
        return None
    theme_norm = _normalize(theme)
    return f"{country_norm}::{theme_norm}"


def _get_bucket(key: str) -> List[Dict[str, Any]]:
    try:
        stored = platform_store.get(_NAMESPACE, key)
    except Exception:
        return []
    return [s for s in (stored or []) if isinstance(s, dict)]


def _set_bucket(key: str, suppliers: List[Dict[str, Any]]) -> bool:
    return platform_store.set(_NAMESPACE, key, suppliers)


def _contact_key(supplier: Dict[str, Any]) -> Optional[str]:
    """A light dedup key for 'have I already remembered essentially this same supplier in this
    bucket' - email first, else the bare hostname of whatever link is available. Deliberately
    simpler than outreach_discovery.normalize_email_key/normalize_url_key (which preserve the
    full path, for matching a specific listing page) - this only ever compares entries WITHIN
    one already-narrow (country, theme) bucket, so a same-domain match is precise enough to
    call it a repeat add, not a coincidence."""
    email = (supplier.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    url = str(supplier.get("website") or supplier.get("listingUrl") or supplier.get("social") or "").strip().lower()
    if not url:
        return None
    url = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    host = url.split("/")[0].split("?")[0]
    host = re.sub(r"^www\d*\.", "", host)
    return f"url:{host}" if host else None


def remember_supplier(country: str, theme: str, supplier: Dict[str, Any]) -> bool:
    """Stores one manually-added supplier under (country, theme) so a future search for the
    same combination automatically resurfaces it. Returns True only if something was actually
    newly written.

    Silently declines (returns False) rather than raising when: there's no country to key on;
    the supplier has neither an email nor any link to identify it by later (a bare name alone
    can't be de-duplicated against, or matched back to anything a future search would find, so
    it isn't a useful memory entry); or an entry with the same contact already exists in this
    exact bucket. The last case is deliberate, not an edge case to avoid: outreach_tool.py calls
    this on every rerun of the review screen for every currently-manually-added supplier (see its
    own docstring for why calling it repeatedly has to be a safe no-op, not a growing list of
    duplicates)."""
    key = _bucket_key(country, theme)
    if not key:
        return False
    contact_key = _contact_key(supplier)
    if not contact_key:
        return False

    bucket = _get_bucket(key)
    if any(_contact_key(existing) == contact_key for existing in bucket):
        return False

    entry = dict(supplier)
    entry["learnedFrom"] = {
        "country": (country or "").strip(),
        "theme": (theme or "").strip(),
        "rememberedAt": datetime.now(timezone.utc).isoformat(),
    }
    bucket.append(entry)
    return _set_bucket(key, bucket)


def get_remembered_for(country: str, theme: str) -> List[Dict[str, Any]]:
    """Raw remembered entries for this exact (country, theme) combination - copies, so a caller
    mutating one entry (e.g. resurface_remembered_suppliers rewriting selectionReason) never
    touches stored state by accident. Never raises - a store that's down means nothing gets
    remembered/resurfaced this run, which is visibly a normal empty result, not a crash."""
    key = _bucket_key(country, theme)
    if not key:
        return []
    return [dict(s) for s in _get_bucket(key)]


def resurface_remembered_suppliers(country: str, theme: str) -> List[Dict[str, Any]]:
    """Remembered suppliers for this exact (country, theme), ready to merge straight into a
    fresh outreach_discovery.discover_suppliers() result: each has its selectionReason rewritten
    to say plainly it's a remembered entry (not a fresh find) and carries isRemembered=True, and
    any entry whose domain is now on the block-list is dropped - being added by hand once does
    not exempt a domain from later being blocked (see outreach_memory.py)."""
    # Imported here, not at module load, to keep this module's only hard dependency at import
    # time on platform_store - is_blocked only needs to exist by the time this function actually
    # runs, and outreach_memory itself has no dependency back on this module either way, so
    # there's no real circularity risk - this is just keeping the import next to its one use.
    from outreach_memory import extract_domain, is_blocked

    out = []
    for entry in get_remembered_for(country, theme):
        contact_url = entry.get("website") or entry.get("listingUrl") or entry.get("social")
        if contact_url and is_blocked(extract_domain(contact_url)):
            continue
        learned = entry.pop("learnedFrom", {}) or {}
        remembered_at = (learned.get("rememberedAt") or "")[:10]  # just the date
        entry["selectionReason"] = (
            f"Remembered — added by hand for a \"{learned.get('theme') or theme}\" search in "
            f"{learned.get('country') or country}" + (f" on {remembered_at}" if remembered_at else "") + "."
        )
        entry["isRemembered"] = True
        out.append(entry)
    return out


def list_all() -> List[Dict[str, Any]]:
    """Every remembered supplier across every (country, theme) bucket, flattened for an admin/
    review table - each row carries its own country/theme back out (the bucket key itself is an
    internal normalized string, not something to show a human)."""
    try:
        namespace = platform_store.get_namespace(_NAMESPACE)
    except Exception:
        return []
    rows = []
    for suppliers in namespace.values():
        if not isinstance(suppliers, list):
            continue
        for s in suppliers:
            if not isinstance(s, dict):
                continue
            learned = s.get("learnedFrom") or {}
            rows.append({
                "id": s.get("id"),
                "country": learned.get("country") or "",
                "theme": learned.get("theme") or "",
                "name": s.get("name") or "",
                "email": s.get("email") or "",
                "website": s.get("website") or "",
                "rememberedAt": learned.get("rememberedAt") or "",
            })
    rows.sort(key=lambda r: r["rememberedAt"], reverse=True)
    return rows


def forget_supplier(country: str, theme: str, supplier_id: str) -> bool:
    """Removes one remembered supplier from its (country, theme) bucket by id. Returns True only
    if something was actually removed."""
    key = _bucket_key(country, theme)
    if not key:
        return False
    bucket = _get_bucket(key)
    remaining = [s for s in bucket if s.get("id") != supplier_id]
    if len(remaining) == len(bucket):
        return False
    return _set_bucket(key, remaining)
