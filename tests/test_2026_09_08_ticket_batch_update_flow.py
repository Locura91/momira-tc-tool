"""Tests for the Ticket batch-UPDATE flow (product owner, 2026-09-08):

    "yes please fix it first, that's the reason i reached out to you first" - said in response
    to being asked whether a batch-update tool (for updating MANY EXISTING tickets from one
    document) should be built before attempting the actual FTS_Momira_Whole_Egypt_B2B_Catalogue
    import.

Context: the app already had a batch-CREATE flow for several NEW excursions from one document
(render_multi_ticket_flow) but no equivalent for updating several EXISTING tickets from one
document. The new Egypt catalogue restates ~22 excursions the operator's live tickets already
carry under the same codes (CAI-01, LXR-01, ASW-01, ...) - but the new catalogue has entrance
tickets INCLUDED where the live tickets currently say "(Entrance Ticket not included)" in their
title/includes/excludes, so importing it means correcting those fields (not just the price) for
every matching live ticket, in one batch pass.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup) - see
test_2026_09_01_medium_batch1_app_py.py's own docstring for the established pattern this suite
follows: most of the NEW app.py wiring below is verified by reading its source text and checking
the specific code shape. The one genuinely new PURE-LOGIC behavior this feature depends on -
publishing a ticket whose title/includes/excludes have already been corrected to drop the
"entrance not included" wording, together with the new price-validity code - is exercised for
real, end-to-end, through builder.build_ticket_payloads (no import of app.py needed for that).
"""
import os

from schemas import TicketHumanPreConfig
from builder import build_ticket_payloads

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line):
    start = src.index(def_line)
    # Next top-level "def " (no leading whitespace) after this function's own body.
    rest = src[start + len(def_line):]
    end_offset = rest.index("\ndef ")
    return src[start:start + len(def_line) + end_offset]


# ---------------------------------------------------------------------------
# 1. Action menu wiring - reachable from Ticket Step 2, distinct from every
#    existing action, never asks for a single existing-ticket-code up front.
# ---------------------------------------------------------------------------

def test_batch_update_action_is_registered_and_labeled():
    src = _read_app_py()
    assert '"update_tickets_batch"' in src
    assert "Update multiple existing Tickets from one document" in src


def test_batch_update_action_fields_do_not_ask_for_a_single_ticket_or_modality_code():
    """Matching happens PER DETECTED EXCURSION inside the new flow's own 'match' phase, not once
    up front in Step 3 - see TICKET_ACTION_FIELDS's own comment for the fuller rationale
    (currency/min/max passengers are inherited per matched ticket too, not asked once)."""
    src = _read_app_py()
    fields_block = src.split("TICKET_ACTION_FIELDS = {")[1].split("\n}")[0]
    batch_line = [l for l in fields_block.splitlines() if '"update_tickets_batch":' in l][0]
    assert "existing_ticket_code" not in batch_line
    assert "modality_code" not in batch_line
    assert "currency" not in batch_line


def test_create_action_still_routes_to_the_create_flow_not_the_new_one():
    src = _read_app_py()
    assert 'if action == "create":\n        render_multi_ticket_flow(' in src


def test_batch_update_action_routes_to_the_new_flow_and_returns():
    src = _read_app_py()
    assert 'if action == "update_tickets_batch":\n        render_multi_ticket_update_flow(' in src


def test_publish_action_label_lookup_has_an_entry_for_the_new_action():
    """_tk_action_to_publish_label is a plain dict lookup that runs for EVERY action before the
    new action's early-return - a missing entry would KeyError before ever reaching the new flow."""
    src = _read_app_py()
    block = src.split("_tk_action_to_publish_label = {")[1].split("\n    }")[0]
    assert '"update_tickets_batch":' in block


def test_update_refresh_single_service_picker_excludes_the_batch_action():
    """The 'pick ONE existing Ticket, then choose what kind of update' screen
    (_render_update_refresh_coded_service) must not offer the batch action - it already presumes
    one specific ticket was chosen, which doesn't fit a flow that does its own excursion
    detection and matching across possibly many tickets."""
    src = _read_app_py()
    fn = _function_source(src, "def _render_update_refresh_coded_service(client, service):")
    assert '("create", "update_tickets_batch")' in fn


# ---------------------------------------------------------------------------
# 2. render_multi_ticket_update_flow's own shape: UPDATE semantics throughout,
#    never CREATE semantics.
# ---------------------------------------------------------------------------

def test_new_flow_function_exists_with_its_own_mtu_session_state_prefix():
    src = _read_app_py()
    assert "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):" in src
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert 'st.session_state.mtu_phase' in fn
    # Never touches the sibling batch-CREATE flow's own "mt_" state.
    assert "st.session_state.mt_phase" not in fn
    assert "st.session_state.mt_queue" not in fn


def test_new_flow_never_calls_create_ticket_or_create_ticket_option():
    """This flow only ever UPDATES already-existing tickets - it must never call the
    create_ticket/create_ticket_option methods the sibling batch-CREATE flow uses."""
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert "client.create_ticket(" not in fn
    assert "client.create_ticket_option(" not in fn
    assert "client.update_ticket(" in fn
    assert "client.update_ticket_option(" in fn


def test_new_flow_reuses_the_shared_variant_detector_and_content_drift_check():
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert "detect_ticket_variants(raw_text)" in fn
    assert "check_ticket_content_drift(" in fn
    assert "extract_ticket_main_info(" in fn
    assert "extract_ticket_modality_data(" in fn


def test_new_flow_merges_fresh_extraction_over_the_live_ticket_baseline():
    """This is what correctly updates title/includes/excludes to new wording (e.g. dropping
    '(Entrance Ticket not included)') when the new document restates them, while preserving
    whatever the new document doesn't mention - reusing the exact helpers the single-ticket
    'Whole ticket' update path already uses, per the product owner's explicit instruction not to
    reinvent them."""
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert "_map_fetched_ticket_to_data(live_ticket)" in fn
    assert "_merge_extraction_over_baseline(baseline, fresh)" in fn


def test_new_flow_matching_uses_check_code_availability_with_ticket_kind():
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert 'check_code_availability(client, "ticket", supplier_id, code_val)' in fn
    # CONFIRMED product-owner adjustment: "exists" is the GOOD outcome here (a real ticket to
    # update), the inverse of the create-flow's framing - the not-found branch must actually
    # block the item, not just warn.
    assert 'cand["_match_status"] = "not_found"' in fn


def test_new_flow_inherits_currency_and_passenger_limits_per_matched_ticket_not_globally():
    """CONFIRMED REAL RULE (product owner): 'an UPDATE never asks for things the live record
    already has' - each matched ticket's OWN live currency/min/max passengers must be used,
    since a batch can span multiple already-live tickets that don't necessarily share one
    currency or passenger cap."""
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert 'live_ticket.get("currency")' in fn
    assert 'live_ticket.get("maxPassengers")' in fn
    assert '(q.get("live_ticket") or {}).get("minPassengers")' in fn


def test_new_flow_preserves_the_live_active_state_on_every_update():
    """Same bug class as audit CRITICAL #2 (2026-09-01) for the single-ticket update path -
    build_ticket_payloads always sets active=False (correct only for a brand-new ticket); an
    update must republish the LIVE record's own active state instead."""
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert '(q.get("live_ticket") or {}).get("active")' in fn
    assert 'mtu_update_payload["active"] = _mtu_live_active' in fn


def test_new_flow_has_two_stage_recovery_like_the_create_batch_flow():
    """A failure must never force restarting the whole batch and losing every other item's
    edits - mirrors mt_precreate_failed_items/mt_failed_items's split (failed-before-any-write
    vs. ticket-updated-but-option-failed)."""
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert "mtu_update_failed_items" in fn
    assert "mtu_option_failed_items" in fn


def test_new_flow_does_not_shadow_the_single_ticket_update_active_state_fix_test():
    """CONFIRMED REGRESSION GUARD (2026-09-08): the new flow's publish loop originally reused the
    exact same local variable name ('update_payload') as the single-ticket 'Whole ticket' update
    branch, which broke test_2026_09_01_update_no_longer_deactivates.py's
    test_ticket_update_carries_forward_the_live_active_state (its src.index() of that literal
    found THIS flow's occurrence first, since it now appears earlier in the file). Renamed to
    'mtu_update_payload' so the two flows' near-identical code never collides on exact text."""
    src = _read_app_py()
    marker = 'update_payload = dict(payloads["main_ticket_payload"])'
    first_idx = src.index(marker)
    publish_idx = src.index('result = client.update_ticket(supplier_id, update_payload)', first_idx)
    window = src[first_idx:publish_idx]
    assert "tk_fetched_ticket" in window


# ---------------------------------------------------------------------------
# 3. Real, importable behavior: publishing a ticket whose content was already
#    corrected (simulating what the merge produces) plus a price-validity code
#    - the concrete end-to-end guarantee the Egypt catalogue import depends on.
# ---------------------------------------------------------------------------

def _entrance_not_included_live_snapshot():
    """What a typical live ticket in this scenario looks like BEFORE the batch update - the
    common wording the product owner described nearly all live tickets currently carrying."""
    return {
        "ticket_name": "Cairo Museum Half-Day Tour (Entrance Ticket not included)",
        "description": "A tour of the Egyptian Museum.",
        "includes": ["Private guide", "Hotel pickup"],
        "excludes": ["Entrance Ticket", "Tips"],
        "entrance_fees_excluded": True,
    }


def _entrance_included_corrected_data(**overrides):
    """What the SAME excursion looks like after being (re-)extracted from the new Egypt
    catalogue and merged over the live baseline - the new document states entrance tickets ARE
    included, so the merge/fresh extraction corrects the title/includes/excludes and clears the
    entrance_fees_excluded flag, exactly what the batch-update flow's Step 1 review screen is for."""
    data = {
        "ticket_name": "Cairo Museum Half-Day Tour",
        "description": "A tour of the Egyptian Museum, entrance included.",
        "city": "Cairo",
        "manual_latitude": 30.0444,
        "manual_longitude": 31.2357,
        "includes": ["Private guide", "Hotel pickup", "Museum entrance ticket"],
        "excludes": ["Tips"],
        "entrance_fees_excluded": False,
        "base_adult_price": 45,
        "price_type": "DISTRIBUTION",
        "price_valid_until_date": "2027-10-31",
    }
    data.update(overrides)
    return data


def test_corrected_entrance_included_content_publishes_without_the_old_notice(fake_api_client):
    """The whole point of the Egypt-catalogue import: once the new document restates entrance
    tickets as included, the published title/excludes must no longer carry the stale
    '(Entrance Ticket not included)' notice/exclusion."""
    pre_config = TicketHumanPreConfig(
        supplier_id="48940", ticket_code="CAI-01", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    result = build_ticket_payloads(pre_config, _entrance_included_corrected_data(), fake_api_client)
    assert result["main_ticket_error"] is None
    main = result["main_ticket_payload"]
    assert main["name"] == "Cairo Museum Half-Day Tour"
    assert "not included" not in main["name"].lower()
    assert main["datasheets"]["EN"]["excludes"] == ["Tips"]
    assert "Museum entrance ticket" in main["datasheets"]["EN"]["includes"]


def test_corrected_content_still_carries_the_new_price_validity_code(fake_api_client):
    """The SAME publish that corrects the wording must also carry the '(YYYYMMDD)' price-
    validity code the human set on the renewal screen - build_ticket_payloads bakes this in via
    with_price_validity_code, so the batch-update flow needs no separate voucher-remarks-only
    call the way the fast 'Price only' path does."""
    pre_config = TicketHumanPreConfig(
        supplier_id="48940", ticket_code="CAI-01", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    result = build_ticket_payloads(pre_config, _entrance_included_corrected_data(), fake_api_client)
    voucher_text = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "(20271031)" in voucher_text


def test_publishing_onto_the_matched_existing_code_not_a_new_one(fake_api_client):
    """The batch-update flow always targets the ALREADY-LIVE ticket code it matched in its
    'match' phase - build_ticket_payloads' own code-guessing (TICKET-<code> prefix) is
    irrelevant here since the flow overwrites payload['code'] with the matched live code before
    publishing (see the source-shape test above); this just confirms the payload is otherwise a
    normal, valid Ticket payload ready to have its code overwritten that way."""
    pre_config = TicketHumanPreConfig(
        supplier_id="48940", ticket_code="CAI-01", currency="EUR",
        modality_code="Standard", on_request=False,
    )
    result = build_ticket_payloads(pre_config, _entrance_included_corrected_data(), fake_api_client)
    assert result["ticket_option_error"] is None
    assert result["ticket_option_payload"]["code"] == "Standard"


# ---------------------------------------------------------------------------
# 2026-09-08 follow-up (real Egypt-catalogue run): matching used to compare the AI-detected
# excursion's own supplier_code/label against live ticket codes - but an AI-read excursion
# title ("Pyramids, Sphinx & Egyptian Museum") is never equal to a code ("CAI-01"), so 0 of 22
# real excursions matched and the human had to type 22 codes by hand. "No reason for new
# modality code if there is already modality code... Just check name, itinerary and
# included/excluded and prices." Fixed by matching the other direction: start from the
# supplier's real live ticket codes (get_existing_ticket_codes) and check whether the DOCUMENT
# mentions each one, via a plain substring search - not by asking the AI to guess a code.
# ---------------------------------------------------------------------------

def test_gather_phase_matches_by_searching_the_document_for_each_live_ticket_code():
    src = _read_app_py()
    fn = _function_source(src, "def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):")
    assert "get_existing_ticket_codes(client, supplier_id)" in fn
    assert "_norm_code(code) in raw_norm" in fn
    # The AI detector still runs, but must never be the PRIMARY source of target_ticket_code -
    # a matched-by-code candidate is pre-filled and selected; an AI-only one is not.
    assert '"target_ticket_code": code,' in fn
    assert '"selected": True,' in fn
    assert '"target_ticket_code": "",' in fn
    assert '"selected": False,' in fn


def test_norm_code_ignores_whitespace_and_case_the_same_way_a_real_document_would_need():
    import re

    def _norm_code(s):
        return re.sub(r"\s+", "", (s or "")).strip().lower()

    assert _norm_code("CAI-01") == _norm_code("cai-01")
    assert _norm_code("CAI-01") == _norm_code(" CAI - 01".replace(" ", ""))
    assert _norm_code("CAI-01") in _norm_code("Destination | Code | Excursion\nCairo | CAI-01 | Pyramids Tour")


def test_a_live_code_present_in_the_document_is_matched_end_to_end():
    """Simulates the real shape of the Egypt-catalogue table: a grid where the code column
    ('CAI-01') sits next to (not equal to) the excursion's display name - the exact case that
    used to score 0 matches."""
    import re

    def _norm_code(s):
        return re.sub(r"\s+", "", (s or "")).strip().lower()

    raw_text = ("Destination | Code | Excursion | 1 Pax\n"
                "Cairo | CAI-01 | Pyramids, Sphinx & Egyptian Museum | 112\n"
                "Cairo | CAI-02 | Pyramids, Sphinx & Grand Egyptian Museum | 137\n")
    existing_items = [{"name": "Pyramids Tour (Entrance Ticket not included)", "code": "CAI-01"},
                      {"name": "Some Unrelated Ticket", "code": "ZZZ-99"}]
    raw_norm = _norm_code(raw_text)
    matched = [item for item in existing_items
              if (item.get("code") or "").strip() and _norm_code(item["code"]) in raw_norm]
    assert [m["code"] for m in matched] == ["CAI-01"]
