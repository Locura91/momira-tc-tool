"""
numeric_helpers.py — the one place `_safe_float`/`_safe_int` live.

WHY THIS EXISTS (2026-09-13, product owner: "is there a chance we could merge some files... maybe
we can find some double written codes that could be combined"): before this module existed,
`_safe_float`/`_safe_int` were independently copy-pasted into builder.py, ui_components.py,
bulk_notes.py and price_audit.py. Two of those four copies (builder.py, ui_components.py) had
been hardened after a real production crash; the other two (bulk_notes.py, and - worse -
price_audit.py, the tool built specifically to catch price mistakes) had silently fallen behind
and still carried the unguarded version. Copy-pasting a helper is how that kind of drift happens:
every copy has to be remembered and fixed by hand, forever, and nothing enforces that they stay
in sync. One shared implementation removes the chance of a fifth copy quietly diverging again.

CONFIRMED FIX (real production crash, LXR-3): "Out of range float values are not JSON compliant:
nan" - the `requests` library explicitly disallows NaN when serializing a `json=` payload (unlike
Python's own json.dumps, which allows it by default), so any NaN float reaching a numeric payload
field crashes at publish time with exactly this error.

NaN commonly reaches these helpers from a blank Streamlit data_editor cell: when a numeric column
mixes a blank row with other rows holding real numbers, pandas silently promotes the blank cell to
NaN (float) to keep the column's dtype consistent. CRITICAL: NaN is TRUTHY in Python (only
0/0.0/None/""/False are falsy), so the common "value or 0" guard does NOT catch it - float(nan or
0) still returns nan, not 0. `_safe_float`/`_safe_int` check for NaN (and Infinity, equally invalid
JSON) explicitly, on top of the normal None/non-numeric cases float()/int() themselves would raise
on.

pandas is an optional dependency of this check, not a hard import: `pd.isna` catches a few edge
cases isinstance(value, float) already narrows to, but the math.isnan/isinf check below covers the
same ground without pandas, so this module has no pandas import of its own.
"""
import math

MODULE_BUILD = "2026-09-18-holiday-package-fetch-failed-diagnostics"


def _safe_float(value, fallback=0.0):
    if value is None:
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(result) or math.isinf(result):
        return fallback
    return result


def _safe_int(value, fallback=0):
    """Same NaN/Infinity/non-numeric safety as _safe_float, but returns an int."""
    result = _safe_float(value, fallback=None)
    return fallback if result is None else int(result)
