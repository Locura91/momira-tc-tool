"""Regression tests for hotel_automap.dismiss() (2026-09-16). Product owner, after deleting a
test hotel directly in Travel Compositor:

    "I deleted the created hotels, becuase the prices were wrong and the matches not included -
    thereforee it was useless data, but the app still shows that i have to match it. 1 hotel(s)
    still need attention."

The automap checklist had no way to reflect a deletion that happened entirely on Travel
Compositor's side (same read-only-API constraint documented in hotel_automap.py's own module
docstring), and the only existing resolution action - mark_mapped() - would misrepresent the
audit trail for a hotel that was never actually mapped, only deleted. dismiss() is a separate
resolution path with its own timestamp/reason, kept in the store (not hard-deleted) so the audit
trail still answers "was this one ever dealt with, and how".

Covers: hotel_automap.dismiss()/list_dismissed()/the updated list_pending() exclusion logic, and
the new "No longer applicable" popover + Dismissed expander wiring in app_helpers.py's
render_hotel_automap_review.
"""
import os

import hotel_automap


# ---------------------------------------------------------------------------------------------
# hotel_automap.dismiss() / list_dismissed() / list_pending()
# ---------------------------------------------------------------------------------------------

def _record(supplier_id="51758", provider_code="HRG-H1"):
    hotel_automap.record_pending(
        supplier_id, provider_code, hotel_name="Steigenberger Golf Resort El Gouna",
        accommodation_id="15285", giata_id="22166",
        master_name="Steigenberger Golf Resort El Gouna",
    )


def test_dismiss_marks_a_pending_entry_as_dismissed_not_mapped():
    _record("dismiss-1", "HRG-H1")
    assert hotel_automap.dismiss("dismiss-1", "HRG-H1", reason="deleted in Travel Compositor") is True
    entry = hotel_automap.get("dismiss-1", "HRG-H1")
    assert entry["dismissed_at"] is not None
    assert entry["dismiss_reason"] == "deleted in Travel Compositor"
    assert not entry.get("mapped_at")


def test_dismiss_returns_false_for_an_unknown_entry():
    assert hotel_automap.dismiss("nope", "no-such-code") is False


def test_dismiss_with_blank_reason_stores_none_not_empty_string():
    _record("dismiss-2", "HRG-H2")
    hotel_automap.dismiss("dismiss-2", "HRG-H2", reason="   ")
    entry = hotel_automap.get("dismiss-2", "HRG-H2")
    assert entry["dismiss_reason"] is None


def test_dismiss_with_no_reason_argument_stores_none():
    _record("dismiss-3", "HRG-H3")
    hotel_automap.dismiss("dismiss-3", "HRG-H3")
    entry = hotel_automap.get("dismiss-3", "HRG-H3")
    assert entry["dismiss_reason"] is None


def test_list_pending_excludes_dismissed_entries():
    _record("dismiss-4", "HRG-H4")
    before = {r["provider_code"] for r in hotel_automap.list_pending() if r["supplier_id"] == "dismiss-4"}
    assert "HRG-H4" in before
    hotel_automap.dismiss("dismiss-4", "HRG-H4", reason="test cleanup")
    after = {r["provider_code"] for r in hotel_automap.list_pending() if r["supplier_id"] == "dismiss-4"}
    assert "HRG-H4" not in after


def test_list_pending_still_excludes_mapped_entries_as_before():
    _record("dismiss-5", "HRG-H5")
    hotel_automap.mark_mapped("dismiss-5", "HRG-H5")
    pending = {r["provider_code"] for r in hotel_automap.list_pending() if r["supplier_id"] == "dismiss-5"}
    assert "HRG-H5" not in pending


def test_list_dismissed_contains_the_dismissed_entry_with_its_reason():
    _record("dismiss-6", "HRG-H6")
    hotel_automap.dismiss("dismiss-6", "HRG-H6", reason="prices were wrong, useless data")
    dismissed = [r for r in hotel_automap.list_dismissed() if r["supplier_id"] == "dismiss-6"]
    assert len(dismissed) == 1
    assert dismissed[0]["dismiss_reason"] == "prices were wrong, useless data"


def test_list_dismissed_does_not_include_mapped_entries():
    _record("dismiss-7", "HRG-H7")
    hotel_automap.mark_mapped("dismiss-7", "HRG-H7")
    dismissed = [r for r in hotel_automap.list_dismissed() if r["supplier_id"] == "dismiss-7"]
    assert dismissed == []


def test_list_mapped_does_not_include_dismissed_entries():
    _record("dismiss-8", "HRG-H8")
    hotel_automap.dismiss("dismiss-8", "HRG-H8")
    mapped = [r for r in hotel_automap.list_mapped() if r["supplier_id"] == "dismiss-8"]
    assert mapped == []


def test_dismiss_does_not_hard_delete_the_entry_unlike_forget():
    _record("dismiss-9", "HRG-H9")
    hotel_automap.dismiss("dismiss-9", "HRG-H9")
    # still retrievable - forget() would remove it entirely, dismiss() must not.
    assert hotel_automap.get("dismiss-9", "HRG-H9") is not None


def test_dismissing_an_entry_a_second_time_updates_the_reason_and_timestamp():
    _record("dismiss-10", "HRG-H10")
    hotel_automap.dismiss("dismiss-10", "HRG-H10", reason="first reason")
    first = hotel_automap.get("dismiss-10", "HRG-H10")["dismissed_at"]
    hotel_automap.dismiss("dismiss-10", "HRG-H10", reason="corrected reason")
    entry = hotel_automap.get("dismiss-10", "HRG-H10")
    assert entry["dismiss_reason"] == "corrected reason"
    assert entry["dismissed_at"] >= first


# ---------------------------------------------------------------------------------------------
# app_helpers.py wiring - the "No longer applicable" popover and Dismissed expander
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_HELPERS_PY = os.path.join(os.path.dirname(_HERE), "app_helpers.py")


def _read_app_helpers():
    with open(_APP_HELPERS_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_dismiss_popover_is_wired_into_the_automap_review():
    src = _read_app_helpers()
    assert 'st.popover("🗑️ No longer applicable")' in src
    assert "hotel_automap.dismiss(" in src


def test_dismiss_button_passes_the_optional_reason_text_input():
    src = _read_app_helpers()
    popover_idx = src.index('st.popover("🗑️ No longer applicable")')
    dismiss_call_idx = src.index("hotel_automap.dismiss(", popover_idx)
    block = src[popover_idx:dismiss_call_idx + 200]
    assert "automap_dismiss_reason_" in block
    assert "dismiss_reason" in block


def test_dismissed_expander_lists_hotel_automap_list_dismissed():
    src = _read_app_helpers()
    assert "hotel_automap.list_dismissed()" in src
    assert "Dismissed — no longer applicable" in src


def test_mark_as_done_button_still_present_alongside_the_new_dismiss_action():
    src = _read_app_helpers()
    assert "hotel_automap.mark_mapped(" in src
    assert 'st.button("✅ Mark as done"' in src
