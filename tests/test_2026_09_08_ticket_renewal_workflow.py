"""Tests for the price-validity RENEWAL workflow (product owner, 2026-09-08 follow-up):

    "the idea must be also, that once we receive the new prices for the services like transfer
    transport and tickets, we must then add the new price according to the new list, we must
    exchange the code with the correct date... and we must shortly review the TICKET itinerary,
    if there has been made some changes."

Clarified (AskUserQuestion): checklist reminder on the edit screen (Recommended), and for the
itinerary check specifically: "the ai must review the current ticket information and check if
that is matching with the new ticket information with the new price list. Sometimes small
changes are done for a new season and we must detect that."

Two concrete gaps this closes:
  1. The "Price only" ticket-update path (the FAST path a real price-list update actually goes
     through) never showed the "price valid until" field at all, and its publish call
     (update_ticket_option) never touches Voucher Remarks - so the price-validity code could
     never actually be updated through that path. build_ticket_voucher_remarks_only_update()
     fixes this with a minimal, content-safe update_ticket payload.
  2. Nothing compared the new document's content against what's currently live, so a supplier
     quietly changing a meeting point/inclusion alongside a new season's prices could slip
     through unnoticed. check_ticket_content_drift() is the cheap, single-purpose AI check for
     that - NOT a second full extraction.
"""
import builder
import ai_extractor


# ---------------------------------------------------------------------------
# build_ticket_voucher_remarks_only_update
# ---------------------------------------------------------------------------

def _live_ticket(voucher_remarks="Meet at the harbor gate."):
    return {
        "code": "RAK-T1",
        "name": "Atlas Day Trip",
        "geolocation": {"latitude": 31.5, "longitude": -8.0},
        "city": "Marrakech",
        "currency": "EUR",
        "active": True,
        "duration": 8.0,
        "durationType": "HOURS",
        "modalityCodes": ["Standard"],
        "datasheets": {
            "EN": {
                "name": "Atlas Day Trip",
                "description": "A full day in the Atlas Mountains.",
                "meetingPoint": "Hotel lobby",
                "departureTime": "8:00 AM",
                "voucherRemarks": voucher_remarks,
                "includes": ["Lunch", "Guide"],
                "excludes": ["Tips"],
            }
        },
    }


def test_voucher_remarks_only_update_rewrites_only_the_code_leaves_everything_else_untouched():
    live = _live_ticket("Meet at the harbor gate.\n(20250101)")
    updated = builder.build_ticket_voucher_remarks_only_update(live, "2027-10-31")

    assert updated["datasheets"]["EN"]["voucherRemarks"] == "Meet at the harbor gate.\n(20271031)"
    # Everything else is untouched, byte-for-byte.
    assert updated["name"] == live["name"]
    assert updated["code"] == live["code"]
    assert updated["geolocation"] == live["geolocation"]
    assert updated["datasheets"]["EN"]["description"] == live["datasheets"]["EN"]["description"]
    assert updated["datasheets"]["EN"]["meetingPoint"] == live["datasheets"]["EN"]["meetingPoint"]
    assert updated["datasheets"]["EN"]["includes"] == live["datasheets"]["EN"]["includes"]


def test_voucher_remarks_only_update_does_not_mutate_the_original_live_dict():
    live = _live_ticket("Meet at the harbor gate.\n(20250101)")
    builder.build_ticket_voucher_remarks_only_update(live, "2027-10-31")
    assert live["datasheets"]["EN"]["voucherRemarks"] == "Meet at the harbor gate.\n(20250101)"


def test_voucher_remarks_only_update_appends_a_code_when_there_was_none_before():
    live = _live_ticket("Meet at the harbor gate.")
    updated = builder.build_ticket_voucher_remarks_only_update(live, "2027-10-31")
    assert updated["datasheets"]["EN"]["voucherRemarks"] == "Meet at the harbor gate.\n(20271031)"


def test_voucher_remarks_only_update_strips_the_code_when_date_is_blank():
    live = _live_ticket("Meet at the harbor gate.\n(20250101)")
    updated = builder.build_ticket_voucher_remarks_only_update(live, "")
    assert updated["datasheets"]["EN"]["voucherRemarks"] == "Meet at the harbor gate."


def test_voucher_remarks_only_update_handles_a_ticket_with_no_datasheets_key_at_all():
    live = {"code": "RAK-T1", "name": "Atlas Day Trip"}
    updated = builder.build_ticket_voucher_remarks_only_update(live, "2027-10-31")
    assert updated["datasheets"]["EN"]["voucherRemarks"] == "(20271031)"
    assert updated["code"] == "RAK-T1"


# ---------------------------------------------------------------------------
# check_ticket_content_drift
# ---------------------------------------------------------------------------

def _live_content():
    return {
        "name": "Atlas Day Trip", "description": "A full day in the Atlas Mountains.",
        "meetingPoint": "Hotel lobby", "departureTime": "8:00 AM",
        "includes": ["Lunch", "Guide"], "excludes": ["Tips"],
    }


def test_content_drift_reports_no_changes_when_the_ai_finds_none(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"has_changes": False, "changes": []})
    result = ai_extractor.check_ticket_content_drift("New 2027 price list, same excursion.", _live_content())
    assert result == {"has_changes": False, "changes": []}


def test_content_drift_surfaces_detected_changes(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {
        "has_changes": True,
        "changes": ["Meeting point changed from Hotel lobby to Central Square for the new season."],
    })
    result = ai_extractor.check_ticket_content_drift("New 2027 price list. Meet at Central Square.", _live_content())
    assert result["has_changes"] is True
    assert len(result["changes"]) == 1


def test_content_drift_defends_against_has_changes_true_but_empty_list_from_the_model(monkeypatch):
    # A dropped/inconsistent field from the model must never surface a scary "something changed"
    # banner with nothing to actually show for it.
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"has_changes": True, "changes": []})
    result = ai_extractor.check_ticket_content_drift("Just a price list.", _live_content())
    assert result == {"has_changes": False, "changes": []}


def test_content_drift_defends_against_missing_keys_entirely(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {})
    result = ai_extractor.check_ticket_content_drift("Just a price list.", _live_content())
    assert result == {"has_changes": False, "changes": []}


def test_content_drift_filters_out_non_string_or_blank_change_entries(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {
        "has_changes": True, "changes": ["A real change.", "", None, "   "],
    })
    result = ai_extractor.check_ticket_content_drift("Doc text.", _live_content())
    assert result["changes"] == ["A real change."]
