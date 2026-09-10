"""Regression test for the product-owner rule (date_format.py's own docstring):

    "Please use always for Date: DD/MM/YYYY." (and, per the 2026-09-10 follow-up, DD.MM.YYYY
    must also be accepted - date_format.py's _DMY_RE already takes any of / . - as separator,
    so this was already true everywhere EXCEPT two fields added earlier in this same session.)

The Transfer/Transport dated-supplement period fields (mi_s_period_start/end,
mi_ts_period_start/end - added 2026-09-09 for the bulk price-supplement feature) were built with
raw "YYYY-MM-DD" labels/placeholders and no _iso()/_disp() conversion, unlike every other date
field in the app - an oversight from before the house DD/MM/YYYY convention was applied to them.
Fixed to match the same convention as every Valid-From/Valid-Until field elsewhere in app.py.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_transfer_supplement_period_dates_use_the_house_display_format():
    src = _read_app_py()
    assert 'item_data["start_date"] = _iso(st.text_input(' in src
    assert 'key="mi_s_period_start"' in src
    assert "YYYY-MM-DD" not in src.split('key="mi_s_period_start"')[0][-400:]


def test_transport_supplement_period_dates_use_the_house_display_format():
    src = _read_app_py()
    assert 'key="mi_ts_period_start"' in src
    assert 'key="mi_ts_period_end"' in src
    # Both wrapped in _iso(...) so whatever the human types (DD/MM/YYYY or DD.MM.YYYY) is
    # normalized to ISO before it ever reaches bulk_notes.
    block = src.split('key="mi_ts_period_start"')[0][-300:] + src.split('key="mi_ts_period_start"')[1][:300]
    assert "_iso(" in block


def test_no_raw_yyyy_mm_dd_labels_remain_on_the_supplement_period_fields():
    src = _read_app_py()
    for key in ("mi_s_period_start", "mi_s_period_end", "mi_ts_period_start", "mi_ts_period_end"):
        around = src.split(f'key="{key}"')[0][-300:]
        assert "YYYY-MM-DD" not in around


def test_date_format_module_accepts_both_slash_and_dot_separators():
    # The underlying acceptance rule this UI fix relies on - confirms date_format.py itself
    # (not just this one screen) already satisfies "DD/MM/YYYY or DD.MM.YYYY, both acceptable".
    import date_format
    assert date_format.to_iso_date("20/12/2026") == "2026-12-20"
    assert date_format.to_iso_date("20.12.2026") == "2026-12-20"
    assert date_format.to_iso_date("20-12-2026") == "2026-12-20"
