"""Tests for the stoppable combination-search queue in outreach_tool.py.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): "when Find and Contact Supplier and the search
is being conducted, please give the human one button that says 'Stop the search' and give the
human all results found until then."

A country-scope run can queue many place/theme combinations, each its own web search - a plain
Python for-loop over all of them blocks the whole Streamlit script, and Streamlit cannot service
a button click until a script run returns, so a for-loop can never be interrupted once started.
The fix processes exactly ONE combination per Streamlit rerun (_process_one_queued_job, driven by
_init_queue_run's session-state machine in _render_search), which gives the "Stop the search"
button an actual gap to be clicked in between combinations.

These tests cover the two pure pieces that don't need a running Streamlit script:
_merge_one_job_result (folding one combination's result into the running list) and
_finalize_queue_result (the shared finishing steps for both a completed and a stopped-early run).
The session-state-driven scheduling itself (_init_queue_run/_process_one_queued_job) is exercised
indirectly through _merge_one_job_result, which is the part of that pair with real logic in it.
"""
import outreach_tool as ot


def _fresh_stats():
    return {"raw": 0, "after_prefilter": 0, "final": 0, "used_mock_provider": False}


def _job_result(suppliers, raw=5, after_prefilter=3, final=None, used_mock=False):
    return {
        "suppliers": suppliers,
        "stats": {"raw": raw, "after_prefilter": after_prefilter,
                  "final": final if final is not None else len(suppliers),
                  "used_mock_provider": used_mock},
    }


def _supplier(name, email=None, website=None):
    return {"name": name, "email": email, "website": website, "social": None,
            "listingUrl": None, "rating": None, "selected": bool(email)}


# ======================================================================
# _merge_one_job_result
# ======================================================================
def test_merge_one_job_result_accumulates_stats_across_calls():
    merged, seen, stats = [], set(), _fresh_stats()
    ot._merge_one_job_result(merged, seen, stats, "Luxor · Nile Cruise",
                             _job_result([_supplier("A", email="a@x.com")], raw=5, after_prefilter=3))
    ot._merge_one_job_result(merged, seen, stats, "Aswan · Nile Cruise",
                             _job_result([_supplier("B", email="b@x.com")], raw=4, after_prefilter=2))
    assert stats["raw"] == 9
    assert stats["after_prefilter"] == 5
    assert len(merged) == 2


def test_merge_one_job_result_dedupes_by_domain_across_jobs():
    merged, seen, stats = [], set(), _fresh_stats()
    same = _supplier("Nile Adventures", website="https://nile-adventures.com")
    ot._merge_one_job_result(merged, seen, stats, "Luxor · Nile Cruise", _job_result([same]))
    ot._merge_one_job_result(merged, seen, stats, "Aswan · Nile Cruise", _job_result([dict(same)]))
    assert len(merged) == 1


def test_merge_one_job_result_tags_each_supplier_with_its_own_combination_label():
    merged, seen, stats = [], set(), _fresh_stats()
    ot._merge_one_job_result(merged, seen, stats, "Luxor · Nile Cruise",
                             _job_result([_supplier("A", email="a@x.com")]))
    assert merged[0]["foundVia"] == "Luxor · Nile Cruise"


def test_merge_one_job_result_used_mock_provider_sticks_once_true():
    merged, seen, stats = [], set(), _fresh_stats()
    ot._merge_one_job_result(merged, seen, stats, "Luxor", _job_result([], used_mock=True))
    ot._merge_one_job_result(merged, seen, stats, "Aswan", _job_result([], used_mock=False))
    assert stats["used_mock_provider"] is True


# ======================================================================
# _finalize_queue_result
# ======================================================================
def test_finalize_queue_result_reports_a_stopped_early_run():
    merged = [_supplier("A", email="a@x.com")]
    result = ot._finalize_queue_result(merged, _fresh_stats(), [],
                                       stopped_early=True, searched=7, total=40)
    assert result["stats"]["stopped_early"] is True
    assert result["stats"]["searched"] == 7
    assert result["stats"]["total_planned"] == 40
    assert len(result["suppliers"]) == 1


def test_finalize_queue_result_does_not_report_stopped_early_when_the_queue_finished():
    merged = [_supplier("A", email="a@x.com")]
    result = ot._finalize_queue_result(merged, _fresh_stats(), [],
                                       stopped_early=False, searched=40, total=40)
    assert "stopped_early" not in result["stats"]


def test_finalize_queue_result_still_dedupes_and_caps_a_stopped_early_run():
    # A partial run should go through the exact same finishing steps a completed one does -
    # dedupe by email/social, then the usual _MAX_MERGED_RESULTS cap.
    merged = [_supplier(f"Supplier {i}", email=f"s{i}@x.com") for i in range(ot._MAX_MERGED_RESULTS + 5)]
    result = ot._finalize_queue_result(merged, _fresh_stats(), [],
                                       stopped_early=True, searched=10, total=40)
    assert len(result["suppliers"]) == ot._MAX_MERGED_RESULTS
    assert result["stats"]["dropped_over_cap"] == 5


def test_finalize_queue_result_with_nothing_found_returns_an_empty_list_not_an_error():
    result = ot._finalize_queue_result([], _fresh_stats(), [], stopped_early=True, searched=1, total=40)
    assert result["suppliers"] == []
    assert result["stats"]["stopped_early"] is True
