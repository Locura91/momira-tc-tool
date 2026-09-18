"""Regression tests for a real product-owner request (2026-09-18, verbatim, with two screenshots
of a live Travel Compositor Modality "Prices" tab): "the modality reader for the correct dates is
not working : End dtae must be one day before next season start date. If are two modalities in the
same time, travel c gives an error, an extra modality has to be build. If questions, ask."

Two distinct problems, clarified via follow-up (AskUserQuestion, both confirmed):
  1. Touching boundaries (one season's endDate == the next season's startDate) - "Auto-fix
     silently on extract/build (Recommended)" -> builder.fix_touching_season_boundaries.
  2. Nested seasons (a shorter season entirely inside a longer one) - "Auto-split into a second
     Modality" (the MORE ambitious option, explicitly chosen over "flag it, human builds by
     hand") -> builder.split_nested_price_list_seasons, wired into flows/multi_tour.py to append
     a brand-new Modality dict.

Both are also re-applied as a belt-and-braces safety net inside builder.build_closed_tour_payloads
itself, since that function has other callers besides the flows/multi_tour.py extraction wiring.
"""
import datetime

import builder


def _row(start, end, name=None, single=100.0):
    row = {"startDate": start, "endDate": end, "price": {"singlePrice": {"amount": single, "currency": "EUR"}}}
    if name:
        row["name"] = name
    return row


# ---------------------------------------------------------------------------------------------
# fix_touching_season_boundaries
# ---------------------------------------------------------------------------------------------

def test_shifts_earlier_end_date_back_one_day_when_touching_the_next_start_date():
    pl = [
        _row("2026-11-30", "2026-12-21", "Low"),
        _row("2026-12-21", "2027-01-04", "High"),
        _row("2027-01-04", "2027-04-30", "Shoulder"),
    ]
    fixed = builder.fix_touching_season_boundaries(pl)
    by_name = {r["name"]: r for r in fixed}
    assert by_name["Low"]["endDate"] == "2026-12-20"
    assert by_name["High"]["endDate"] == "2027-01-03"
    assert by_name["Shoulder"]["endDate"] == "2027-04-30"  # last row, nothing after it - untouched


def test_leaves_non_touching_rows_alone():
    pl = [_row("2026-01-01", "2026-01-10", "A"), _row("2026-02-01", "2026-02-10", "B")]
    fixed = builder.fix_touching_season_boundaries(pl)
    by_name = {r["name"]: r for r in fixed}
    assert by_name["A"]["endDate"] == "2026-01-10"
    assert by_name["B"]["endDate"] == "2026-02-10"


def test_passes_through_rows_with_unparseable_dates_untouched():
    bad = {"startDate": "not-a-date", "endDate": "2026-01-10", "name": "Bad"}
    pl = [_row("2026-01-01", "2026-01-10", "A"), bad]
    fixed = builder.fix_touching_season_boundaries(pl)
    assert bad in fixed


def test_returns_empty_list_for_empty_input():
    assert builder.fix_touching_season_boundaries([]) == []
    assert builder.fix_touching_season_boundaries(None) == []


def test_does_not_mutate_the_original_rows():
    original = _row("2026-11-30", "2026-12-21", "Low")
    pl = [original, _row("2026-12-21", "2027-01-04", "High")]
    builder.fix_touching_season_boundaries(pl)
    assert original["endDate"] == "2026-12-21"  # unchanged - fix works on copies


# ---------------------------------------------------------------------------------------------
# split_nested_price_list_seasons
# ---------------------------------------------------------------------------------------------

def test_splits_a_single_nested_season_out_of_its_container():
    pl = [
        _row("2027-01-04", "2027-04-30", "High Season"),
        _row("2027-03-19", "2027-03-29", "Peak Season"),
    ]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert notes == []
    assert len(removed) == 1
    assert removed[0]["name"] == "Peak Season"
    assert removed[0]["startDate"] == "2027-03-19" and removed[0]["endDate"] == "2027-03-29"

    remaining_by_name = [r for r in remaining if r.get("name") == "High Season"]
    assert len(remaining_by_name) == 2
    ranges = sorted((r["startDate"], r["endDate"]) for r in remaining_by_name)
    assert ranges == [("2027-01-04", "2027-03-18"), ("2027-03-30", "2027-04-30")]


def test_nested_season_touching_one_edge_of_its_container_leaves_a_single_remaining_piece():
    pl = [
        _row("2027-01-04", "2027-04-30", "High Season"),
        _row("2027-01-04", "2027-01-20", "Early Peak"),
    ]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert notes == []
    assert [r["name"] for r in removed] == ["Early Peak"]
    remaining_by_name = [r for r in remaining if r.get("name") == "High Season"]
    assert len(remaining_by_name) == 1
    assert remaining_by_name[0]["startDate"] == "2027-01-21"
    assert remaining_by_name[0]["endDate"] == "2027-04-30"


def test_multiple_nested_seasons_in_one_container_all_get_carved_out():
    pl = [
        _row("2027-01-01", "2027-12-31", "Whole Year"),
        _row("2027-03-01", "2027-03-10", "Spring Peak"),
        _row("2027-07-01", "2027-07-10", "Summer Peak"),
    ]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert notes == []
    assert {r["name"] for r in removed} == {"Spring Peak", "Summer Peak"}
    remaining_by_name = [r for r in remaining if r.get("name") == "Whole Year"]
    assert len(remaining_by_name) == 3
    ranges = sorted((r["startDate"], r["endDate"]) for r in remaining_by_name)
    assert ranges == [
        ("2027-01-01", "2027-02-28"),
        ("2027-03-11", "2027-06-30"),
        ("2027-07-11", "2027-12-31"),
    ]


def test_multi_level_nesting_is_declined_and_reported_untouched():
    pl = [
        _row("2027-01-01", "2027-12-31", "Whole Year"),
        _row("2027-01-04", "2027-04-30", "High Season"),
        _row("2027-03-19", "2027-03-29", "Peak Season"),
    ]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert removed == []
    assert len(remaining) == 3
    assert {r["name"] for r in remaining} == {"Whole Year", "High Season", "Peak Season"}
    for r in pl:
        assert r in remaining
    assert len(notes) == 1
    assert "Whole Year" in notes[0] and "High Season" in notes[0] and "Peak Season" in notes[0]
    assert "nested more than one level deep" in notes[0]


def test_no_nesting_is_a_pure_no_op():
    pl = [_row("2026-01-01", "2026-06-30", "A"), _row("2026-07-01", "2026-12-31", "B")]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert removed == []
    assert notes == []
    assert sorted(r["name"] for r in remaining) == ["A", "B"]


def test_identical_date_ranges_are_not_treated_as_nested():
    pl = [_row("2026-01-01", "2026-06-30", "A"), _row("2026-01-01", "2026-06-30", "B")]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert removed == []
    assert notes == []
    assert len(remaining) == 2


def test_passes_through_unparseable_rows_untouched():
    bad = {"startDate": "bad", "endDate": "2026-01-10", "name": "Bad"}
    pl = [_row("2026-01-01", "2026-06-30", "A"), bad]
    remaining, removed, notes = builder.split_nested_price_list_seasons(pl)
    assert bad in remaining
    assert removed == []


def test_returns_empty_for_empty_input():
    remaining, removed, notes = builder.split_nested_price_list_seasons([])
    assert remaining == [] and removed == [] and notes == []
    remaining, removed, notes = builder.split_nested_price_list_seasons(None)
    assert remaining == [] and removed == [] and notes == []


# ---------------------------------------------------------------------------------------------
# build_closed_tour_payloads belt-and-braces safety net
# ---------------------------------------------------------------------------------------------

def test_build_closed_tour_payloads_return_dict_includes_price_list_overlap_notes_key():
    import inspect
    source = inspect.getsource(builder.build_closed_tour_payloads)
    assert '"price_list_overlap_notes": _price_list_overlap_notes' in source


def test_build_closed_tour_payloads_reapplies_touching_boundary_fix_before_sorting():
    import inspect
    source = inspect.getsource(builder.build_closed_tour_payloads)
    # The safety net re-applies fix_touching_season_boundaries right where the sorted price list
    # is built, not just relying on the caller (flows/multi_tour.py) to have already done it.
    idx = source.index("_tour_price_list_sorted = sorted(")
    snippet = source[idx:idx + 400]
    assert "fix_touching_season_boundaries(extracted_dmc_data.get(\"price_list\", []))" in snippet


# ---------------------------------------------------------------------------------------------
# flows/multi_tour.py wiring
# ---------------------------------------------------------------------------------------------

def _read_multi_tour_source():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flows", "multi_tour.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_multi_tour_imports_both_new_builder_functions():
    src = _read_multi_tour_source()
    assert "fix_touching_season_boundaries" in src
    assert "split_nested_price_list_seasons" in src


def test_extraction_block_applies_touching_boundary_fix_then_nested_split():
    src = _read_multi_tour_source()
    fix_idx = src.index('mod["data"]["price_list"] = fix_touching_season_boundaries(mod["data"].get("price_list"))')
    split_idx = src.index("split_nested_price_list_seasons(mod[\"data\"][\"price_list\"])")
    assert fix_idx < split_idx  # boundary fix must run before nesting detection


def test_extraction_block_appends_a_new_modality_for_each_nested_season():
    src = _read_multi_tour_source()
    idx = src.index("for _nested_row in _nested_seasons:")
    snippet = src[idx:idx + 900]
    assert "modalities.append(" in snippet
    assert '"confirmed": False' in snippet
    assert "_mct_generate_split_modality_code(" in snippet


def test_extraction_block_warns_about_unhandled_multi_level_nesting():
    src = _read_multi_tour_source()
    idx = src.index("for _nesting_note in _unhandled_nesting_notes:")
    snippet = src[idx:idx + 120]
    assert "st.warning(" in snippet


def test_save_mct_price_list_applies_touching_boundary_fix_but_not_auto_split():
    src = _read_multi_tour_source()
    save_idx = src.index("def _save_mct_price_list(")
    save_body = src[save_idx:save_idx + 1500]
    assert "fix_touching_season_boundaries(" in save_body
    assert "split_nested_price_list_seasons(" not in save_body


def test_review_screen_renders_price_list_overlap_notes():
    src = _read_multi_tour_source()
    assert 'render_supplement_zero_price_notes(preview_payloads, key="price_list_overlap_notes")' in src


# ---------------------------------------------------------------------------------------------
# _mct_generate_split_modality_code
# ---------------------------------------------------------------------------------------------

# flows/multi_tour.py can't be imported standalone in a test process - it's designed to be
# loaded only via app.py's own controlled circular-import sequence (see the module's own
# docstring on late-binding `from app import (...)`), same reason every other multi_tour test
# in this suite (e.g. test_2026_09_18_max_occupancy_extraction_hint.py) checks this module via
# its source text rather than importing it directly. _mct_generate_split_modality_code has no
# dependency on anything app.py-specific beyond _clean_modality_code (imported from app.py, but
# functionally identical to the standalone copy in app_helpers.py) - exec'ing just this one
# function's own source, with that same real implementation supplied, gives real behavioural
# coverage without needing the whole app.py import chain.
def _load_generate_split_modality_code():
    import re
    src = _read_multi_tour_source()
    start = src.index("def _mct_generate_split_modality_code(")
    end = src.index("\n\n\n", start)
    func_src = src[start:end]
    namespace = {"_clean_modality_code": lambda raw_code: "".join(c for c in (raw_code or "") if c not in "/\\+-.")}
    exec(func_src, namespace)
    return namespace["_mct_generate_split_modality_code"]


def test_generate_split_code_uses_the_season_name_when_present():
    fn = _load_generate_split_modality_code()
    nested = {"name": "Peak Season", "startDate": "2027-03-19", "endDate": "2027-03-29"}
    code = fn("CABIN", nested, existing_codes=["CABIN"])
    assert code == "CABINPeakSeason"


def test_generate_split_code_falls_back_to_dates_when_no_name():
    fn = _load_generate_split_modality_code()
    nested = {"startDate": "2027-03-19", "endDate": "2027-03-29"}
    code = fn("CABIN", nested, existing_codes=["CABIN"])
    assert code.startswith("CABIN2027")


def test_generate_split_code_appends_numeric_suffix_on_collision():
    fn = _load_generate_split_modality_code()
    nested = {"name": "Peak Season"}
    code = fn("CABIN", nested, existing_codes=["CABIN", "CABINPeakSeason"])
    assert code == "CABINPeakSeason2"


def test_generate_split_code_keeps_incrementing_past_multiple_collisions():
    fn = _load_generate_split_modality_code()
    nested = {"name": "Peak Season"}
    code = fn("CABIN", nested,
               existing_codes=["CABIN", "CABINPeakSeason", "CABINPeakSeason2", "CABINPeakSeason3"])
    assert code == "CABINPeakSeason4"
