"""Tests for the Stop Sales Email Reader's release/removal, "almost full" exclusion, and
sender -> supplier matching rules.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16), three message types the tool must handle
correctly, plus one overall rule:
  1. New stop sale for any SERVICE -> included on the correct product (already worked;
     unchanged here).
  2. New re-open selling date -> an EXISTING stop sale must be REMOVED, not added to
     (stop_sales_parser.remove_stop_sales, apply_to_tour_option/apply_to_hotel_rate with
     is_release=True).
  3. "Almost full" (limited but still available) -> NOT a stop sale at all (prompt-only rule,
     spot-checked here by asserting the guidance text is actually present in the prompt sent
     to the model - the real behavior can only be confirmed against a live model call).
  4. "Stop sale will come from a specific mail, which must be the first time matched to an
     existing supplier from our system" -> the first sender/supplier match is remembered
     (stop_sales_tool.remembered_supplier_for / remember_supplier_for), keyed by domain so
     every address at the same DMC matches automatically afterwards.
"""
import stop_sales_parser as ssp
import stop_sales_tool as sst


# ======================================================================
# remove_stop_sales - the "re-open" / release half of the lifecycle
# ======================================================================
def test_remove_stop_sales_removes_an_exact_match():
    existing = [{"start": "2026-08-12", "end": "2026-08-19"}]
    result = ssp.remove_stop_sales(existing, [{"start": "2026-08-12", "end": "2026-08-19"}])
    assert result["merged"] == []
    assert len(result["removed"]) == 1
    assert result["not_found"] == []


def test_remove_stop_sales_leaves_other_live_ranges_untouched():
    existing = [{"start": "2026-08-12", "end": "2026-08-19"},
                {"start": "2026-09-01", "end": "2026-09-05"}]
    result = ssp.remove_stop_sales(existing, [{"start": "2026-08-12", "end": "2026-08-19"}])
    assert result["merged"] == [{"start": "2026-09-01", "end": "2026-09-05"}]


def test_remove_stop_sales_auto_splits_a_partial_overlap():
    # CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): supplier releases 12-15 Aug out of a
    # live 12-19 Aug block - the block is now split, leaving 16-19 Aug still blocked.
    existing = [{"start": "2026-08-12", "end": "2026-08-19"}]
    result = ssp.remove_stop_sales(existing, [{"start": "2026-08-12", "end": "2026-08-15"}])
    assert result["merged"] == [{"start": "2026-08-16", "end": "2026-08-19"}]
    assert len(result["removed"]) == 1
    assert result["not_found"] == []


def test_remove_stop_sales_auto_splits_a_middle_overlap_into_two_remaining_pieces():
    # Releasing a window entirely inside a wider live block leaves both the before- and
    # after-slice still blocked.
    existing = [{"start": "2026-08-01", "end": "2026-08-31"}]
    result = ssp.remove_stop_sales(existing, [{"start": "2026-08-10", "end": "2026-08-15"}])
    assert sorted(result["merged"], key=lambda r: r["start"]) == [
        {"start": "2026-08-01", "end": "2026-08-09"},
        {"start": "2026-08-16", "end": "2026-08-31"},
    ]
    assert len(result["removed"]) == 1


def test_remove_stop_sales_release_with_no_overlap_is_still_not_found():
    existing = [{"start": "2026-08-12", "end": "2026-08-19"}]
    result = ssp.remove_stop_sales(existing, [{"start": "2026-09-01", "end": "2026-09-05"}])
    assert result["merged"] == existing
    assert result["removed"] == []
    assert len(result["not_found"]) == 1


def test_remove_stop_sales_does_not_double_remove_the_same_range_twice():
    existing = [{"start": "2026-08-12", "end": "2026-08-19"}]
    release = [{"start": "2026-08-12", "end": "2026-08-19"},
               {"start": "2026-08-12", "end": "2026-08-19"}]
    result = ssp.remove_stop_sales(existing, release)
    assert result["merged"] == []
    assert len(result["removed"]) == 1
    assert len(result["not_found"]) == 1     # the second occurrence has nothing left to match


# ======================================================================
# normalize_sender - for the sender -> supplier matching rule
# ======================================================================
def test_normalize_sender_pulls_address_and_domain_from_a_display_name_header():
    info = ssp.normalize_sender("DMC Nile Cruises <Info@Nile-DMC.com>")
    assert info["email"] == "info@nile-dmc.com"
    assert info["domain"] == "nile-dmc.com"


def test_normalize_sender_handles_a_bare_address():
    info = ssp.normalize_sender("ops@example.com")
    assert info["email"] == "ops@example.com"
    assert info["domain"] == "example.com"


def test_normalize_sender_handles_empty_input():
    info = ssp.normalize_sender("")
    assert info == {"email": "", "domain": ""}


# ======================================================================
# "Almost full" exclusion - confirm the rule text actually reached the prompt
# ======================================================================
def test_prompt_tells_the_model_almost_full_is_not_a_stop_sale():
    prompt = ssp.STOP_SALES_EXTRACTION_SYSTEM_PROMPT
    assert "ALMOST FULL" in prompt
    assert "is_stop_sale\": false" in prompt


# ======================================================================
# Sender -> supplier memory (first-time match rule)
# ======================================================================
def test_remembered_supplier_for_returns_none_before_any_match():
    assert sst.remembered_supplier_for("brand-new-dmc-domain.example") is None


def test_remember_then_recall_supplier_for_a_sender():
    domain = "nile-cruise-test-domain.example"
    assert sst.remembered_supplier_for(domain) is None
    ok = sst.remember_supplier_for(domain, "48940", "Nile Cruises Ltd — ID 48940")
    assert ok
    remembered = sst.remembered_supplier_for(domain)
    assert remembered["supplier_id"] == "48940"
    assert remembered["supplier_label"] == "Nile Cruises Ltd — ID 48940"
    assert remembered.get("first_matched_at")


def test_remember_supplier_for_does_not_overwrite_a_conflicting_existing_match():
    domain = "conflict-test-domain.example"
    assert sst.remember_supplier_for(domain, "111", "Supplier A")
    # A different supplier for the SAME domain must not silently replace the first match.
    ok = sst.remember_supplier_for(domain, "222", "Supplier B")
    assert ok is False
    assert sst.remembered_supplier_for(domain)["supplier_id"] == "111"


def test_remember_supplier_for_resaving_the_same_supplier_is_a_harmless_no_op():
    domain = "resave-test-domain.example"
    assert sst.remember_supplier_for(domain, "999", "Supplier X")
    first = sst.remembered_supplier_for(domain)
    assert sst.remember_supplier_for(domain, "999", "Supplier X (renamed)")
    second = sst.remembered_supplier_for(domain)
    # first_matched_at is preserved across the no-op re-save.
    assert second["first_matched_at"] == first["first_matched_at"]


# ======================================================================
# apply_to_tour_option / apply_to_hotel_rate with is_release=True
# ======================================================================
class _FakeTourClient:
    def __init__(self):
        self.updated_payload = None

    def update_closed_tour_option(self, supplier_id, tour_code, payload):
        self.updated_payload = payload
        return payload


def test_apply_to_tour_option_release_removes_the_matching_block():
    option = {"code": "STD", "stopSales": [{"start": "2026-08-12", "end": "2026-08-19"}]}
    client = _FakeTourClient()
    result = sst.apply_to_tour_option(client, "48940", "ASW-1", option,
                                      [{"start": "2026-08-12", "end": "2026-08-19"}],
                                      is_release=True)
    assert result["status"] == "updated"
    assert client.updated_payload["stopSales"] == []


def test_apply_to_tour_option_release_with_no_match_is_unchanged_and_writes_nothing():
    option = {"code": "STD", "stopSales": [{"start": "2026-08-12", "end": "2026-08-19"}]}
    client = _FakeTourClient()
    result = sst.apply_to_tour_option(client, "48940", "ASW-1", option,
                                      [{"start": "2026-09-01", "end": "2026-09-05"}],
                                      is_release=True)
    assert result["status"] == "unchanged"
    assert len(result["not_found"]) == 1
    assert client.updated_payload is None


def test_apply_to_tour_option_add_still_works_as_before():
    option = {"code": "STD", "stopSales": []}
    client = _FakeTourClient()
    result = sst.apply_to_tour_option(client, "48940", "ASW-1", option,
                                      [{"start": "2026-08-12", "end": "2026-08-19"}])
    assert result["status"] == "updated"
    assert client.updated_payload["stopSales"] == [{"start": "2026-08-12", "end": "2026-08-19"}]


class _FakeHotelClient:
    def __init__(self):
        self.updated_payload = None

    def update_hotel_rates(self, supplier_id, provider_code, payload):
        self.updated_payload = payload
        return payload


def test_apply_to_hotel_rate_release_removes_from_the_named_room_only():
    rate = {"name": "Standard Rate", "stopSales": [
        {"roomName": "Superior Room", "stopSales": [{"start": "2026-08-12", "end": "2026-08-19"}]},
        {"roomName": "Deluxe Room", "stopSales": [{"start": "2026-08-12", "end": "2026-08-19"}]},
    ]}
    client = _FakeHotelClient()
    result = sst.apply_to_hotel_rate(client, "48940", "CAI-H1", rate,
                                     [{"start": "2026-08-12", "end": "2026-08-19"}],
                                     ["Superior Room"], is_release=True)
    assert result["status"] == "updated"
    groups = {g["roomName"]: g["stopSales"] for g in client.updated_payload["stopSales"]}
    assert groups["Superior Room"] == []
    assert groups["Deluxe Room"] == [{"start": "2026-08-12", "end": "2026-08-19"}]


def test_apply_to_hotel_rate_release_skips_a_room_with_nothing_live():
    rate = {"name": "Standard Rate", "stopSales": []}
    client = _FakeHotelClient()
    result = sst.apply_to_hotel_rate(client, "48940", "CAI-H1", rate,
                                     [{"start": "2026-08-12", "end": "2026-08-19"}],
                                     ["Superior Room"], is_release=True)
    assert result["status"] == "unchanged"
    assert client.updated_payload is None
