"""
weekly_review.py — the app asks, once a week, what it should have understood by now.

CONFIRMED PRODUCT-OWNER REQUEST: "Could we also include, that the integrated AI tool will ask me
once a week, if it needs clarification. So we can constantly improve the included databank
information."

WHAT THIS IS NOT: a model inventing questions to look busy. Every question here is derived from
something the platform has actually observed in its own memory - a correction typed on three
different suppliers, a rule stated five times, a value mapping that keeps recurring. If nothing
has been observed, nothing is asked, and the week passes in silence. A weekly prompt that asks
for the sake of asking gets dismissed unread within a month, and then the one week it had
something real to say is dismissed too.

WHAT AN ANSWER DOES: "Yes" promotes the observation into a HOUSE RULE - fed into every future
extraction of that product type, for every supplier. That is the whole point: the thing being
repeated stops being repeated. "No" records the dismissal so the same question is not asked
again next week.

Nothing here writes to Travel Compositor. It only edits the platform's own memory.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-03-google-maps-url-coordinates"

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

import platform_store
import extraction_memory

_NAMESPACE = "weekly_review"
_STATE_KEY = "state"

# CONFIRMED (full-app audit LOW, 2026-09-02): this module's whole state (last-reviewed date AND
# every dismissal) lives in one JSON blob, read-modify-written with no compare-and-swap. Two
# concurrent sessions each dismissing a DIFFERENT question around the same time could race: both
# read the same starting state, both compute their own change, and whichever write lands second
# silently overwrote the first session's dismissal entirely - it would resurface as if never
# answered. platform_store has no true compare-and-swap primitive (get/set/delete only), so this
# narrows the race rather than eliminating it: re-reads the state immediately before writing and
# retries against whatever's actually there if it changed underneath, instead of blindly
# clobbering it. A genuinely simultaneous write within the final re-read-and-write instant can
# still race in principle - true elimination would need row-level locking in platform_store
# itself - but this closes the much larger, easily-hit window the plain read-then-write had.
_CAS_RETRIES = 5

REVIEW_INTERVAL_DAYS = 7

# How often a correction must recur before it is worth asking about. Two different suppliers is
# the strongest signal available that something is true of the trade rather than of a company;
# five repetitions on ONE supplier is the second-strongest.
_MIN_SUPPLIERS_FOR_PROMOTION = 2
_MIN_REPEATS_ON_ONE_SUPPLIER = 4
_MAX_QUESTIONS = 5


def _now():
    return datetime.now(timezone.utc)


def _state() -> Dict[str, Any]:
    row = platform_store.get(_NAMESPACE, _STATE_KEY)
    return row if isinstance(row, dict) else {}


def _save_state(state: Dict[str, Any]) -> bool:
    return platform_store.set(_NAMESPACE, _STATE_KEY, state)


def _read_modify_write(mutate: Callable[[Dict[str, Any]], None]) -> bool:
    """Applies `mutate` (mutates the passed-in state dict in place) against the freshest
    available read of the state, retrying if another session's write is found to have landed
    between the read and the write - see the _CAS_RETRIES comment above for what this does and
    does not guarantee."""
    for _ in range(_CAS_RETRIES):
        state = _state()
        before = json.dumps(state, sort_keys=True, default=str)
        mutate(state)
        latest = _state()
        if json.dumps(latest, sort_keys=True, default=str) != before:
            continue  # someone else wrote in between - retry against their newer state
        return _save_state(state)
    # Contention persisted through every retry - write anyway rather than silently dropping the
    # operator's action; a very rare double-write here is preferable to it never landing at all.
    state = _state()
    mutate(state)
    return _save_state(state)


def last_reviewed_at():
    raw = _state().get("last_reviewed_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def days_since_review():
    last = last_reviewed_at()
    if last is None:
        return None
    return (_now() - last).days


def mark_reviewed() -> bool:
    def _mutate(state):
        state["last_reviewed_at"] = _now().isoformat()
    return _read_modify_write(_mutate)


def snooze(days: int = REVIEW_INTERVAL_DAYS) -> bool:
    """Push the next check-in out without answering. Recorded as a review so the banner does not
    reappear on the next page load - being nagged is how a useful prompt becomes an ignored one."""
    def _mutate(state):
        state["last_reviewed_at"] = (_now() - timedelta(days=REVIEW_INTERVAL_DAYS - max(1, days))).isoformat()
    return _read_modify_write(_mutate)


def _dismissed() -> Dict[str, str]:
    d = _state().get("dismissed")
    return d if isinstance(d, dict) else {}


def dismiss(question_id: str) -> bool:
    def _mutate(state):
        dismissed = state.get("dismissed")
        if not isinstance(dismissed, dict):
            dismissed = {}
        dismissed[question_id] = _now().isoformat()
        state["dismissed"] = dismissed
    return _read_modify_write(_mutate)


def is_due() -> bool:
    """True when a week has passed. The FIRST run is not due immediately - a brand-new install
    has nothing to review, and opening with a check-in nobody can answer teaches the operator to
    close it unread."""
    last = last_reviewed_at()
    if last is None:
        mark_reviewed()
        return False
    return (_now() - last) >= timedelta(days=REVIEW_INTERVAL_DAYS)


def _observations() -> List[Dict[str, Any]]:
    """Every learned instruction, grouped by (product type, wording), across all suppliers."""
    grouped: Dict[Any, Dict[str, Any]] = {}
    for row in extraction_memory.list_all_instructions():
        supplier = str(row.get("supplier_id") or "")
        product = str(row.get("product_type") or "")
        text = (row.get("text") or "").strip()
        if not (product and text) or supplier == extraction_memory.HOUSE_SCOPE:
            continue
        key = (product, extraction_memory._instruction_key(text))
        bucket = grouped.setdefault(key, {"product_type": product, "text": text,
                                          "suppliers": set(), "count": 0})
        bucket["suppliers"].add(supplier)
        bucket["count"] += int(row.get("count", 1) or 1)
        # Keep the longest wording seen - it is usually the one with the reasoning in it.
        if len(text) > len(bucket["text"]):
            bucket["text"] = text
    return list(grouped.values())


def pending_questions() -> List[Dict[str, Any]]:
    """What the platform would like clarified, derived only from what it has observed.

    Ordered so the strongest evidence is asked first, and capped: a list of five is read, a list
    of thirty is closed."""
    already_house = {}
    dismissed = _dismissed()
    questions = []

    for obs in _observations():
        product = obs["product_type"]
        if product not in already_house:
            already_house[product] = {
                extraction_memory._instruction_key(r.get("text", ""))
                for r in extraction_memory.list_house_rules(product)
            }
        key = extraction_memory._instruction_key(obs["text"])
        if key in already_house[product]:
            continue                                  # already a house rule; nothing to ask
        question_id = f"{product}|{key}"
        if question_id in dismissed:
            continue

        suppliers = len(obs["suppliers"])
        if suppliers >= _MIN_SUPPLIERS_FOR_PROMOTION:
            why = (f"You have had to say this on {suppliers} different suppliers' documents, "
                   f"which usually means it is true of {product}s in general rather than of one "
                   f"supplier.")
            strength = (2, suppliers, obs["count"])
        elif obs["count"] >= _MIN_REPEATS_ON_ONE_SUPPLIER:
            why = (f"You have had to say this {obs['count']} times. Repeating a correction that "
                   f"often usually means it is a basic the AI should simply know.")
            strength = (1, suppliers, obs["count"])
        else:
            continue

        questions.append({
            "id": question_id,
            "product_type": product,
            "text": obs["text"],
            "why": why,
            "suppliers": suppliers,
            "count": obs["count"],
            "_strength": strength,
        })

    questions.sort(key=lambda q: q["_strength"], reverse=True)
    return questions[:_MAX_QUESTIONS]


def accept(question: Dict[str, Any]) -> bool:
    """Promote an observation to a house rule, so it stops needing to be said."""
    ok = extraction_memory.add_house_rule(question["product_type"], question["text"])
    dismiss(question["id"])          # answered either way - never ask it again
    return ok
