"""Regression tests for consolidating `_safe_float`/`_safe_int` into numeric_helpers.py
(2026-09-13, product owner: "is there a chance we could merge some files... maybe we can find
some double written codes that could be combined").

Before this change, `_safe_float`/`_safe_int` were copy-pasted independently into builder.py,
ui_components.py, bulk_notes.py and price_audit.py. Two of the four copies (builder.py,
ui_components.py) had been hardened after a real production crash (LXR-3: a blank Streamlit
data_editor cell promotes to NaN, which is truthy in Python and slips past `value or 0`, then
crashes `requests`' json= serialization because NaN isn't valid JSON). The other two copies -
bulk_notes.py, and price_audit.py, the tool built specifically to catch price mistakes - had
silently fallen behind and still carried the unguarded version. price_audit.py's `_safe_int` also
rounded (`int(round(float(value)))`) where the canonical version truncates (`int(result)`) - a
second, independent divergence from the same copy-paste.

These tests prove: (1) every module that used to define its own copy now imports the single
shared implementation (so a future fix to the shared version reaches all four call sites, not
just whichever ones someone remembers to touch), and (2) the previously-unguarded modules
(bulk_notes.py, price_audit.py) now actually have the NaN/Infinity guard - i.e. the bug is fixed,
not just the file layout tidied.
"""
import math

import bulk_notes
import builder
import numeric_helpers
import price_audit
import ui_components


# ======================================================================
# 1. every module now points at the ONE shared implementation
# ======================================================================
def test_builder_safe_float_and_safe_int_are_the_shared_implementation():
    assert builder._safe_float is numeric_helpers._safe_float
    assert builder._safe_int is numeric_helpers._safe_int


def test_ui_components_safe_float_and_safe_int_are_the_shared_implementation():
    assert ui_components._safe_float is numeric_helpers._safe_float
    assert ui_components._safe_int is numeric_helpers._safe_int


def test_bulk_notes_safe_float_is_the_shared_implementation():
    assert bulk_notes._safe_float is numeric_helpers._safe_float


def test_price_audit_safe_float_and_safe_int_are_the_shared_implementation():
    assert price_audit._safe_float is numeric_helpers._safe_float
    assert price_audit._safe_int is numeric_helpers._safe_int


# ======================================================================
# 2. the bug itself: bulk_notes.py and price_audit.py used to crash-feed a
#    NaN straight through - now they don't, because they use the guarded version.
# ======================================================================
def test_bulk_notes_safe_float_no_longer_lets_nan_through():
    """Before this fix, bulk_notes._safe_float(float('nan')) returned nan itself (the naive
    copy had no NaN guard at all) - which would go on to crash requests' json= serialization
    at publish time exactly like LXR-3 did for builder.py's numeric fields."""
    result = bulk_notes._safe_float(float("nan"))
    assert not math.isnan(result)
    assert result == 0.0


def test_price_audit_safe_float_no_longer_lets_nan_or_infinity_through():
    assert price_audit._safe_float(float("nan")) == 0.0
    assert price_audit._safe_float(float("inf")) == 0.0
    assert price_audit._safe_float(float("-inf")) == 0.0


def test_price_audit_safe_int_no_longer_lets_nan_through():
    """price_audit._safe_int used to be int(round(float(value))) with no NaN guard at all -
    round(float('nan')) raises ValueError, which the bare except (TypeError, ValueError) around
    it DID catch, so this specific path happened to be harmless before - but only by accident,
    and it silently diverged from the canonical rounding behavior (round vs truncate) at the
    same time. This just confirms the shared, truncating implementation behaves the same for a
    genuine NaN."""
    assert price_audit._safe_int(float("nan")) == 0


def test_price_audit_safe_int_now_truncates_like_every_other_copy_instead_of_rounding():
    """The old price_audit-only copy rounded (2.6 -> 3); the canonical shared version
    truncates (2.6 -> 2), matching builder.py/ui_components.py. price_audit only ever calls
    _safe_int on adults/children counts, which the extraction schema already constrains to
    whole numbers, so this behavior change has no practical effect on real audit output - it
    just removes the silent divergence."""
    assert price_audit._safe_int(2.6) == 2


# ======================================================================
# 3. numeric_helpers.py itself carries the module-build stamp discovery relies on
# ======================================================================
def test_numeric_helpers_module_has_a_build_stamp():
    assert hasattr(numeric_helpers, "MODULE_BUILD")
    assert isinstance(numeric_helpers.MODULE_BUILD, str) and numeric_helpers.MODULE_BUILD


def test_bulk_notes_now_carries_a_module_build_stamp():
    """bulk_notes.py never had MODULE_BUILD before this change - a gap that meant a partial
    deploy touching only this file was invisible to app.py's own stale-module check."""
    assert hasattr(bulk_notes, "MODULE_BUILD")
    assert isinstance(bulk_notes.MODULE_BUILD, str) and bulk_notes.MODULE_BUILD
