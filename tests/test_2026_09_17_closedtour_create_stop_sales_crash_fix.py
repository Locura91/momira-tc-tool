"""Regression tests for a real production publish-blocking crash (2026-09-17), reported right
after the fixed_departure_dates feature (test_2026_09_17_closedtour_fixed_departure_dates.py) was
shipped, on a REAL ClosedTour built from the exact same Movenpick MS Darakum cruise documents used
to validate that feature:

    "i have a problem that i cannot publish this closedtour and I cannot got back to solve it"
    ... "Couldn't prepare ASW-12's payload. These fields need attention: Stop sales row 1 -
    'start' was left blank - enter a number, or clear that whole column if it isn't sold. * Stop
    sales row 1 - 'end' was left blank ..."

ROOT CAUSE: fixed_departure_dates (and guaranteed_departure_rule) were only ever wired into
extract_modality_data/extract_option_only_data - NOT extract_structured_data, which is what the
"Create a brand-new ClosedTour" action (Step 1's default, and the one Chris actually used to build
ASW-12) calls. Seeing a sailing-dates table it had no proper field for, the AI tried to represent
those dates directly in stop_sales itself, producing (at least) one entry with a missing/invalid
start or end - which extracts cleanly (no exception) but fails Travel Compositor's own payload
validation at publish time with the cryptic error above.

FIX (two layers, both in ai_extractor.py):
  1. extract_structured_data's own prompt (EXTRACTION_SYSTEM_PROMPT)/tool schema
     (EXTRACTION_TOOL_SCHEMA)/defaults now document and accept guaranteed_departure_rule and
     fixed_departure_dates, exactly like the Modality-scoped prompts already did, and the
     function now calls the same _apply_guaranteed_departure_rule/_apply_fixed_departure_dates
     helpers before returning - so a sailing-dates table produces real, fully-formed stop_sales
     ranges instead of the AI trying to invent something itself.
  2. A new, independent safety net - _drop_incomplete_stop_sales_entries - runs at the end of all
     three ClosedTour extraction paths (create/add_option/update_option) and silently strips any
     stop_sales entry that ISN'T a dict with a genuinely parseable ISO start AND end, flagging the
     drop in schedule_notes instead of letting a half-formed entry reach publish-time validation
     completely invisibly. This is a defensive net independent of fix #1 - it catches the same
     failure mode even if the AI ignores the new prompt guidance.
"""
import datetime

import ai_extractor


# ---------------------------------------------------------------------------------------------
# Layer 2: _drop_incomplete_stop_sales_entries - the defensive net
# ---------------------------------------------------------------------------------------------

def test_drops_entries_missing_start_or_end():
    data = {
        "stop_sales": [
            {"start": "2027-02-12", "end": ""},          # exactly the real ASW-12 failure shape
            {"start": None, "end": "2027-03-10"},
            {"start": "2027-01-01", "end": "2027-01-31"},  # well-formed, must survive
        ],
        "schedule_notes": "",
    }
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert data["stop_sales"] == [{"start": "2027-01-01", "end": "2027-01-31"}]


def test_drops_non_dict_entries():
    data = {"stop_sales": ["2027-02-12", 42, {"start": "2027-01-01", "end": "2027-01-31"}], "schedule_notes": ""}
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert data["stop_sales"] == [{"start": "2027-01-01", "end": "2027-01-31"}]


def test_drops_entries_with_unparseable_dates():
    data = {"stop_sales": [{"start": "not-a-date", "end": "2027-01-31"}], "schedule_notes": ""}
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert data["stop_sales"] == []


def test_notes_the_drop_so_its_visible_on_the_review_screen():
    data = {"stop_sales": [{"start": "", "end": ""}], "schedule_notes": ""}
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert "1 incomplete Stop Sales entry" in data["schedule_notes"]


def test_is_a_noop_when_every_entry_is_well_formed():
    data = {"stop_sales": [{"start": "2027-01-01", "end": "2027-01-31"}], "schedule_notes": "existing note"}
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert data["stop_sales"] == [{"start": "2027-01-01", "end": "2027-01-31"}]
    assert data["schedule_notes"] == "existing note"  # untouched - nothing was dropped


def test_is_a_noop_on_empty_or_missing_stop_sales():
    data = {"stop_sales": [], "schedule_notes": ""}
    ai_extractor._drop_incomplete_stop_sales_entries(data)
    assert data["stop_sales"] == []

    data2 = {"schedule_notes": ""}
    ai_extractor._drop_incomplete_stop_sales_entries(data2)
    assert data2.get("stop_sales", []) == []


# ---------------------------------------------------------------------------------------------
# Layer 1: extract_structured_data now wires guaranteed_departure_rule/fixed_departure_dates,
# same as the Modality-scoped extraction functions already did.
# ---------------------------------------------------------------------------------------------

def _base_fake_response(**overrides):
    fake = {
        "tour_name": "13 Days Nile Cruise - Aswan to Cairo", "description": "", "hotels_text": "",
        "hotels_count": 1, "supplements": [], "included": "", "excluded": "", "meeting_point": "",
        "policy_remarks": "", "what_to_bring": "", "cancellation_policy_tiers": [],
        "cancellation_policy_text": "", "itinerary_destinations": ["Aswan", "Cairo"], "nights": 12,
        "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
        "child_discount_percentage": None, "extra_child_allowed": True,
        "extra_child_max_overrides": {"single": None, "double": None, "triple": None, "quadruple": None},
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "pricing_notes": "", "stop_sales": [], "price_list": [],
        "release_days_mentions": [], "guaranteed_departure_rule": None, "fixed_departure_dates": None,
    }
    fake.update(overrides)
    return fake


def test_extract_structured_data_wires_fixed_departure_dates(monkeypatch):
    # Real dates from the actual Movenpick MS Darakum sailing-dates table.
    fake_response = _base_fake_response(
        fixed_departure_dates=["2027-02-12", "2027-03-10", "2027-04-05"],
    )
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    assert any(r.get("start") and r.get("end") for r in data["stop_sales"])
    assert "Fixed departure dates" in data["schedule_notes"]


def test_extract_structured_data_wires_guaranteed_departure_rule(monkeypatch):
    fake_response = _base_fake_response(
        guaranteed_departure_rule={
            "weekday": "MONDAY", "ordinals": [1, 3], "range_start": "2026-11-01",
            "range_end": "2027-10-31", "min_pax_otherwise": 4,
        },
    )
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    assert data["operational_days"] == ["MONDAY"]
    assert any(r.get("start") and r.get("end") for r in data["stop_sales"])


def test_extract_structured_data_sanitizes_a_malformed_ai_added_stop_sales_entry(monkeypatch):
    # Reproduces the EXACT real failure: the AI, with no fixed_departure_dates field to use,
    # stuffs a half-formed entry straight into stop_sales instead.
    fake_response = _base_fake_response(
        stop_sales=[{"start": "2027-02-12", "end": ""}, {"start": "2027-05-01", "end": "2027-05-15"}],
    )
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    # The malformed one must be gone - this is what used to reach build_closed_tour_payloads and
    # fail pydantic validation at publish time with "Stop sales row 1 - 'start' was left blank".
    assert {"start": "2027-02-12", "end": ""} not in data["stop_sales"]
    for row in data["stop_sales"]:
        assert row.get("start") and row.get("end")
    # The well-formed one survives untouched.
    assert {"start": "2027-05-01", "end": "2027-05-15"} in data["stop_sales"]
    assert "incomplete Stop Sales entry" in data["schedule_notes"]


def test_extract_structured_data_defaults_new_fields_when_ai_omits_them(monkeypatch):
    fake_response = _base_fake_response()
    del fake_response["guaranteed_departure_rule"]
    del fake_response["fixed_departure_dates"]
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    assert data["guaranteed_departure_rule"] is None
    assert data["fixed_departure_dates"] is None
    assert data["stop_sales"] == []


def test_prompt_and_schema_document_the_new_fields():
    assert "fixed_departure_dates" in ai_extractor.EXTRACTION_SYSTEM_PROMPT
    assert "guaranteed_departure_rule" in ai_extractor.EXTRACTION_SYSTEM_PROMPT
    assert "guaranteed_departure_rule" in ai_extractor.EXTRACTION_TOOL_SCHEMA["properties"]
    assert "fixed_departure_dates" in ai_extractor.EXTRACTION_TOOL_SCHEMA["properties"]
    assert "guaranteed_departure_rule" in ai_extractor.EXTRACTION_TOOL_SCHEMA["required"]
    assert "fixed_departure_dates" in ai_extractor.EXTRACTION_TOOL_SCHEMA["required"]


def test_real_movenpick_sailing_dates_through_the_full_create_path(monkeypatch):
    # End-to-end reproduction of the exact real scenario: the AI reads the sailing-dates table
    # and (now that the field exists) correctly uses fixed_departure_dates rather than trying to
    # invent something in stop_sales itself.
    fake_response = _base_fake_response(
        fixed_departure_dates=["2027-02-12", "2027-03-10", "2027-04-05", "2027-05-01",
                                "2027-09-01", "2027-09-27", "2027-10-23"],
    )
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    # Every stop_sales row must be publish-safe: a real, parseable start AND end.
    assert len(data["stop_sales"]) == 7
    for row in data["stop_sales"]:
        assert ai_extractor._is_parseable_iso_date(row["start"])
        assert ai_extractor._is_parseable_iso_date(row["end"])
