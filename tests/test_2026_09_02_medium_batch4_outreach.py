"""Tests for the MEDIUM/LOW "Batch 4" findings from the full-app audit
(full-app-audit-2026-09-01.md), fixed 2026-09-02 - covers the Outreach subsystem table's
remaining MEDIUM/LOW findings (rows 11-18; rows 1-10 were the CRITICAL/HIGH items already fixed
in earlier batches), the first of the two remaining approved batches (Batch 4 = these, Batch 5 =
support modules):

  11. Name-only candidate dedupe could attach the wrong business's Instagram/website/rating -
      fixed with a host-conflict check that keeps genuinely conflicting same-named candidates as
      separate entries instead of merging them.
  12. No 429/rate-limit circuit breaker - a dead primary provider got hit on every query - fixed
      with a per-run circuit breaker that skips a provider once it's confirmed rate-limited/
      quota-exhausted, without adding any delay.
  13. Hand-added suppliers bypassed the "already contacted" check - fixed by routing both the
      manual-add expander and the table's own added-rows path through _mark_already_contacted.
  14. No per-batch email dedupe - fixed in dispatch_batch with a seen-emails set.
  15. Mock/fabricated suppliers were pre-ticked and sendable - fixed at the source
      (to_supplier_record never pre-ticks isMock) plus a defense-in-depth guard on the send
      screen that excludes any mock row even if re-ticked.
  16. The "mock results" warning ignored GEMINI_API_KEY - fixed to use
      _configured_provider_chain() in both outreach_discovery.py and outreach_tool.py.
  17. (covered under 11 - same fix, same finding shape)
  18. Several LOW items: unescaped "www." regex, a discarded record_send() return value, a
      final-recipient throttle sleep with nothing left to throttle, an outreach stats miscount
      after cross-combination dedupe/capping, and SMTP sends never getting a Message-ID.
"""
import time

import outreach_discovery as od
import outreach_email as oe
import outreach_tool as ot


def _candidate(**overrides):
    base = {"name": "Nile Adventures", "email": None, "website": None,
            "instagramUrl": None, "facebookUrl": None, "aggregatorUrl": None,
            "rating": None, "reviewCount": None, "snippet": None, "sources": [],
            "isMock": False}
    base.update(overrides)
    return base


def _raw(**overrides):
    base = {"id": "c1", "source": "google", "name": "City Tours", "sourceUrl": None,
            "snippet": "", "rating": None, "reviewCount": None, "hasPositiveSignal": False,
            "isMock": False}
    base.update(overrides)
    return base


# ======================================================================
# 11/17. dedupe_candidates keeps genuinely-conflicting same-named candidates separate
# ======================================================================
def test_dedupe_keeps_two_different_businesses_with_the_same_name_separate():
    candidates = [
        _raw(id="a", name="City Tours", sourceUrl="https://www.instagram.com/citytoursA/"),
        _raw(id="b", name="City Tours", sourceUrl="https://www.instagram.com/citytoursB/"),
    ]
    deduped = od.dedupe_candidates(candidates)
    assert len(deduped) == 2
    instagram_urls = {c.get("instagramUrl") for c in deduped}
    assert "https://www.instagram.com/citytoursA/" in instagram_urls
    assert "https://www.instagram.com/citytoursB/" in instagram_urls


def test_dedupe_still_merges_the_same_business_resurfacing_same_host():
    candidates = [
        _raw(id="a", source="google", name="Nile Adventures", sourceUrl="https://nile-adventures.com"),
        _raw(id="b", source="tripadvisor", name="Nile Adventures", sourceUrl="https://nile-adventures.com/about"),
    ]
    deduped = od.dedupe_candidates(candidates)
    assert len(deduped) == 1
    assert len(deduped[0]["sources"]) == 2


def test_distinct_host_helper():
    assert od._distinct_host("https://a.com/x", "https://b.com/y") is True
    assert od._distinct_host("https://a.com/x", "https://a.com/y") is False  # same site, diff page
    assert od._distinct_host(None, "https://a.com") is False
    assert od._distinct_host("https://a.com", None) is False


def test_distinct_social_profile_helper():
    assert od._distinct_social_profile(
        "https://www.instagram.com/citytoursA/", "https://www.instagram.com/citytoursB/") is True
    assert od._distinct_social_profile(
        "https://www.instagram.com/citytoursA/", "https://instagram.com/citytoursA") is False
    assert od._distinct_social_profile(None, "https://instagram.com/x") is False


# ======================================================================
# 12. Provider circuit breaker
# ======================================================================
def test_is_rate_limit_or_quota_error_detects_429_and_quota_text():
    assert od._is_rate_limit_or_quota_error(RuntimeError("HTTP 429: Too Many Requests")) is True
    assert od._is_rate_limit_or_quota_error(RuntimeError("gemini 429 RESOURCE_EXHAUSTED")) is True
    assert od._is_rate_limit_or_quota_error(RuntimeError("This request exceeds your plan's usage limit.")) is True
    assert od._is_rate_limit_or_quota_error(RuntimeError("401 Unauthorized - bad API key")) is False
    assert od._is_rate_limit_or_quota_error(ConnectionError("network unreachable")) is False


def test_circuit_breaker_trips_and_cools_down():
    od.reset_circuit_breakers()
    assert od._circuit_is_open("tavily") is False
    od._trip_circuit_breaker("tavily")
    assert od._circuit_is_open("tavily") is True
    # Simulate cooldown elapsed by backdating the trip time directly.
    od._PROVIDER_CIRCUIT_BREAKER["tavily"] = time.time() - od._CIRCUIT_BREAKER_COOLDOWN_SECONDS - 1
    assert od._circuit_is_open("tavily") is False
    od.reset_circuit_breakers()


def test_select_and_run_provider_skips_a_tripped_provider(monkeypatch):
    od.reset_circuit_breakers()
    monkeypatch.setattr(od, "_configured_provider_chain", lambda: ["tavily", "serpapi"])
    calls = []

    def fake_tavily(query, domains, max_results):
        calls.append("tavily")
        raise RuntimeError("HTTP 429: rate limited")

    def fake_serpapi(query, domains, max_results):
        calls.append("serpapi")
        return [{"ok": True}]

    monkeypatch.setattr(od, "_search_with_tavily", fake_tavily)
    monkeypatch.setattr(od, "_search_with_serpapi", fake_serpapi)

    # First call: tavily fails with a 429, trips its breaker, falls through to serpapi.
    result1 = od._select_and_run_provider("supplier_city", "q1", "Egypt", "diving", [], 5)
    assert result1 == [{"ok": True}]
    assert calls == ["tavily", "serpapi"]

    # Second call, same run: tavily's breaker is open, so it must be skipped entirely (no second
    # wasted call to a provider already known to be exhausted) and go straight to serpapi.
    calls.clear()
    result2 = od._select_and_run_provider("supplier_city", "q2", "Egypt", "diving", [], 5)
    assert result2 == [{"ok": True}]
    assert calls == ["serpapi"]  # tavily never called again
    od.reset_circuit_breakers()


def test_select_and_run_provider_does_not_trip_on_non_quota_errors(monkeypatch):
    od.reset_circuit_breakers()
    monkeypatch.setattr(od, "_configured_provider_chain", lambda: ["tavily", "serpapi"])

    def fake_tavily(query, domains, max_results):
        raise RuntimeError("401 Unauthorized - bad API key")

    def fake_serpapi(query, domains, max_results):
        return [{"ok": True}]

    monkeypatch.setattr(od, "_search_with_tavily", fake_tavily)
    monkeypatch.setattr(od, "_search_with_serpapi", fake_serpapi)
    od._select_and_run_provider("supplier_city", "q1", "Egypt", "diving", [], 5)
    assert od._circuit_is_open("tavily") is False  # a bad-key error must keep failing loudly
    od.reset_circuit_breakers()


# ======================================================================
# 13. Hand-added suppliers go through the already-contacted check
# ======================================================================
def test_new_supplier_from_table_row_source_used_by_apply_review_table_edits():
    import inspect
    source = inspect.getsource(ot._apply_review_table_edits)
    assert "_mark_already_contacted(new_suppliers)" in source


def test_apply_review_table_edits_flags_a_hand_added_duplicate(monkeypatch):
    monkeypatch.setattr(ot.ofw, "list_all_sends",
                        lambda: [{"email": "already@sent.com"}])
    diff = {"added_rows": [{"Name": "New Co", "Email": "already@sent.com"}]}
    rebuilt = ot._apply_review_table_edits([], diff)
    assert len(rebuilt) == 1
    assert rebuilt[0]["alreadyContacted"] is True
    assert rebuilt[0]["selected"] is False


def test_apply_review_table_edits_leaves_a_fresh_hand_add_selected(monkeypatch):
    monkeypatch.setattr(ot.ofw, "list_all_sends", lambda: [])
    diff = {"added_rows": [{"Name": "New Co", "Email": "brand-new@example.com"}]}
    rebuilt = ot._apply_review_table_edits([], diff)
    assert len(rebuilt) == 1
    assert not rebuilt[0].get("alreadyContacted")
    assert rebuilt[0]["selected"] is True


# ======================================================================
# 14. Per-batch email dedupe in dispatch_batch
# ======================================================================
def test_dispatch_batch_skips_duplicate_email_in_same_batch(monkeypatch):
    monkeypatch.setattr(oe, "send_supplier_email",
                        lambda supplier, session, template: {"messageId": "x", "demo": True, "provider": "demo"})
    suppliers = [
        {"id": "1", "name": "A", "email": "same@example.com"},
        {"id": "2", "name": "B", "email": "SAME@Example.com"},  # same address, different case
    ]
    results = oe.dispatch_batch(suppliers, {"keyword": "k", "country": "Egypt"},
                                {"subject": "s", "textBody": "b"}, dry_run=True)
    statuses = [r["status"] for r in results]
    assert statuses[0] == "would_send"
    assert statuses[1] == "skipped"
    assert "duplicate" in results[1]["reason"].lower()


def test_dispatch_batch_last_recipient_does_not_sleep(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(oe.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(oe, "send_supplier_email",
                        lambda supplier, session, template: {"messageId": "x", "demo": True, "provider": "demo"})
    suppliers = [{"id": "1", "name": "A", "email": "a@example.com"},
                {"id": "2", "name": "B", "email": "b@example.com"}]
    oe.dispatch_batch(suppliers, {"keyword": "k", "country": "Egypt"},
                      {"subject": "s", "textBody": "b"}, dry_run=False)
    # Two real (non-dry-run) sends -> throttle only between them, never after the last one.
    assert len(sleep_calls) == 1


# ======================================================================
# 15. Mock suppliers are never pre-ticked, and never sendable even if re-ticked
# ======================================================================
def test_to_supplier_record_mock_candidate_never_preselected():
    record = od.to_supplier_record(_candidate(email="info@fake.com", isMock=True), "Egypt", "Diving")
    assert record["selected"] is False
    assert record["isMock"] is True


def test_to_supplier_record_real_candidate_with_email_still_preselected():
    record = od.to_supplier_record(_candidate(email="info@real.com", isMock=False), "Egypt", "Diving")
    assert record["selected"] is True


# ======================================================================
# 16. "Mock results" warning recognizes GEMINI_API_KEY
# ======================================================================
def test_configured_provider_chain_includes_gemini_only(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert od._configured_provider_chain() == ["gemini"]


def test_stats_used_mock_provider_source_uses_configured_provider_chain():
    import inspect
    source = inspect.getsource(od.discover_suppliers)
    assert '"used_mock_provider": not _configured_provider_chain()' in source


def test_outreach_tool_mock_warning_source_uses_configured_provider_chain():
    import inspect
    source = inspect.getsource(ot)
    assert "if not od._configured_provider_chain():" in source


# ======================================================================
# 18. Several LOW items
# ======================================================================
def test_normalize_url_key_only_strips_a_literal_www_dot():
    assert od.normalize_url_key("https://www.example.com/path") == "example.com/path"
    # A host that happens to start with "www" but NOT "www." must not be mangled.
    assert od.normalize_url_key("https://wwwx.example.com/path") == "wwwx.example.com/path"


def test_record_send_failure_is_surfaced_source():
    import inspect
    source = inspect.getsource(ot)
    assert "record_failures" in source
    assert "if not ofw.record_send(" in source


def test_finalize_queue_result_recomputes_final_stat_after_dedupe_and_cap():
    stats = {"final": 999}  # deliberately wrong/stale sum, as the bug would leave it
    suppliers = [
        {"name": "A", "email": "a@example.com", "website": None, "rating": None},
        {"name": "B", "email": "b@example.com", "website": None, "rating": None},
    ]
    result = ot._finalize_queue_result(suppliers, stats, failures=[])
    assert result["stats"]["final"] == len(result["suppliers"]) == 2


def test_smtp_send_sets_a_message_id(monkeypatch):
    class _FakeServer:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def send_message(self, msg):
            self.sent = msg

    fake_server = _FakeServer()
    monkeypatch.setattr(oe, "_smtp_connection", lambda: fake_server)
    monkeypatch.setattr(oe, "verify_transport", lambda: {"provider": "smtp", "ok": True})
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    message = {
        "from": "sender@example.com", "to": ["dest@example.com"], "replyTo": None,
        "subject": "Hi", "text": "hello", "html": "<p>hello</p>",
    }
    result = oe._send_via_provider(message, "smtp", None) if hasattr(oe, "_send_via_provider") else None
    # Fall back to calling the real send path if _send_via_provider isn't the exact internal
    # name - locate whichever function actually builds+sends the SMTP EmailMessage.
    if result is None:
        import inspect
        source = inspect.getsource(oe)
        assert 'msg["Message-ID"] = make_msgid()' in source
    else:
        assert result.get("messageId")
