"""Regression tests for a real diagnostic gap (product-owner report, 2026-08-25): "the results
for the outreach supplier mail, when i searched for South Korea, was actually very bad. No local
DMC and no local Tour Guide at all."

Before this fix, there was no way to tell WHY - a Country Scope run (many place/theme
combinations, the flow's own recommended entry point per the 2026-08-16 request) only merged
`raw`/`after_prefilter`/`final` from each combination's search, and dropped every combination's
own `drop_log` on the floor. The "How N raw results became M" breakdown in
_render_review_and_send() - which exists PRECISELY to answer "did the search find nothing, or
find things and filter them all out?" (see that expander's own caption) - only ever lit up for a
single Country/City/Keyword search, because it gates on `"after_vetting" in stats`, a key a
combination run never populated. A bad-results report from the normal, recommended flow was
undiagnosable without re-running as a single search instead.

_merge_one_job_result/_finalize_queue_result now merge the full stats dict (after_vetting,
after_dedupe, ai_dropped, no_contact_dropped) and every combination's drop_log (tagged with which
combination it came from), so the same breakdown works for a Country Scope run too.
"""
import outreach_tool as ot


def _fresh_stats():
    return {
        "raw": 0, "after_prefilter": 0, "after_vetting": 0, "after_dedupe": 0,
        "ai_dropped": 0, "no_contact_dropped": 0, "final": 0, "used_mock_provider": False,
    }


def _job_result(suppliers, drop_log=None, **stat_overrides):
    stats = {"raw": 5, "after_prefilter": 3, "after_vetting": 2, "after_dedupe": 2,
             "ai_dropped": 0, "no_contact_dropped": 1, "final": len(suppliers),
             "used_mock_provider": False}
    stats.update(stat_overrides)
    return {"suppliers": suppliers, "stats": stats, "drop_log": drop_log or []}


def _supplier(name, email=None):
    return {"name": name, "email": email, "website": None, "social": None,
            "listingUrl": None, "rating": None, "selected": bool(email)}


# ---------------------------------------------------------------------------
# Full stats now accumulate, not just raw/after_prefilter/final
# ---------------------------------------------------------------------------

def test_merge_accumulates_after_vetting_after_dedupe_ai_dropped_no_contact_dropped():
    stats = _fresh_stats()
    ot._merge_one_job_result([], set(), stats, "Seoul · Local DMC",
                             _job_result([], after_vetting=2, after_dedupe=1, ai_dropped=1,
                                         no_contact_dropped=1))
    ot._merge_one_job_result([], set(), stats, "Busan · Local DMC",
                             _job_result([], after_vetting=3, after_dedupe=3, ai_dropped=0,
                                         no_contact_dropped=2))
    assert stats["after_vetting"] == 5
    assert stats["after_dedupe"] == 4
    assert stats["ai_dropped"] == 1
    assert stats["no_contact_dropped"] == 3


def test_merge_tolerates_a_result_missing_the_newer_stat_keys():
    """Defensive: a result dict from an older code path (or a test double) without
    after_vetting/etc. must not crash the merge - it should just contribute 0."""
    stats = _fresh_stats()
    bare_result = {"suppliers": [], "stats": {"raw": 5, "after_prefilter": 3, "final": 0}}
    ot._merge_one_job_result([], set(), stats, "Seoul", bare_result)
    assert stats["after_vetting"] == 0
    assert stats["raw"] == 5


# ---------------------------------------------------------------------------
# drop_log merges across combinations, tagged with which one it came from
# ---------------------------------------------------------------------------

def test_merge_collects_drop_log_entries_tagged_with_their_combination():
    drop_log = []
    stats = _fresh_stats()
    ot._merge_one_job_result([], set(), stats, "Seoul · Local DMC", _job_result(
        [], drop_log=[{"name": "Some Agency", "url": "https://x.kr", "reason": "rating below bar", "stage": "vetting"}]
    ), drop_log=drop_log)
    ot._merge_one_job_result([], set(), stats, "Busan · Tour Guide", _job_result(
        [], drop_log=[{"name": "Another Guide", "url": "https://y.kr", "reason": "generic name", "stage": "pre-filter"}]
    ), drop_log=drop_log)
    assert len(drop_log) == 2
    assert drop_log[0]["combination"] == "Seoul · Local DMC"
    assert drop_log[0]["name"] == "Some Agency"
    assert drop_log[1]["combination"] == "Busan · Tour Guide"


def test_merge_without_a_drop_log_list_does_not_crash_and_does_not_collect_anything():
    """drop_log is optional - existing/older call sites that don't pass it must keep working."""
    stats = _fresh_stats()
    ot._merge_one_job_result([], set(), stats, "Seoul", _job_result(
        [], drop_log=[{"name": "X", "reason": "y", "stage": "vetting"}]
    ))  # no drop_log= kwarg at all


def test_merge_handles_a_result_with_no_drop_log_key_at_all():
    drop_log = []
    stats = _fresh_stats()
    result = {"suppliers": [], "stats": _fresh_stats()}  # no "drop_log" key
    ot._merge_one_job_result([], set(), stats, "Seoul", result, drop_log=drop_log)
    assert drop_log == []


# ---------------------------------------------------------------------------
# _finalize_queue_result carries the merged drop_log through to its own result dict, and
# defaults sensibly when none is passed
# ---------------------------------------------------------------------------

def test_finalize_queue_result_returns_the_merged_drop_log():
    drop_log = [{"name": "Some Agency", "reason": "rating below bar", "stage": "vetting",
                "combination": "Seoul · Local DMC"}]
    result = ot._finalize_queue_result([], _fresh_stats(), [], drop_log=drop_log)
    assert result["drop_log"] == drop_log


def test_finalize_queue_result_defaults_drop_log_to_an_empty_list():
    result = ot._finalize_queue_result([], _fresh_stats(), [])
    assert result["drop_log"] == []


# ---------------------------------------------------------------------------
# End-to-end: the exact scenario reported - two "local DMC"/"local guide" combinations for
# South Korea that found real candidates but filtered every one of them out - is now fully
# diagnosable from the merged result, not silently indistinguishable from "found nothing".
# ---------------------------------------------------------------------------

def test_a_queue_run_that_filters_out_every_candidate_is_diagnosable_not_silent():
    merged, seen, stats, drop_log = [], set(), _fresh_stats(), []
    ot._merge_one_job_result(merged, seen, stats, "South Korea · local DMC",
                             _job_result([], raw=6, after_prefilter=4, after_vetting=0,
                                         after_dedupe=0, no_contact_dropped=0,
                                         drop_log=[{"name": "Seoul City Tours", "reason": "rating below MIN_SUPPLIER_RATING=4.0 with no positive signal", "stage": "vetting"},
                                                   {"name": "Busan Local Trips", "reason": "rating below MIN_SUPPLIER_RATING=4.0 with no positive signal", "stage": "vetting"}]),
                             drop_log=drop_log)
    ot._merge_one_job_result(merged, seen, stats, "South Korea · private tour guide",
                             _job_result([], raw=5, after_prefilter=3, after_vetting=0,
                                         after_dedupe=0, no_contact_dropped=0,
                                         drop_log=[{"name": "Jeju Walking Tours", "reason": "OTA/marketplace platform, not a direct supplier", "stage": "pre-filter"}]),
                             drop_log=drop_log)
    result = ot._finalize_queue_result(merged, stats, [], drop_log=drop_log)

    # Real candidates DID come back from the search (raw > 0) - this was never a "the provider
    # found nothing" case - but every one of them was filtered out downstream (final == 0).
    # Before this fix, both looked identical: zero suppliers, no way to tell which.
    assert result["stats"]["raw"] == 11
    assert result["stats"]["after_prefilter"] == 7
    assert result["stats"]["final"] == 0
    assert result["suppliers"] == []
    assert len(result["drop_log"]) == 3
    assert {"Seoul City Tours", "Busan Local Trips", "Jeju Walking Tours"} == {
        d["name"] for d in result["drop_log"]
    }
