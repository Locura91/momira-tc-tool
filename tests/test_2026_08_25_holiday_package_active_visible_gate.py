"""Regression tests locking in the "very important" rule (confirmed 2026-08-25 while checking
this on request): a Holiday Package is only ever translated when it is BOTH active and visible
at the same time. Neither flag alone is enough - a package that's active but hidden, or visible
but inactive, must be skipped either way.

This logic already existed in sync_one_package_entry() (sync_holiday_package.py) as a prior
CONFIRMED BUG FIX, but had zero test coverage anywhere in the suite before this file - these
tests exist to prove, executably, that both single-package (sync_holiday_package) and
all-packages (sync_all_holiday_packages) translation paths enforce it, and to lock the current
correct behaviour in against a future regression.

Both paths route through the same sync_one_package_entry(), and the active/visible check runs
BEFORE anything touches api/translator/store - so a skip-path test can pass None for all three
without needing to fake the translation pipeline.
"""
from sync_holiday_package import sync_one_package_entry, sync_all_holiday_packages, _flag_state


def _entry(**overrides):
    base = {"id": 12345, "title": "A Wonderful Trip", "active": True, "visible": True}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The core "both at the same time" rule
# ---------------------------------------------------------------------------

def test_active_false_skips_even_when_visible_is_true():
    result = sync_one_package_entry(None, None, None, "momiratravel",
                                     _entry(active=False, visible=True), ["FR"])
    assert result["status"] == "skipped"
    assert "active is false" in result["reason"]


def test_visible_false_skips_even_when_active_is_true():
    result = sync_one_package_entry(None, None, None, "momiratravel",
                                     _entry(active=True, visible=False), ["FR"])
    assert result["status"] == "skipped"
    assert "visible is false" in result["reason"]


def test_both_false_skips_on_the_active_check_first():
    result = sync_one_package_entry(None, None, None, "momiratravel",
                                     _entry(active=False, visible=False), ["FR"])
    assert result["status"] == "skipped"
    assert "active is false" in result["reason"]


def test_both_true_does_not_get_skipped_for_active_or_visible():
    """Package has no translatable text, so it still ends up 'skipped' - but for a completely
    different, unrelated reason. Proves active/visible=True lets it past THIS gate."""
    result = sync_one_package_entry(None, None, None, "momiratravel",
                                     _entry(active=True, visible=True, title=""), ["FR"])
    assert result["status"] == "skipped"
    assert "active is false" not in result["reason"]
    assert "visible is false" not in result["reason"]
    assert result["reason"] == "no translatable text fields found"


# ---------------------------------------------------------------------------
# Ambiguous/missing flags fall through rather than blocking everything - the exact real bug
# this logic was already written to avoid (see sync_one_package_entry's own docstring) - still
# worth pinning down so it's never silently reintroduced.
# ---------------------------------------------------------------------------

def test_missing_active_field_does_not_get_treated_as_false():
    entry = _entry(title="")
    del entry["active"]
    result = sync_one_package_entry(None, None, None, "momiratravel", entry, ["FR"])
    assert "active is false" not in result["reason"]


def test_missing_visible_field_does_not_get_treated_as_false():
    entry = _entry(title="")
    del entry["visible"]
    result = sync_one_package_entry(None, None, None, "momiratravel", entry, ["FR"])
    assert "visible is false" not in result["reason"]


def test_string_and_numeric_false_values_are_still_recognized():
    for falsy in ("false", "False", "no", "0", 0, 0.0):
        result = sync_one_package_entry(None, None, None, "momiratravel",
                                         _entry(active=falsy), ["FR"])
        assert result["status"] == "skipped"
        assert "active is false" in result["reason"], f"failed for active={falsy!r}"


def test_string_and_numeric_true_values_pass_the_gate():
    for truthy in ("true", "True", "yes", "1", 1, 1.0):
        result = sync_one_package_entry(None, None, None, "momiratravel",
                                         _entry(active=truthy, title=""), ["FR"])
        assert "active is false" not in result["reason"]


# ---------------------------------------------------------------------------
# Both call paths (single package, and the "all packages" loop) enforce the same rule
# ---------------------------------------------------------------------------

def test_sync_all_holiday_packages_skips_inactive_and_invisible_entries(monkeypatch):
    import sync_holiday_package as shp
    packages = [
        _entry(id=1, active=True, visible=True, title=""),   # passes gate, skipped for content
        _entry(id=2, active=False, visible=True),             # blocked: not active
        _entry(id=3, active=True, visible=False),             # blocked: not visible
    ]
    monkeypatch.setattr(shp, "fetch_all_holiday_packages", lambda api, microsite_id, limit=None: packages)
    results = sync_all_holiday_packages(None, None, None, "momiratravel", ["FR"])
    by_id = {r["package_id"]: r for r in results}
    assert "active is false" not in by_id[1]["reason"]
    assert "active is false" in by_id[2]["reason"]
    assert "visible is false" in by_id[3]["reason"]


def test_flag_state_handles_common_encodings():
    assert _flag_state(True) is True
    assert _flag_state(False) is False
    assert _flag_state("true") is True
    assert _flag_state("false") is False
    assert _flag_state(1) is True
    assert _flag_state(0) is False
    assert _flag_state(None) is None
    assert _flag_state("unclear") is None
