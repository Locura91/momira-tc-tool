"""Regression test for the 2026-10-02 weekly duplicate-code audit's confirmed real bug fix:
flows/multi_modality.py's _save_mm_price_list callback was missing the same product-owner-
mandated "End date must be one day before next season start date" rule (see
builder.fix_touching_season_boundaries's docstring for the full quote/reasoning) that app.py (on
display) and flows/multi_tour.py's own equivalent manual-edit callback (on save) already applied -
so a human editing a Modality's price table in the multi-modality flow into two touching seasons
would have the overlap silently reach Travel Compositor's publish call unfixed, unlike the single-
tour flow.

flows/multi_modality.py can't be imported standalone in a test process - it's designed to be
loaded only via app.py's own controlled circular-import sequence (see the module's own docstring
on late-binding `from app import (...)`), same reason flows/multi_tour.py's own tests for this
exact pattern (test_2026_09_18_closedtour_season_date_range_fixes.py) inspect source text instead
of importing and calling the function directly.
"""
from conftest import read_flow_source


def _read_multi_modality_source() -> str:
    return read_flow_source("multi_modality.py")


def test_multi_modality_imports_fix_touching_season_boundaries():
    src = _read_multi_modality_source()
    assert "fix_touching_season_boundaries" in src
    import_idx = src.index("from builder import")
    import_line = src[import_idx:src.index("\n", import_idx)]
    assert "fix_touching_season_boundaries" in import_line


def test_save_mm_price_list_applies_touching_boundary_fix():
    src = _read_multi_modality_source()
    save_idx = src.index("def _save_mm_price_list(")
    save_body = src[save_idx:save_idx + 1700]
    assert "fix_touching_season_boundaries(" in save_body
