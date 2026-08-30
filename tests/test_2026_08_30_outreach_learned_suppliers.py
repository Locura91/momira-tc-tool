"""Tests for outreach_learned_suppliers.py and its wiring into outreach_discovery.py and
outreach_tool.py - built 2026-08-30 per the product-owner request: "whenever the human is adding
manually suppliers, so the App can learn which suppliers are needed and to improve the search
results."

Two confirmed design decisions this locks in:
  1. Match scope is Country + Theme/Keyword (an exact normalized match), not Country alone - see
     outreach_learned_suppliers.py's own docstring.
  2. "Improve search results" means both: (a) resurfacing remembered suppliers straight into a
     future matching search's results, and (b) passing them to the AI verification step (when
     configured) as known-good calibration examples for judging OTHER, new candidates.

NOTE ON SHARED STATE: per tests/conftest.py, PLATFORM_STORE_PATH is one shared temp sqlite file
for the whole test session (not reset per test) - same constraint test_stop_sales.py and
test_outreach_followups.py already document. Every test below uses a country/theme (or email)
string unique to itself so it can never collide with another test's data.
"""
import outreach_discovery as od
import outreach_learned_suppliers as ols
import outreach_memory as om
import outreach_tool as ot


def _supplier(name, email=None, website=None):
    return {
        "id": f"manual-{name.lower().replace(' ', '-')}",
        "name": name, "email": email, "social": None, "socialPlatform": None,
        "website": website, "listingUrl": None, "listingSource": None,
        "selectionReason": "Added by hand, not found by the automated search.",
        "reviewSummary": "Added manually.", "rating": None, "reviewCount": None,
        "sources": [], "selected": bool(email), "isMock": False, "addedManually": True,
    }


# ============================================================================
# remember_supplier / get_remembered_for - the storage layer
# ============================================================================
def test_remember_supplier_then_get_remembered_for_round_trips():
    s = _supplier("Nile Sailors Co", email="info@nilesailors-test1.example.com")
    assert ols.remember_supplier("Egypt", "Nile Cruise Test1", s) is True
    found = ols.get_remembered_for("Egypt", "Nile Cruise Test1")
    assert len(found) == 1
    assert found[0]["name"] == "Nile Sailors Co"
    assert found[0]["learnedFrom"]["country"] == "Egypt"
    assert found[0]["learnedFrom"]["theme"] == "Nile Cruise Test1"


def test_remember_supplier_matching_is_case_and_whitespace_insensitive():
    s = _supplier("Desert Wind Tours", email="info@desertwind-test2.example.com")
    assert ols.remember_supplier("  Egypt  ", "  Desert Safari Test2  ", s) is True
    assert len(ols.get_remembered_for("egypt", "desert safari test2")) == 1
    assert len(ols.get_remembered_for("EGYPT", "DESERT SAFARI TEST2")) == 1


def test_get_remembered_for_does_not_match_a_different_theme():
    # CONFIRMED DESIGN DECISION: Country + Theme, not Country alone - a supplier remembered for
    # one theme must NOT resurface on an unrelated theme search in the same country.
    s = _supplier("Felucca Friends", email="info@felucca-test3.example.com")
    ols.remember_supplier("Jordan Test3", "Nile Cruise Test3", s)
    assert ols.get_remembered_for("Jordan Test3", "Nile Cruise Test3") != []
    assert ols.get_remembered_for("Jordan Test3", "Desert Safari Test3") == []


def test_get_remembered_for_does_not_match_a_different_country():
    s = _supplier("Sahara Star", email="info@saharastar-test4.example.com")
    ols.remember_supplier("Morocco Test4", "Desert Safari Test4", s)
    assert ols.get_remembered_for("Tunisia Test4", "Desert Safari Test4") == []


def test_remember_supplier_is_a_safe_no_op_when_called_again_for_the_same_contact():
    # CONFIRMED DESIGN: outreach_tool.py calls this on every rerun of the review screen for
    # every still-present manual add, not just once - repeat calls must not pile up duplicates.
    s = _supplier("Oasis Trails", email="info@oasistrails-test5.example.com")
    assert ols.remember_supplier("Egypt Test5", "Oasis Tour Test5", s) is True
    assert ols.remember_supplier("Egypt Test5", "Oasis Tour Test5", s) is False
    assert len(ols.get_remembered_for("Egypt Test5", "Oasis Tour Test5")) == 1


def test_remember_supplier_declines_with_no_email_or_website():
    # A bare name gives nothing to de-duplicate on later, and nothing a future search could
    # actually find/match this supplier by either - not a useful memory entry.
    s = _supplier("Nameless Operator Test6")
    assert ols.remember_supplier("Egypt Test6", "Some Theme Test6", s) is False
    assert ols.get_remembered_for("Egypt Test6", "Some Theme Test6") == []


def test_remember_supplier_declines_with_a_blank_country():
    s = _supplier("No Country Operator Test7", email="info@nocountry-test7.example.com")
    assert ols.remember_supplier("", "Some Theme Test7", s) is False


def test_remember_supplier_dedupes_on_website_when_there_is_no_email():
    s1 = _supplier("Website Only Op Test8", website="https://website-only-test8.example.com/tours")
    s2 = _supplier("Website Only Op Renamed Test8", website="https://www.website-only-test8.example.com/other")
    assert ols.remember_supplier("Egypt Test8", "Theme Test8", s1) is True
    # Same host (www. and path both ignored by the light dedup key) - treated as a repeat.
    assert ols.remember_supplier("Egypt Test8", "Theme Test8", s2) is False
    assert len(ols.get_remembered_for("Egypt Test8", "Theme Test8")) == 1


# ============================================================================
# resurface_remembered_suppliers - ready-to-merge shape + blocklist filtering
# ============================================================================
def test_resurface_rewrites_selection_reason_and_flags_is_remembered():
    s = _supplier("Luxor Local Guides", email="info@luxorlocal-test9.example.com")
    ols.remember_supplier("Egypt Test9", "Guided Tour Test9", s)
    out = ols.resurface_remembered_suppliers("Egypt Test9", "Guided Tour Test9")
    assert len(out) == 1
    assert out[0]["isRemembered"] is True
    assert "Remembered" in out[0]["selectionReason"]
    assert "Guided Tour Test9" in out[0]["selectionReason"]
    assert "Egypt Test9" in out[0]["selectionReason"]
    assert "learnedFrom" not in out[0]  # internal bookkeeping, not part of the supplier shape


def test_resurface_excludes_a_supplier_now_on_the_blocklist():
    s = _supplier("Later Blocked Co", website="https://laterblocked-test10.example.com")
    ols.remember_supplier("Egypt Test10", "Theme Test10", s)
    assert len(ols.resurface_remembered_suppliers("Egypt Test10", "Theme Test10")) == 1

    om.add_domain_to_blocklist("laterblocked-test10.example.com")
    try:
        assert ols.resurface_remembered_suppliers("Egypt Test10", "Theme Test10") == []
    finally:
        om.remove_domain_from_blocklist("laterblocked-test10.example.com")


def test_resurface_returns_empty_list_for_a_never_remembered_combination():
    assert ols.resurface_remembered_suppliers("Nowhereland Test11", "Nothing Test11") == []


# ============================================================================
# list_all / forget_supplier - the admin/management surface
# ============================================================================
def test_list_all_includes_country_and_theme_for_each_row():
    s = _supplier("Admin Listed Co", email="info@adminlisted-test12.example.com")
    ols.remember_supplier("Egypt Test12", "Theme Test12", s)
    rows = ols.list_all()
    match = next((r for r in rows if r["name"] == "Admin Listed Co"), None)
    assert match is not None
    assert match["country"] == "Egypt Test12"
    assert match["theme"] == "Theme Test12"
    assert match["email"] == "info@adminlisted-test12.example.com"


def test_forget_supplier_removes_it_and_reports_true():
    s = _supplier("Forget Me Co", email="info@forgetme-test13.example.com")
    ols.remember_supplier("Egypt Test13", "Theme Test13", s)
    assert len(ols.get_remembered_for("Egypt Test13", "Theme Test13")) == 1
    assert ols.forget_supplier("Egypt Test13", "Theme Test13", s["id"]) is True
    assert ols.get_remembered_for("Egypt Test13", "Theme Test13") == []


def test_forget_supplier_returns_false_for_an_id_that_is_not_there():
    assert ols.forget_supplier("Egypt Test14", "Theme Test14", "manual-does-not-exist") is False


# ============================================================================
# discover_suppliers() wiring - recall
# ============================================================================
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_discover_suppliers_resurfaces_a_remembered_supplier_when_automated_search_finds_nothing(monkeypatch):
    _no_api_keys(monkeypatch)
    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics",
                        lambda source, query, country, keyword, domains, max_results: ([], None))
    s = _supplier("Remembered Only Co", email="info@rememberedonly-test15.example.com")
    ols.remember_supplier("Testland15", "Camel Trek Test15", s)

    result = od.discover_suppliers("Testland15", "", "Camel Trek Test15")
    names = [sup["name"] for sup in result["suppliers"]]
    assert "Remembered Only Co" in names
    assert result["stats"]["remembered_available"] == 1
    assert result["stats"]["remembered_added"] == 1


def test_discover_suppliers_does_not_resurface_for_a_different_theme(monkeypatch):
    _no_api_keys(monkeypatch)
    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics",
                        lambda source, query, country, keyword, domains, max_results: ([], None))
    s = _supplier("Wrong Theme Co", email="info@wrongtheme-test16.example.com")
    ols.remember_supplier("Testland16", "Camel Trek Test16", s)

    result = od.discover_suppliers("Testland16", "", "Desert Safari Test16")
    names = [sup["name"] for sup in result["suppliers"]]
    assert "Wrong Theme Co" not in names
    assert result["stats"]["remembered_available"] == 0


def test_discover_suppliers_merges_a_remembered_supplier_with_a_matching_fresh_find(monkeypatch):
    # A remembered supplier the automated search ALSO independently finds again this run must
    # collapse into ONE row, not appear twice.
    _no_api_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

    def fake_search(source, query, country, keyword, domains, max_results):
        if source != "supplier_country":
            return [], None
        return ([{"title": "Twice Found Co tours", "url": "https://twicefound-test17.example.com",
                  "snippet": "A wonderful tours operator. rated 4.8 stars, 250 reviews. "
                             "Contact: info@twicefound-test17.example.com"}], None)

    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics", fake_search)
    monkeypatch.setattr(od, "is_ai_verification_enabled", lambda: False)

    s = _supplier("Twice Found Co", email="info@twicefound-test17.example.com")
    ols.remember_supplier("Testland17", "Tours Test17", s)

    result = od.discover_suppliers("Testland17", "", "Tours Test17")
    matches = [sup for sup in result["suppliers"] if "Twice Found" in (sup.get("name") or "")]
    assert len(matches) == 1


# ============================================================================
# AI verification calibration wiring
# ============================================================================
def test_build_verification_prompt_includes_known_examples_when_given():
    prompt = od._build_verification_prompt(
        [{"id": "c1", "name": "Some Candidate", "source": "supplier_country",
          "sourceUrl": "https://example.com", "snippet": "..."}],
        "Egypt", "Nile Cruise",
        known_examples=[{"name": "Confirmed Good Co", "website": "https://confirmedgood.example.com"}],
    )
    assert "Confirmed Good Co" in prompt
    assert "confirmedgood.example.com" in prompt
    assert "calibrate" in prompt.lower()


def test_build_verification_prompt_omits_examples_block_when_none_given():
    prompt = od._build_verification_prompt(
        [{"id": "c1", "name": "Some Candidate", "source": "supplier_country",
          "sourceUrl": "https://example.com", "snippet": "..."}],
        "Egypt", "Nile Cruise", known_examples=None,
    )
    assert "already manually confirmed" not in prompt


def test_discover_suppliers_passes_remembered_suppliers_to_verify_candidates(monkeypatch):
    _no_api_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(od, "is_ai_verification_enabled", lambda: True)

    def fake_search(source, query, country, keyword, domains, max_results):
        if source != "supplier_country":
            return [], None
        return ([{"title": "New Candidate Co tours", "url": "https://newcandidate-test18.example.com",
                  "snippet": "A wonderful tours operator. rated 4.8 stars, 250 reviews. "
                             "Contact: info@newcandidate-test18.example.com"}], None)

    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics", fake_search)

    captured = {}

    def fake_verify(candidates, country, keyword, known_examples=None):
        captured["known_examples"] = known_examples
        return None  # skip actual verdicts - only the call args matter here

    monkeypatch.setattr(od, "verify_candidates", fake_verify)

    s = _supplier("Calibration Reference Co", email="info@calibrationref-test18.example.com")
    ols.remember_supplier("Testland18", "Tours Test18", s)

    od.discover_suppliers("Testland18", "", "Tours Test18")
    assert captured["known_examples"] is not None
    assert any(e["name"] == "Calibration Reference Co" for e in captured["known_examples"])


# ============================================================================
# outreach_tool.py wiring - the write side (manual adds -> memory)
# ============================================================================
def test_remember_manually_added_suppliers_only_writes_addedManually_rows(monkeypatch):
    captured = []
    monkeypatch.setattr(ot.oln, "remember_supplier",
                        lambda country, theme, supplier: captured.append((country, theme, supplier["name"])))

    suppliers = [
        _supplier("Found By Search", email="info@foundbysearch-test19.example.com"),
        _supplier("Added By Hand", email="info@addedbyhand-test19.example.com"),
    ]
    suppliers[0]["addedManually"] = False
    suppliers[1]["addedManually"] = True

    ot._remember_manually_added_suppliers(
        suppliers, [{"country": "Testland19", "keyword": "Theme Test19"}])

    assert captured == [("Testland19", "Theme Test19", "Added By Hand")]


def test_remember_manually_added_suppliers_writes_under_every_combination():
    # A Country-Scope run's manual add is remembered against every distinct combination that
    # was actually part of that run - see outreach_tool.py's own comment for why.
    s = _supplier("Multi Combo Co", email="info@multicombo-test20.example.com")
    combinations = [
        {"country": "Testland20", "keyword": "Theme A Test20"},
        {"country": "Testland20", "keyword": "Theme B Test20"},
    ]
    ot._remember_manually_added_suppliers([s], combinations)
    assert len(ols.get_remembered_for("Testland20", "Theme A Test20")) == 1
    assert len(ols.get_remembered_for("Testland20", "Theme B Test20")) == 1


def test_remember_manually_added_suppliers_is_a_no_op_with_no_combinations():
    s = _supplier("No Combo Co", email="info@nocombo-test21.example.com")
    # Must not raise even though there's nothing to attribute this add to.
    ot._remember_manually_added_suppliers([s], [])
    ot._remember_manually_added_suppliers([s], None)
