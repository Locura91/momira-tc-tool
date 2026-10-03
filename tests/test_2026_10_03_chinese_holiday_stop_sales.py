"""
Tests for Chinese holiday stop-sale injection (2026-10-03).

Confirms:
- Golden Week (Oct 1-7) and Spring Festival dates are present in chinese_holiday_stop_sales()
- CHINA_ALWAYS_BLOCKED_HOLIDAYS covers 2025-2040 for both holidays
- _is_china_country_value / _is_china_place_name accept the correct values
- _detect_china_tour correctly identifies China destinations
- Stop sales are merged (not duplicated) when already present in extracted data
- Non-China tours/tickets are unaffected
"""

import pytest
from builder import (
    CHINA_ALWAYS_BLOCKED_HOLIDAYS,
    CHINESE_GOLDEN_WEEK_DATES,
    CHINESE_SPRING_FESTIVAL_DATES,
    chinese_holiday_stop_sales,
    chinese_holiday_coverage_note,
    _is_china_country_value,
    _is_china_place_name,
    _detect_china_tour,
    _merge_stop_sales,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestChineseGoldenWeek:
    def test_covers_2025_to_2040(self):
        assert set(CHINESE_GOLDEN_WEEK_DATES.keys()) == set(range(2025, 2041))

    def test_oct_1_to_7_every_year(self):
        for year, dates in CHINESE_GOLDEN_WEEK_DATES.items():
            assert dates["start"] == f"{year}-10-01"
            assert dates["end"] == f"{year}-10-07"


class TestChineseSpringFestival:
    def test_covers_2025_to_2040(self):
        assert set(CHINESE_SPRING_FESTIVAL_DATES.keys()) == set(range(2025, 2041))

    def test_2026_dates(self):
        """2026: CNY falls on Feb 17."""
        assert CHINESE_SPRING_FESTIVAL_DATES[2026] == {"start": "2026-02-17", "end": "2026-02-23"}

    def test_2027_dates(self):
        assert CHINESE_SPRING_FESTIVAL_DATES[2027] == {"start": "2027-02-06", "end": "2027-02-12"}

    def test_2031_dates(self):
        """2031 is the one year the festival falls in late January."""
        assert CHINESE_SPRING_FESTIVAL_DATES[2031] == {"start": "2031-01-23", "end": "2031-01-29"}

    def test_end_is_always_6_days_after_start(self):
        """All windows must be exactly 6 days wide (7 days inclusive)."""
        from datetime import date
        for year, d in CHINESE_SPRING_FESTIVAL_DATES.items():
            start = date.fromisoformat(d["start"])
            end = date.fromisoformat(d["end"])
            assert (end - start).days == 6, f"{year}: expected 6-day window, got {(end-start).days}"


class TestChinaAlwaysBlockedHolidays:
    def test_has_golden_week_and_spring_festival(self):
        names = list(CHINA_ALWAYS_BLOCKED_HOLIDAYS.keys())
        assert any("Golden Week" in n for n in names)
        assert any("Spring Festival" in n for n in names)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

class TestIsChinaCountryValue:
    def test_iso_code_cn(self):
        assert _is_china_country_value("CN") is True

    def test_iso_code_lowercase(self):
        assert _is_china_country_value("cn") is True

    def test_full_name(self):
        assert _is_china_country_value("China") is True

    def test_full_name_lowercase(self):
        assert _is_china_country_value("china") is True

    def test_indonesia_not_china(self):
        assert _is_china_country_value("ID") is False
        assert _is_china_country_value("Indonesia") is False

    def test_none(self):
        assert _is_china_country_value(None) is False

    def test_empty_string(self):
        assert _is_china_country_value("") is False


class TestIsChinaPlaceName:
    def test_nominatim_china_string(self):
        assert _is_china_place_name("Shanghai, China") is True

    def test_china_in_middle(self):
        assert _is_china_place_name("Beijing, People's Republic of China") is True

    def test_not_china(self):
        assert _is_china_place_name("Bangkok, Thailand") is False

    def test_none(self):
        assert _is_china_place_name(None) is False


# ---------------------------------------------------------------------------
# chinese_holiday_stop_sales()
# ---------------------------------------------------------------------------

class TestChineseHolidayStopSales:
    def test_returns_list_of_dicts(self):
        result = chinese_holiday_stop_sales()
        assert isinstance(result, list)
        assert all(isinstance(x, dict) for x in result)

    def test_all_have_start_and_end(self):
        for entry in chinese_holiday_stop_sales():
            assert "start" in entry
            assert "end" in entry

    def test_golden_week_2026_present(self):
        entries = chinese_holiday_stop_sales()
        assert {"start": "2026-10-01", "end": "2026-10-07"} in entries

    def test_spring_festival_2026_present(self):
        entries = chinese_holiday_stop_sales()
        assert {"start": "2026-02-17", "end": "2026-02-23"} in entries

    def test_total_count_is_32(self):
        """16 years × 2 holidays = 32 entries."""
        assert len(chinese_holiday_stop_sales()) == 32

    def test_no_duplicates(self):
        entries = chinese_holiday_stop_sales()
        keys = [(e["start"], e["end"]) for e in entries]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# chinese_holiday_coverage_note()
# ---------------------------------------------------------------------------

class TestChineseHolidayCoverageNote:
    def test_mentions_golden_week(self):
        assert "Golden Week" in chinese_holiday_coverage_note()

    def test_mentions_spring_festival(self):
        assert "Spring Festival" in chinese_holiday_coverage_note()

    def test_mentions_year_range(self):
        note = chinese_holiday_coverage_note()
        assert "2025" in note
        assert "2040" in note


# ---------------------------------------------------------------------------
# _detect_china_tour (no API client - pure geocoder path not tested here,
# just confirms empty/None locations don't crash and return False)
# ---------------------------------------------------------------------------

class TestDetectChinaTour:
    def test_empty_list(self):
        assert _detect_china_tour([]) is False

    def test_none_entries(self):
        assert _detect_china_tour([None, "", None]) is False


# ---------------------------------------------------------------------------
# Merge deduplication (existing stop sale not doubled when already present)
# ---------------------------------------------------------------------------

class TestMergeWithChineseHolidays:
    def test_existing_golden_week_not_doubled(self):
        existing = [{"start": "2026-10-01", "end": "2026-10-07"}]
        merged = _merge_stop_sales(existing, chinese_holiday_stop_sales())
        golden_week_2026 = [e for e in merged if e["start"] == "2026-10-01" and e["end"] == "2026-10-07"]
        assert len(golden_week_2026) == 1

    def test_non_china_stop_sale_kept(self):
        existing = [{"start": "2026-12-24", "end": "2026-12-26"}]
        merged = _merge_stop_sales(existing, chinese_holiday_stop_sales())
        assert {"start": "2026-12-24", "end": "2026-12-26"} in merged
