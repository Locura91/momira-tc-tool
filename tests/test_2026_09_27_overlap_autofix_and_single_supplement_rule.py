"""Regression tests for two real product-owner reports (2026-09-27).

1) Overlapping price_list rows silently add up (verbatim): "in this price list we have a problem
   very big: if multiple modalities are within the same dates the modalities are added up, which
   in this example makes the closedtour way too expensive." The screenshot showed a single
   Modality's OWN price table (app.py's single-Modality wizard, not flows/multi_tour.py) with a
   long base-season row (e.g. 01/11/2026-30/04/2027) fully containing a shorter, higher
   peak-season row (e.g. 20/12/2026-10/01/2027) - genuine nested/contained overlap, not just a
   touching boundary. flows/multi_tour.py already had auto-fixes for this shape since 2026-09-18
   (fix_touching_season_boundaries / split_nested_price_list_seasons), but app.py's single-
   Modality price table never applied either one before this fix - it only flagged a plain
   overlap with a hard error, requiring the human to manually restructure it.

   Fix: apply both builder functions to the price table's data right where it's loaded, keeping
   both carved container row(s) AND the nested row in the SAME price_list (not spun off into a
   new Modality, unlike flows/multi_tour.py - this screen edits one Modality directly).

2) Single supplement default (verbatim): "if an contract says for specific time period no single
   supplement, the price for single is the same as double. but if the contract does not say
   otherwise, we have to add single supplement to the single price list." A prompt-level house
   rule added to every price_list extraction prompt in ai_extractor.py - cannot be verified with
   a live AI call in this sandbox, so this just locks in that the rule text exists everywhere
   singlePrice/doublePrice extraction is described.

app.py can't be safely imported in a test process (top-level Streamlit calls run on import) - read
its source text directly, same as every other app.py-side regression test in this suite.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(_REPO_ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


# --- Part 1: overlap auto-fix wired into app.py's single-Modality price table ---

def test_app_py_imports_both_season_fix_helpers():
    src = _read("app.py")
    assert "from builder import fix_touching_season_boundaries, split_nested_price_list_seasons" in src


def test_price_table_block_applies_touching_boundary_fix():
    src = _read("app.py")
    idx = src.index('st.subheader("Pricing (required by Travel Compositor to publish)")')
    window = src[idx:idx + 3000]
    assert "default_price_list = fix_touching_season_boundaries(default_price_list)" in window


def test_price_table_block_carves_out_nested_seasons_into_the_same_price_list():
    src = _read("app.py")
    idx = src.index('st.subheader("Pricing (required by Travel Compositor to publish)")')
    window = src[idx:idx + 3500]
    assert "split_nested_price_list_seasons(default_price_list)" in window
    # The carved container rows AND the nested row must both stay in THIS SAME price_list -
    # never routed to a new Modality (this screen only edits one Modality at a time).
    assert "default_price_list = sorted(_ct_carved + _ct_nested_rows" in window


def test_price_table_block_still_warns_on_multi_level_nesting_it_cannot_auto_split():
    src = _read("app.py")
    idx = src.index('st.subheader("Pricing (required by Travel Compositor to publish)")')
    window = src[idx:idx + 4000]
    assert "_ct_nest_unhandled" in window
    assert "wasn't " in window or "auto-split" in window


def test_overlap_error_check_still_present_for_genuine_non_nested_overlaps():
    # A partial (non-containment) overlap still can't be auto-fixed - the hard error that was
    # already there for that case must remain.
    src = _read("app.py")
    assert "Overlapping date ranges detected" in src
    assert "def _dates_overlap(a_start, a_end, b_start, b_end):" in src


def test_builder_functions_still_behave_as_documented_real_worked_example():
    # Sanity-check the exact screenshot numbers: a base season fully containing two peak-season
    # windows must carve cleanly into non-overlapping rows covering the same total date range.
    from builder import fix_touching_season_boundaries, split_nested_price_list_seasons

    price_list = [
        {"name": "Base", "startDate": "2026-11-01", "endDate": "2027-04-30",
         "price": {"singlePrice": {"amount": 19131, "currency": "THB"}}},
        {"name": "Peak", "startDate": "2026-12-20", "endDate": "2027-01-10",
         "price": {"singlePrice": {"amount": 20088, "currency": "THB"}}},
        {"name": "Peak", "startDate": "2027-03-25", "endDate": "2027-04-12",
         "price": {"singlePrice": {"amount": 20088, "currency": "THB"}}},
    ]
    fixed = fix_touching_season_boundaries(price_list)
    carved, nested, unhandled = split_nested_price_list_seasons(fixed)
    assert unhandled == []
    assert len(nested) == 2
    combined = sorted(carved + nested, key=lambda e: e["startDate"])

    def _overlaps(a, b):
        return a["startDate"] <= b["endDate"] and b["startDate"] <= a["endDate"]

    for i in range(len(combined)):
        for j in range(i + 1, len(combined)):
            assert not _overlaps(combined[i], combined[j]), (combined[i], combined[j])


# --- Part 2: single supplement house rule present in every price_list extraction prompt ---

def test_single_supplement_house_rule_appears_at_least_three_times():
    src = _read("ai_extractor.py")
    marker = "SINGLE SUPPLEMENT - CONFIRMED HOUSE RULE (product owner, 2026-09-27"
    assert src.count(marker) >= 3


def test_single_supplement_rule_states_the_waiver_exception():
    src = _read("ai_extractor.py")
    assert 'singlePrice for that row = doublePrice, unchanged' in src


def test_single_supplement_rule_states_the_add_supplement_default():
    src = _read("ai_extractor.py")
    assert "doublePrice + that supplement" in src


def test_single_supplement_rule_does_not_silently_default_when_document_is_silent():
    src = _read("ai_extractor.py")
    assert "do NOT default to equal - say so in pricing_notes" in src
