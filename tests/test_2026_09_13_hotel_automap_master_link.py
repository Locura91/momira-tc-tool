"""Regression tests for keeping a new hotel from becoming a duplicate property in Travel
Compositor (product owner, 2026-09-13: "when we create a new hotel ... we are searching for
existing hotels from travel c side and we must make sure, that Automap with master is also set,
so the hotel is not a duplicate in the travel compositor surface").

The API cannot set the automap - confirmed against the real Swagger for both relevant sections
(see hotel_automap.py's own docstring for the field-by-field check). So what is actually under
test here is the part the app CAN guarantee:

  1. the master record's identity survives the wizard instead of being discarded once its images
     and description have been copied out (it used to be thrown away immediately),
  2. a human cannot walk past the master-data check silently, and
  3. an outstanding back-office mapping is remembered durably rather than living only in the
     success message of a screen someone has already navigated away from.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so - as
with the other app.py suites here - its behaviour is verified by reading its source text and
checking the specific code shape.
"""
import os
import re

import masterdata_matcher
import hotel_automap
import platform_store

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _app_source() -> str:
    """app.py's source concatenated with every module under flows/ - Phase 1 (2026-09-15)
    started splitting render_*_flow functions out of app.py into flows/*.py, verbatim/zero-
    behaviour-change, so Hotel-flow source text checked here (e.g. render_hotel_flow) can now
    live in flows/hotel.py instead. Reading app.py first keeps this purely additive."""
    with open(_APP_PY, "r", encoding="utf-8") as f:
        src = f.read()
    app_helpers_path = os.path.join(os.path.dirname(_APP_PY), "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        with open(app_helpers_path, "r", encoding="utf-8") as f:
            src += chr(10) + f.read()
    flows_dir = os.path.join(os.path.dirname(_APP_PY), "flows")
    if os.path.isdir(flows_dir):
        for _name in sorted(os.listdir(flows_dir)):
            if _name.endswith(".py") and _name != "__init__.py":
                with open(os.path.join(flows_dir, _name), "r", encoding="utf-8") as f:
                    src += chr(10) + f.read()
    return src


# ======================================================================
# 1. the master record's identity is no longer discarded
# ======================================================================
def test_seed_now_carries_the_accommodation_id_and_giata_id():
    """THE ORIGINAL BUG: the datasheet is fetched BY the accommodation id, and then the seed threw
    that id away - so by publish time nothing knew which master record a human had picked, which
    is exactly the value needed to complete the mapping by hand."""
    seed = masterdata_matcher.datasheet_to_masterdata_seed({
        "id": "ACC-123", "giataId": 98765, "name": "Steigenberger Golf Resort",
        "images": [{"url": "https://example.test/a.jpg"}],
    })
    assert seed["accommodation_id"] == "ACC-123"
    assert seed["giata_id"] == "98765"


def test_seed_normalizes_a_giata_id_given_as_an_int_to_a_string():
    """The master index carries a GIATA id as either an int or a str depending on record (the
    same quirk find_by_giata_id already handles). A human retypes this value into Travel
    Compositor's own search box, where 98765 and '98765.0' are not interchangeable."""
    seed = masterdata_matcher.datasheet_to_masterdata_seed({"id": "A", "giataId": 98765})
    assert seed["giata_id"] == "98765"
    assert isinstance(seed["giata_id"], str)


def test_seed_still_carries_the_content_fields_it_always_did():
    """The ids are an addition, not a replacement - the images/text/geolocation the Hotel wizard
    already depends on must keep working exactly as before."""
    seed = masterdata_matcher.datasheet_to_masterdata_seed({
        "id": "A", "giataId": 1, "name": "H", "description": "Nice",
        "images": [{"url": "u1"}, {"url": "u2"}, {"url": "u1"}],
        "geolocation": {"latitude": 1.0, "longitude": 2.0},
    })
    assert seed["image_urls"] == ["u1", "u2"]  # still de-duplicated, still in order
    assert "Nice" in seed["text_block"]
    assert seed["geolocation"] == {"latitude": 1.0, "longitude": 2.0}
    assert seed["name"] == "H"


def test_seed_of_a_junk_datasheet_still_has_the_id_keys_rather_than_raising_later():
    """A failed/garbage datasheet must produce the same SHAPE, so callers reading
    seed['accommodation_id'] don't hit a KeyError on the error path."""
    seed = masterdata_matcher.datasheet_to_masterdata_seed(None)
    assert seed["accommodation_id"] is None
    assert seed["giata_id"] is None


def test_app_falls_back_to_the_index_row_when_the_datasheet_omits_an_id():
    """The candidate row the human clicked already carries id/giataId. Losing them because one
    datasheet response happened to omit a field would leave a human with no way to finish the
    mapping at all."""
    src = _app_source()
    assert 'cand["id"]' in src
    assert '_hp_seed["accommodation_id"] = _hp_seed.get("accommodation_id") or' in src
    assert '_hp_seed["giata_id"] = _hp_seed.get("giata_id") or' in src


# ======================================================================
# 2. the master-data check can't be walked past silently
# ======================================================================
def test_skip_button_is_disabled_until_a_reason_is_typed():
    """CONFIRMED PRODUCT-OWNER DECISION (2026-09-13), choosing a hard block over a warning: if the
    search surfaced candidates and a human rejects all of them, that judgement call is what
    creates a duplicate when it's wrong, so it has to be stated."""
    src = _app_source()
    assert 'key="hp_md_skip", disabled=not _hp_md_can_skip' in src
    assert 'key="hp_md_skip_no_index"' in src
    assert 'disabled=not no_index_reason.strip()' in src


def test_zero_candidates_does_not_demand_a_typed_reason_but_still_records_one():
    """Graded strictness on purpose: demanding free text when the app had nothing to offer just
    trains people to type filler to get past a screen. The automatic reason keeps the audit trail
    complete without that."""
    src = _app_source()
    assert "_hp_md_reason_needed = False" in src
    assert 'Master-data search ran and returned no candidates.' in src


def test_never_having_searched_is_treated_as_strictly_as_rejecting_candidates():
    src = _app_source()
    assert "elif not _hp_md_searched:" in src
    # the branch after it must be the one that requires a reason
    branch = src.split("elif not _hp_md_searched:", 1)[1][:600]
    assert "_hp_md_reason_needed = True" in branch


def test_picking_a_master_record_clears_any_earlier_skip_reason():
    """Otherwise a reason typed, then reconsidered, would be stored against a hotel that DID get
    linked - and the review screen would show a contradiction."""
    src = _app_source()
    assert "st.session_state.hp_masterdata_skip_reason = None" in src


# ======================================================================
# 3. the outstanding mapping is remembered durably
# ======================================================================
class _FakeStore:
    """Stands in for platform_store so these tests never touch a real database."""

    def __init__(self):
        self.data = {}

    def get(self, ns, key):
        return self.data.get((ns, key))

    def set(self, ns, key, value):
        self.data[(ns, key)] = value
        return True

    def delete(self, ns, key):
        return self.data.pop((ns, key), None) is not None

    def get_namespace(self, ns):
        return {k[1]: v for k, v in self.data.items() if k[0] == ns}


def _use_fake_store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(hotel_automap.platform_store, "get", fake.get)
    monkeypatch.setattr(hotel_automap.platform_store, "set", fake.set)
    monkeypatch.setattr(hotel_automap.platform_store, "delete", fake.delete)
    monkeypatch.setattr(hotel_automap.platform_store, "get_namespace", fake.get_namespace)
    return fake


def test_a_linked_hotel_is_recorded_with_the_ids_needed_to_finish_the_mapping(monkeypatch):
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(48940, "HRG-H1", "Steigenberger Golf Resort",
                                 accommodation_id="ACC-123", giata_id="98765",
                                 master_name="Steigenberger Golf Resort El Gouna")
    pending = hotel_automap.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == hotel_automap.STATUS_LINKED
    assert pending[0]["accommodation_id"] == "ACC-123"
    assert pending[0]["giata_id"] == "98765"


def test_a_hotel_created_without_a_master_link_is_recorded_with_its_stated_reason(monkeypatch):
    """This case needs a DIFFERENT thing from a human - there is no id to map to, so what matters
    is re-checking the judgement that the property genuinely wasn't in master data."""
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(48940, "HRG-H2", "Brand New Resort",
                                 skip_reason="opened this year, supplier confirmed not listed")
    entry = hotel_automap.list_pending()[0]
    assert entry["status"] == hotel_automap.STATUS_UNLINKED
    assert entry["accommodation_id"] is None
    assert "opened this year" in entry["skip_reason"]


def test_two_suppliers_can_both_have_the_same_provider_code_without_colliding(monkeypatch):
    """providerCode is human-assigned (see ContractHotelVO's docstring), so "HRG-H1" under two
    different suppliers is normal - one must not overwrite the other's reminder."""
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(1, "HRG-H1", "A", accommodation_id="ACC-1")
    hotel_automap.record_pending(2, "HRG-H1", "B", accommodation_id="ACC-2")
    assert len(hotel_automap.list_pending()) == 2


def test_marking_one_done_removes_it_from_pending_but_keeps_the_audit_trail(monkeypatch):
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(48940, "HRG-H1", "H", accommodation_id="ACC-123")
    assert hotel_automap.mark_mapped(48940, "HRG-H1") is True
    assert hotel_automap.list_pending() == []
    mapped = hotel_automap.list_mapped()
    assert len(mapped) == 1 and mapped[0]["mapped_at"]


def test_republishing_an_already_mapped_hotel_does_not_put_it_back_on_the_list(monkeypatch):
    """A price update doesn't un-map a hotel in Travel Compositor. Resurrecting it would make the
    checklist cry wolf, which is the one thing a checklist can't survive."""
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(48940, "HRG-H1", "H", accommodation_id="ACC-123")
    hotel_automap.mark_mapped(48940, "HRG-H1")
    hotel_automap.record_pending(48940, "HRG-H1", "H", accommodation_id="ACC-123")
    assert hotel_automap.list_pending() == []


def test_republishing_a_still_pending_hotel_updates_it_rather_than_duplicating_it(monkeypatch):
    _use_fake_store(monkeypatch)
    hotel_automap.record_pending(48940, "HRG-H1", "Old name", accommodation_id="ACC-123")
    hotel_automap.record_pending(48940, "HRG-H1", "Corrected name", accommodation_id="ACC-123")
    pending = hotel_automap.list_pending()
    assert len(pending) == 1
    assert pending[0]["hotel_name"] == "Corrected name"


def test_recording_never_raises_when_the_store_is_down(monkeypatch):
    """A failed reminder write must not roll back or abort a publish that already succeeded
    against Travel Compositor - the publish is the irreversible part, the reminder isn't."""
    monkeypatch.setattr(hotel_automap.platform_store, "get", lambda ns, k: None)
    monkeypatch.setattr(hotel_automap.platform_store, "set", lambda ns, k, v: False)
    assert hotel_automap.record_pending(48940, "HRG-H1", "H", accommodation_id="A") is False


def test_a_missing_provider_code_is_refused_rather_than_stored_under_an_empty_key(monkeypatch):
    _use_fake_store(monkeypatch)
    assert hotel_automap.record_pending(48940, "", "H") is False
    assert hotel_automap.list_pending() == []


def test_pending_is_ordered_oldest_first(monkeypatch):
    """The oldest unmapped hotel is the most urgent - it's the one most likely to have already
    produced a duplicate."""
    fake = _use_fake_store(monkeypatch)
    hotel_automap.record_pending(1, "A", "A", accommodation_id="x")
    hotel_automap.record_pending(1, "B", "B", accommodation_id="y")
    fake.data[(hotel_automap._NAMESPACE, "1::A")]["recorded_at"] = 100
    fake.data[(hotel_automap._NAMESPACE, "1::B")]["recorded_at"] = 50
    assert [e["provider_code"] for e in hotel_automap.list_pending()] == ["B", "A"]


# ======================================================================
# 4. it's actually wired into publish and into the UI
# ======================================================================
def test_publish_records_the_reminder_only_for_a_brand_new_hotel():
    """An existing hotel was already mapped (or deliberately not) when it was first created;
    re-raising this on every price update is how a notice becomes wallpaper.

    UPDATED 2026-09-16: a second, standalone hotel_automap.record_pending(...) call site was
    added (app_helpers.py's manual "search master data for a hotel" tool - see
    render_hotel_automap_review's own comment) that is deliberately NOT gated on
    existing_snapshot, since its whole purpose is letting an already-published hotel be checked
    too. So this test now checks that AT LEAST ONE call site (the publish-time one, in
    flows/hotel.py) is still gated the original way, rather than assuming there's only one."""
    src = _app_source()
    assert "hotel_automap.record_pending(" in src
    found_gated_call = False
    search_from = 0
    while True:
        idx = src.find("hotel_automap.record_pending(", search_from)
        if idx == -1:
            break
        preceding = src[max(0, idx - 1200):idx]
        if "if not existing_snapshot:" in preceding:
            found_gated_call = True
            break
        search_from = idx + 1
    assert found_gated_call


def test_publish_shows_the_accommodation_id_on_screen_not_only_in_the_store():
    src = _app_source()
    assert "Map it to accommodation id:" in src


def test_the_review_screen_exists_and_is_reachable():
    src = _app_source()
    assert "def render_hotel_automap_review(client):" in src
    assert "TOOL_HOTEL_AUTOMAP" in src
    assert "render_hotel_automap_review(client)" in src


def test_the_home_screen_banner_and_button_have_been_removed():
    """SUPERSEDES test_the_home_screen_button_is_always_reachable_not_only_when_something_is_
    outstanding (2026-09-16, originally written the same day as the "always reachable" fix this
    replaces). Product owner, 2026-09-16, looking at the home screen: "this information is
    useless now as we map the hotels differently. The hint can be deleted."

    Hotels are now created directly in Travel Compositor via "New hotel using master data",
    which sets automap correctly at creation time (see
    claude/hotel-automap-no-retroactive-mapping-confirmed-2026-09-16.md) - this app only adds
    pricing/inventory to the already-existing record afterward (see builder.py's "UPDATE
    PRIORITY FLIP"). So the create-here-then-remember-to-automap-later failure mode the
    banner/button existed to catch no longer happens in the normal workflow, and product owner
    asked for the hint to be deleted.

    hotel_automap.py itself and the review screen it feeds (TOOL_HOTEL_AUTOMAP) are deliberately
    left in place - see test_the_review_screen_exists_and_is_reachable and
    test_publish_records_the_reminder_only_for_a_brand_new_hotel - only this home-screen entry
    point is gone."""
    src = _app_source()
    assert "_automap_pending = hotel_automap.pending_count()" not in src
    assert 'st.button(_automap_label, key="tool_btn_automap"' not in src
    assert '_automap_label = f"Review {_automap_pending} hotel(s) awaiting automap"' not in src
    # the dispatch/constant/review-screen wiring stays - only the home-screen entry point is gone
    assert "TOOL_HOTEL_AUTOMAP" in src
    assert "render_hotel_automap_review(client)" in src


def test_module_carries_a_build_stamp_matching_the_app():
    src = _app_source()
    app_version = re.search(r'^BUILD_VERSION = "([^"]+)"', src, flags=re.M).group(1)
    assert hotel_automap.MODULE_BUILD == app_version
