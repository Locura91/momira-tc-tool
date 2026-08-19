"""Guardrail test: the AI Trip Idea feature must NEVER call anything past Travel Compositor's
QUOTE step.

CONFIRMED PRODUCT-OWNER BOUNDARY (2026-08-19): "important, we do not want to confirm it. We just
want to get a quote." Travel Compositor's real booking API follows a Quote -> Confirm -> Prebook
-> Book flow per product type (Accommodation, Transports, Transfer, Ticket, Closed Tour) - see
the "client-trip-prompt-idea" project note for the full endpoint list from Chris's own Swagger.
Confirm/Prebook/Book create real holds and real purchases against live inventory (real seats,
real rooms) - this feature is explicitly scoped to Quote only, forever, not just as a starting
point to expand later without re-confirming with the product owner first.

This test doesn't call any API - there's no live Travel Compositor client for this feature yet
(see the project note's "next steps"). Its job is to survive into whenever that client DOES get
built: it scans every trip_*.py module's SOURCE TEXT for anything that looks like a
Confirm/Prebook/Book call and fails loudly if one appears, rather than relying on a comment or a
human's memory to keep enforcing this boundary as the code grows. A genuinely new Quote-only
endpoint is fine to add; this test should never need touching for that. If it ever needs editing
to make a Confirm/Prebook/Book call pass, that's the product owner's decision to make explicitly,
not a refactor to route around quietly.
"""
import glob
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every trip_*.py module in the repo root - the AI Trip Idea prototype's own files. Deliberately
# NOT scoped to a fixed list of filenames, so a new trip_*.py file added later is automatically
# covered without anyone remembering to update this test too.
_TRIP_MODULE_PATHS = sorted(glob.glob(os.path.join(_REPO_ROOT, "trip_*.py")))

# Travel Compositor's real booking-lifecycle steps, per the Swagger endpoint list Chris shared -
# anything matching these in a trip_*.py file's source is exactly the boundary being guarded.
# Matched as whole path segments / identifier fragments to avoid flagging unrelated words that
# happen to contain "book" (e.g. "booking" alone, which appears throughout the QUOTE endpoints'
# own URLs like "/booking/accommodations/quote" and must NOT trip this check).
_FORBIDDEN_PATTERNS = [
    re.compile(r"/confirm\b", re.IGNORECASE),
    re.compile(r"/prebook\b", re.IGNORECASE),
    re.compile(r"/book\b", re.IGNORECASE),              # e.g. "/booking/accommodations/{id}/book"
    re.compile(r"(?<![a-zA-Z])confirm_\w*\(", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z])prebook_\w*\(", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z])book_\w*\(", re.IGNORECASE),
]


def test_at_least_one_trip_module_exists_so_this_guardrail_isnt_silently_vacuous():
    # If this ever fails, it means the trip_*.py naming convention changed - fix the glob above,
    # don't just delete this test.
    assert _TRIP_MODULE_PATHS, "expected trip_*.py files in the repo root"


@pytest.mark.parametrize("path", _TRIP_MODULE_PATHS, ids=[os.path.basename(p) for p in _TRIP_MODULE_PATHS])
def test_trip_module_never_references_a_confirm_prebook_or_book_call(path):
    with open(path, encoding="utf-8") as f:
        source = f.read()

    hits = []
    for pattern in _FORBIDDEN_PATTERNS:
        for m in pattern.finditer(source):
            line_no = source[:m.start()].count("\n") + 1
            hits.append(f"line {line_no}: {m.group(0)!r}")

    assert not hits, (
        f"{os.path.basename(path)} appears to reference a Confirm/Prebook/Book call, which the "
        f"product owner explicitly ruled out (2026-08-19): 'we do not want to confirm it, we "
        f"just want to get a quote'. Found: {hits}. If this is genuinely wanted now, that's a "
        f"product decision to confirm explicitly, not a test to quietly adjust."
    )
