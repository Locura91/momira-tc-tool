"""
text_normalize.py — the one shared name-normalization function used by every "does this new
document reference the same existing record" matcher in the platform.

WHY THIS EXISTS (2026-09-13, product owner: "is there a chance we could merge some files...
maybe we can find some double written codes that could be combined"): this exact function used
to be copy-pasted, byte-for-byte, into hotel_matcher.py and masterdata_matcher.py -
masterdata_matcher.py's own copy even said in its docstring "Same normalization approach as
hotel_matcher._norm ... deliberately consistent across the app's matching modules," which is
exactly the kind of comment that can't actually guarantee anything: nothing enforced the two
copies staying in sync, it only recorded the intention that they should.

That gap was real, not hypothetical: transfer_matcher.py and transport_matcher.py's own route-key
normalization (`_route_key`'s nested `norm()`) was a THIRD, independently-written copy that never
received the NFKC-Unicode-normalization fix confirmed in the 2026-08-30 audit (see the history in
this function's own docstring below) - a smart quote, full-width character, or stray tab left
over from PDF extraction in a departure/arrival name could silently miss the app's own remembered
route mapping. Low-severity here specifically (a miss just costs one extra human confirmation
step, since transfer_matcher.py/transport_matcher.py always fall back to a human-confirmed fuzzy
match rather than auto-deciding) - but it is the same root gap, and the whole point of having ONE
shared function is that a fix landing here reaches every caller, instead of relying on a comment
to keep three independent copies in sync by hand.
"""
import re
import unicodedata
from typing import Optional

MODULE_BUILD = "2026-09-22-transport-missing-reverse-scan-and-batch-create"


def normalize_name(s: Optional[str]) -> str:
    """Normalizes a name for matching - case/outer-whitespace-insensitive. CONFIRMED FIX
    (2026-08-30 audit, known issue flagged in full-app-audit-2026-08-28.md): also NFKC-normalizes
    Unicode (so a smart quote, full-width character, or combining-accent variant of the same text
    compares equal) and collapses any run of INTERNAL whitespace - a double space, a tab, a stray
    newline left over from PDF text extraction - down to one. Before this, a room name re-typed
    with a double space, or extracted with a tab where a space should be, silently failed to match
    the existing room by name - treating an unchanged room as brand-new, losing its real
    providerCode, and creating a duplicate room in Travel Compositor instead of updating the
    existing one (same failure mode for rates/seasons/offers, which all reuse this function).

    Deliberately stops short of fuzzy/similarity matching (e.g. Levenshtein distance) - that
    would risk the opposite and worse failure: silently merging two DIFFERENTLY-NAMED rooms
    ("Deluxe Room" and "Deluxe Suite") into one match and overwriting the wrong room's data."""
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", normalized).strip().lower()
