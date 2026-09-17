"""Regression tests for a real product-owner request (2026-09-17), screenshot of a real Nile
cruise sailing-dates table ("Movenpick MS Darakum Long Cruise Sailing Dates 2027" - 12 Feb, 10
Mar, 5 Apr, 1 May, 1 Sep, 27 Sep, 23 Oct):

    "I have the case, that a closed tour has the attached starting dates: In order that is the
    case and it does not follow a certain pattern, we must then include stop sales from today
    until one day before the first starting date; then next stop sales one day after first
    starting date until one day before next starting date and so on. So far the app does not
    understand this logic."

Fix: a new ai_extractor.fixed_departure_dates field (extracted alongside the existing
guaranteed_departure_rule, in both MODALITY_EXTRACTION_SYSTEM_PROMPT and OPTION_ONLY_SYSTEM_PROMPT
- same two extraction paths add_option/update_option use) captures an explicit, irregular list of
departure dates. ai_extractor.compute_stop_sales_from_fixed_departure_dates() does the exact gap
math in plain deterministic Python (mirroring the existing compute_non_guaranteed_stop_sales
pattern for the weekly/ordinal guaranteed-departure case), and
ai_extractor._apply_fixed_departure_dates() merges the computed gaps into data["stop_sales"] the
same way _apply_guaranteed_departure_rule already does, wired into both extract_modality_data()
(used by add_option) and extract_option_only_data() (used by update_option).
"""
import datetime

import ai_extractor


# ---------------------------------------------------------------------------------------------
# compute_stop_sales_from_fixed_departure_dates - the pure calendar math
# ---------------------------------------------------------------------------------------------

def test_real_movenpick_example_produces_the_exact_gaps_described():
    # Anchored to a fixed "today" so the test is deterministic regardless of when it's run.
    dates = ["2027-02-12", "2027-03-10", "2027-04-05", "2027-05-01", "2027-09-01",
             "2027-09-27", "2027-10-23"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    assert result == [
        {"start": "2026-09-17", "end": "2027-02-11"},  # today -> day before 1st departure
        {"start": "2027-02-13", "end": "2027-03-09"},  # day after 1st -> day before 2nd
        {"start": "2027-03-11", "end": "2027-04-04"},
        {"start": "2027-04-06", "end": "2027-04-30"},
        {"start": "2027-05-02", "end": "2027-08-31"},
        {"start": "2027-09-02", "end": "2027-09-26"},
        {"start": "2027-09-28", "end": "2027-10-22"},
        # nothing added after the last departure date (2027-10-23) - deliberately open-ended,
        # see the function's own docstring for why.
    ]


def test_unsorted_and_duplicate_input_dates_are_handled():
    dates = ["2027-04-05", "2027-02-12", "2027-02-12", "2027-03-10"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    assert result == [
        {"start": "2026-09-17", "end": "2027-02-11"},
        {"start": "2027-02-13", "end": "2027-03-09"},
        {"start": "2027-03-11", "end": "2027-04-04"},
    ]


def test_consecutive_calendar_days_produce_no_gap_between_them():
    dates = ["2027-02-12", "2027-02-13"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    # today -> day before the 1st, then nothing between the 1st and 2nd (zero-length gap skipped)
    assert result == [{"start": "2026-09-17", "end": "2027-02-11"}]


def test_a_departure_date_that_is_today_produces_no_leading_gap():
    dates = ["2026-09-17", "2027-01-01"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    assert result == [{"start": "2026-09-18", "end": "2026-12-31"}]


def test_a_departure_date_already_in_the_past_produces_no_negative_range():
    dates = ["2026-01-01", "2027-01-01"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    # the first "gap" (today -> the day before the past departure date) would be backwards -
    # skipped entirely, not reported as an invalid/reversed range. The gap after that past
    # departure date still gets blocked normally, starting the day right after it (not "today"),
    # since that day genuinely isn't a departure day either.
    assert result == [{"start": "2026-01-02", "end": "2026-12-31"}]


def test_single_date_only_produces_one_leading_gap():
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        ["2027-06-15"], today=datetime.date(2026, 9, 17))
    assert result == [{"start": "2026-09-17", "end": "2027-06-14"}]


def test_empty_or_unparseable_input_returns_empty_list():
    assert ai_extractor.compute_stop_sales_from_fixed_departure_dates([]) == []
    assert ai_extractor.compute_stop_sales_from_fixed_departure_dates(None) == []
    assert ai_extractor.compute_stop_sales_from_fixed_departure_dates(["not-a-date", ""]) == []


def test_date_objects_are_accepted_alongside_iso_strings():
    dates = [datetime.date(2027, 2, 12), "2027-03-10"]
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=datetime.date(2026, 9, 17))
    assert result == [
        {"start": "2026-09-17", "end": "2027-02-11"},
        {"start": "2027-02-13", "end": "2027-03-09"},
    ]


def test_defaults_to_real_today_when_not_given():
    # Just confirms it runs without error and anchors to something sane (today or later) -
    # doesn't assert an exact date since the test suite runs on different days.
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(["2099-01-01"])
    assert len(result) == 1
    assert result[0]["end"] == "2098-12-31"
    assert datetime.date.fromisoformat(result[0]["start"]) <= datetime.date.today()


# ---------------------------------------------------------------------------------------------
# _apply_fixed_departure_dates - wiring into the extracted data dict
# ---------------------------------------------------------------------------------------------

def test_apply_merges_computed_gaps_into_stop_sales():
    data = {"fixed_departure_dates": ["2027-02-12", "2027-03-10"], "stop_sales": [],
            "schedule_notes": ""}
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    assert {"start": "2026-09-17", "end": "2027-02-11"} in data["stop_sales"]
    assert {"start": "2027-02-13", "end": "2027-03-09"} in data["stop_sales"]


def test_apply_preserves_existing_ai_extracted_stop_sales_and_skips_overlap():
    data = {
        "fixed_departure_dates": ["2027-02-12", "2027-03-10"],
        "stop_sales": [{"start": "2027-01-01", "end": "2027-01-31"}],  # e.g. a dry-dock closure
        "schedule_notes": "",
    }
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    # the manually-extracted dry-dock closure survives untouched
    assert {"start": "2027-01-01", "end": "2027-01-31"} in data["stop_sales"]
    # the computed leading gap (2026-09-17 -> 2027-02-11) overlaps that dry-dock closure and must
    # be skipped entirely, not added a second, redundant/overlapping way
    assert {"start": "2026-09-17", "end": "2027-02-11"} not in data["stop_sales"]
    # the LATER gap, which genuinely doesn't overlap anything already extracted, must still land
    assert {"start": "2027-02-13", "end": "2027-03-09"} in data["stop_sales"]


def test_apply_appends_a_plain_english_note_naming_the_dates():
    data = {"fixed_departure_dates": ["2027-02-12", "2027-03-10"], "stop_sales": [],
            "schedule_notes": ""}
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    assert "2027-02-12" in data["schedule_notes"]
    assert "2027-03-10" in data["schedule_notes"]
    assert "Fixed departure dates" in data["schedule_notes"]


def test_apply_is_a_noop_when_no_fixed_departure_dates():
    data = {"fixed_departure_dates": None, "stop_sales": [], "schedule_notes": "existing note"}
    ai_extractor._apply_fixed_departure_dates(data)
    assert data["stop_sales"] == []
    assert data["schedule_notes"] == "existing note"

    data2 = {"stop_sales": [], "schedule_notes": ""}
    ai_extractor._apply_fixed_departure_dates(data2)
    assert data2["stop_sales"] == []


# ---------------------------------------------------------------------------------------------
# The two extraction paths that actually feed this - both mocked at _call_claude so no real API
# call happens, same established pattern as every other ai_extractor test in this suite.
# ---------------------------------------------------------------------------------------------

def test_extract_modality_data_wires_fixed_departure_dates(monkeypatch):
    fake_response = {
        "price_list": [], "pricing_notes": "", "schedule_notes": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "stop_sales": [], "guaranteed_departure_rule": None,
        "fixed_departure_dates": ["2027-02-12", "2027-03-10"],
    }
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_modality_data("irrelevant raw text")
    assert any(r["start"] and r["end"] for r in data["stop_sales"])
    assert "Fixed departure dates" in data["schedule_notes"]


def test_extract_option_only_data_wires_fixed_departure_dates(monkeypatch):
    fake_response = {
        "price_list": [], "pricing_notes": "", "schedule_notes": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "stop_sales": [], "guaranteed_departure_rule": None,
        "fixed_departure_dates": ["2027-02-12", "2027-03-10"],
    }
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_option_only_data("irrelevant raw text")
    assert any(r["start"] and r["end"] for r in data["stop_sales"])
    assert "Fixed departure dates" in data["schedule_notes"]


def test_prompts_document_the_new_field():
    assert "fixed_departure_dates" in ai_extractor.MODALITY_EXTRACTION_SYSTEM_PROMPT
    assert "fixed_departure_dates" in ai_extractor.OPTION_ONLY_SYSTEM_PROMPT
