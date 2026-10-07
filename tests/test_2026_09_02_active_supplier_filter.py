"""Tests for the product-owner request (2026-09-02): "For the select supplier, please only
display active suppliers. All supplier who are inactive do not show in the dropdown menu."

is_active_supplier() (ui_components.py) is the single shared check every "Select Supplier"
dropdown across the app now goes through - see that function's own docstring for why this is
centralized rather than re-implemented at each of the ~10 call sites (the same
one-implementation-many-callers pattern already used for render_cancellation_policy_editor).

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern - the app.py/stop_sales_tool.py call sites are
verified by reading their source text and confirming is_active_supplier(...) is chained onto
every momira-prefix filter. is_active_supplier() itself and translation_tool.py's filtered
fetch are tested directly.
"""
import os

import ui_components as uic

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PY = os.path.join(_REPO_DIR, "app.py")
_STOP_SALES_TOOL_PY = os.path.join(_REPO_DIR, "stop_sales_tool.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_app_py():
    """app.py's source concatenated with every module under flows/ - Phase 1 (2026-09-15)
    started splitting render_*_flow functions out of app.py into flows/*.py, verbatim/zero-
    behaviour-change, so a momira-prefix filter that used to live in app.py (e.g. Hotel's
    Select Supplier dropdown) can now be in a flows/*.py file instead. Reading app.py first
    means this is purely additive to what _read(_APP_PY) already found there."""
    src = _read(_APP_PY)
    app_helpers_path = os.path.join(_REPO_DIR, "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        src += chr(10) + _read(app_helpers_path)
    flows_dir = os.path.join(_REPO_DIR, "flows")
    if os.path.isdir(flows_dir):
        for _name in sorted(os.listdir(flows_dir)):
            if _name.endswith(".py") and _name != "__init__.py":
                src += chr(10) + _read(os.path.join(flows_dir, _name))
    return src


# ======================================================================
# is_active_supplier itself
# ======================================================================
def test_active_true_is_shown():
    assert uic.is_active_supplier({"id": 1, "active": True}) is True


def test_active_false_is_hidden():
    assert uic.is_active_supplier({"id": 1, "active": False}) is False


def test_missing_active_field_defaults_to_shown():
    # CONFIRMED default: absence of the field must never blank out the whole dropdown.
    assert uic.is_active_supplier({"id": 1}) is True


def test_active_none_defaults_to_shown():
    assert uic.is_active_supplier({"id": 1, "active": None}) is True


def test_active_falsy_non_bool_still_hidden_only_when_literally_false():
    # 0 and "" are falsy but NOT the literal False the API sends - must not be misread as inactive.
    assert uic.is_active_supplier({"id": 1, "active": 0}) is True
    assert uic.is_active_supplier({"id": 1, "active": ""}) is True


# ======================================================================
# app.py - every momira-prefix supplier filter also checks is_active_supplier
# ======================================================================
def test_app_py_imports_is_active_supplier_from_ui_components():
    content = _read(_APP_PY)
    assert "is_active_supplier" in content
    # confirm it comes from the shared ui_components import block, not redefined locally
    assert "def is_active_supplier(" not in content


def _momira_helper_body():
    """The body of ui_components.momira_active_suppliers - the one place the Momira-prefix +
    active-only rule now lives (consolidation 2026-10-07)."""
    ui = _read(os.path.join(_REPO_DIR, "ui_components.py"))
    return ui.split("def momira_active_suppliers")[1].split("\ndef ")[0]


def test_the_shared_momira_helper_enforces_both_the_prefix_and_the_active_check():
    # CONSOLIDATION (2026-10-07): the ~10 hand-copied "startswith('momira_') and
    # is_active_supplier(s)" comprehensions were replaced by one helper,
    # ui_components.momira_active_suppliers. The guarantee this suite protects - a Momira filter
    # NEVER drops the active check - is now pinned on that single helper instead of on each copy.
    body = _momira_helper_body()
    assert 'startswith("momira_")' in body
    assert "is_active_supplier(" in body


def test_app_py_gets_the_momira_filter_via_the_shared_helper_not_an_inline_copy():
    content = _read_app_py()
    # The rule is enforced centrally now, so app.py must route through the helper...
    assert "momira_active_suppliers(" in content
    # ...and must not carry an inline momira-prefix filter that could silently skip the active
    # check (every such line was removed in the 2026-10-07 consolidation).
    for line in content.splitlines():
        if 'startswith("momira_")' in line:
            assert "is_active_supplier(" in line, f"inline momira filter re-introduced without active check: {line!r}"


# ======================================================================
# stop_sales_tool.py - same treatment
# ======================================================================
def test_stop_sales_tool_imports_is_active_supplier():
    content = _read(_STOP_SALES_TOOL_PY)
    assert "from ui_components import is_active_supplier" in content


def test_stop_sales_tool_momira_filter_also_checks_active():
    # Consolidation (2026-10-07): stop_sales_tool now routes through the shared helper too; the
    # active-check guarantee is pinned on the helper (see the helper test above). Here we just
    # confirm stop_sales_tool uses it and has no inline momira filter missing the active check.
    content = _read(_STOP_SALES_TOOL_PY)
    assert "momira_active_suppliers(" in content
    for line in content.splitlines():
        if 'startswith("momira_")' in line:
            assert "is_active_supplier(" in line


# ======================================================================
# translation_tool.py - deliberately not momira-restricted, but still active-only
# ======================================================================
def test_translation_tool_filters_inactive_suppliers():
    import translation_tool as tt

    class _FakeAPI:
        def get_all_suppliers(self):
            return [
                {"id": 1, "commercialName": "Active Co", "active": True},
                {"id": 2, "commercialName": "Inactive Co", "active": False},
                {"id": 3, "commercialName": "No Flag Co"},  # missing field -> shown
            ]

    tt.TranslationTCAPI = _FakeAPI  # type: ignore[attr-defined]
    tt._fetch_translation_suppliers.clear()  # bust the st.cache_data cache between tests
    result = tt._fetch_translation_suppliers()
    names = {name for _id, name in result}
    assert "Active Co" in names
    assert "No Flag Co" in names
    assert "Inactive Co" not in names
