"""Pins the exact guarantee asked for in "Transactional updates for translation state":
a language must never be recorded as translated unless the write that was supposed to
deliver it to Travel Compositor actually succeeded.

INVESTIGATION BEFORE WRITING THIS (2026-08-12): the issue describes a two-phase-commit
design (mark_language_pending / confirm_language_written / clear_pending) built around a
PUT-per-language model. Reading state_store.py and all five sync_*.py modules first showed
that guarantee is already implemented, just at the granularity the real API actually
supports: Travel Compositor's PUT updates a whole entity's datasheets in one call (every
language at once) - there is no per-language PUT to hang a "pending" flag off of. Every
sync_*_from_data function already:
  1. computes which languages actually translated successfully (translate_in_batches'
     failed_languages signal, NOT a fragile "identical to source" heuristic - see
     translator.py),
  2. sends ONE PUT for the whole entity,
  3. calls store.upsert_state (merging into translated_languages) ONLY after that PUT
     returns success, and
  4. returns a "put_failed" status without touching state at all if the PUT fails.

These tests exist to keep that guarantee true, not to add it - a future refactor that
accidentally moves the state write before the PUT check would be caught here immediately.
"""
import pytest

from state_store import StateStore
import sync_ticket
import sync_transfer
import sync_transport
import sync_closed_tour
import sync_hotel


class FakeTranslator:
    """Always "succeeds": every requested field/language comes back as a distinguishable
    (never identical-to-source) string, so these tests isolate the PUT failure path from
    the translation-failure path (covered separately in the ai_extractor/translator tests)."""

    def translate_fields(self, source_fields, target_languages, retries=5):
        return {
            lang: {field: f"[{lang}] {text}" for field, text in source_fields.items()}
            for lang in target_languages
        }


class FakeAPI:
    """update_* methods fail (return an error dict, matching every real api_client.py
    write method's contract) until `succeed_after` calls, then succeed. get_* fetch
    methods just echo back whatever entry was configured."""

    def __init__(self, succeed_after=None):
        self.succeed_after = succeed_after  # None = always fail
        self.call_counts = {}

    def _write(self, method_name):
        self.call_counts[method_name] = self.call_counts.get(method_name, 0) + 1
        if self.succeed_after is not None and self.call_counts[method_name] > self.succeed_after:
            return {"ok": True}
        return {"error": 500, "message": "simulated transient failure"}

    def update_ticket(self, supplier_id, payload):
        return self._write("update_ticket")

    def update_transfer(self, supplier_id, payload):
        return self._write("update_transfer")

    def update_transport(self, supplier_id, payload):
        return self._write("update_transport")

    def update_closed_tour(self, supplier_id, payload):
        return self._write("update_closed_tour")

    def update_hotel(self, supplier_id, payload):
        return self._write("update_hotel")


@pytest.fixture
def store():
    return StateStore()


def ticket_entry(code="TEST-TICKET-1", name="Tokyo City Tour"):
    return {"code": code, "datasheets": {"EN": {
        "name": name, "description": "A test excursion.", "meetingPoint": "Hotel Lobby",
    }}}


def transfer_entry(entity_id="TEST-TRANSFER-1", name="Airport Transfer"):
    return {"id": entity_id, "datasheets": {"EN": {
        "name": name, "description": "A test transfer.", "pickupInformation": "Lobby",
    }}}


def transport_entry(entity_id="TEST-TRANSPORT-1", name="City Bus Route"):
    return {"id": entity_id, "datasheets": {"EN": {
        "name": name, "description": "A test transport.",
    }}}


def closed_tour_entry(code="TEST-TOUR-1", name="Nile Cruise"):
    # active=True: sync_closed_tour_from_data refuses to sync a non-active tour at all
    # (same "only sync live, published entities" rule every product type follows) -
    # without it every call here returns status="skipped" before ever reaching the PUT.
    return {"code": code, "active": True, "datasheets": {"EN": {
        "name": name, "description": "A test cruise.",
    }}}


def hotel_entry(contract_id="TEST-HOTEL-1", name="Test Hotel"):
    return {"contractId": contract_id, "hotelname": name,
            "descriptions": [{"language": "EN", "description": "A test hotel."}]}


# ============================================================
# A failed PUT must never write state - for every product type
# ============================================================
@pytest.mark.parametrize("sync_fn, entry_fn, supplier_id, extra_kwargs, entity_kind, entity_id_field, code_field", [
    (sync_ticket.sync_ticket_from_data, ticket_entry, "48940", {}, "ticket", "code", None),
    (sync_transfer.sync_transfer_from_data, transfer_entry, "48940", {}, "transfer", "id", None),
    (sync_transport.sync_transport_from_data, transport_entry, "48940", {}, "transport", "id", None),
])
def test_a_failed_put_never_marks_any_language_translated(store, sync_fn, entry_fn, supplier_id,
                                                            extra_kwargs, entity_kind,
                                                            entity_id_field, code_field):
    api = FakeAPI(succeed_after=None)  # every write fails
    translator = FakeTranslator()
    entry = entry_fn()
    entity_id = entry[entity_id_field]

    result = sync_fn(api, translator, store, supplier_id, entry, ["FR", "DE", "ES"], dry_run=False)

    assert result["status"] == "put_failed"
    state = store.get_state(entity_kind, supplier_id, entity_id)
    assert state is None, "a failed PUT must leave no trace in the state store at all"


def test_closed_tour_failed_put_never_marks_any_language_translated(store):
    api = FakeAPI(succeed_after=None)
    translator = FakeTranslator()
    entry = closed_tour_entry()

    result = sync_closed_tour.sync_closed_tour_from_data(
        api, translator, store, "48940", entry, ["FR", "DE", "ES"], "TEST-TOUR-1", dry_run=False)

    assert result["status"] == "put_failed"
    assert store.get_state("closed_tour", "48940", "TEST-TOUR-1") is None


def test_hotel_failed_put_never_marks_any_language_translated(store):
    """Hotel is the most intricate case: rooms/supplements/offers are folded into ONE
    update_hotel PUT, and sync_hotel() must demote every one of them to put_failed and
    write NO state for any of them if that single PUT fails."""
    api = FakeAPI(succeed_after=None)
    translator = FakeTranslator()
    entry = hotel_entry()
    entry["rooms"] = [{"providerCode": "ROOM1", "description": "A deluxe room."}]

    result = sync_hotel.sync_hotel(api, translator, store, "48940", entry, ["FR", "DE"], dry_run=False)

    assert result["main"]["status"] == "put_failed"
    for r in result["rooms"]:
        assert r["status"] == "put_failed"
    assert store.get_state("hotel", "48940", "TEST-HOTEL-1") is None
    assert store.get_state("hotel_room", "48940", "TEST-HOTEL-1|room|ROOM1", option_code="ROOM1") is None


# ============================================================
# The next run must still see a language dropped by a failed PUT as "needed" -
# this is the actual end-to-end guarantee the issue asked for.
# ============================================================
def test_after_a_failed_run_the_next_run_still_asks_for_the_same_languages(store):
    api = FakeAPI(succeed_after=None)
    translator = FakeTranslator()
    entry = ticket_entry(code="TEST-TICKET-RETRY-NEEDED")

    first = sync_ticket.sync_ticket_from_data(
        api, translator, store, "48940", entry, ["FR", "DE"], dry_run=False)
    assert first["status"] == "put_failed"

    # Simulate the next run: verify_and_filter_needed must recompute the SAME "needed" set,
    # not a smaller one - nothing must have been silently marked done by the failed attempt.
    still_needed = sync_ticket.verify_and_filter_needed(
        store, "ticket", "48940", "TEST-TICKET-RETRY-NEEDED",
        sync_ticket.compute_hash(sync_ticket.extract_translatable_fields_from_ticket(entry)),
        ["FR", "DE"], entry, sync_ticket.extract_translatable_fields_from_ticket(entry))
    assert set(still_needed) == {"FR", "DE"}, \
        "a failed PUT must not remove any language from what the next run still needs"


def test_a_successful_retry_after_a_failure_marks_only_that_runs_languages(store):
    """A realistic recovery: the first attempt fails outright, the human/CLI retries, and
    THAT attempt succeeds - state must end up correct (all requested languages marked),
    not missing the ones that failed the first time around."""
    entry = ticket_entry(code="TEST-TICKET-RECOVERY")
    translator = FakeTranslator()

    failing_api = FakeAPI(succeed_after=None)
    first = sync_ticket.sync_ticket_from_data(
        failing_api, translator, store, "48940", entry, ["FR", "DE"], dry_run=False)
    assert first["status"] == "put_failed"

    succeeding_api = FakeAPI(succeed_after=0)  # succeeds from the very first call
    second = sync_ticket.sync_ticket_from_data(
        succeeding_api, translator, store, "48940", entry, ["FR", "DE"], dry_run=False)
    assert second["status"] == "updated"
    assert set(second["languages_written"]) == {"FR", "DE"}

    state = store.get_state("ticket", "48940", "TEST-TICKET-RECOVERY")
    assert set(state["translated_languages"]) == {"FR", "DE"}


def test_a_partial_batch_translation_failure_still_writes_only_the_real_successes(store, monkeypatch):
    """The OTHER half of "transactional": even when the PUT succeeds, a language whose
    TRANSLATION failed (not the write) must not be marked done either - translate_in_batches
    reports this via failed_languages, and sync_ticket_from_data must honour it."""
    class PartiallyFailingTranslator:
        def translate_fields(self, source_fields, target_languages, retries=5):
            if "DE" in target_languages:
                raise RuntimeError("simulated provider outage for this batch")
            return {lang: {f: f"[{lang}] {v}" for f, v in source_fields.items()} for lang in target_languages}

    api = FakeAPI(succeed_after=0)
    entry = ticket_entry(code="TEST-TICKET-PARTIAL-TRANSLATE")
    # BATCH_SIZE=1 in sync_ticket.py means each language is its own batch/call, so DE's
    # translate_fields raises on its own and every other language is unaffected.
    result = sync_ticket.sync_ticket_from_data(
        api, PartiallyFailingTranslator(), store, "48940", entry, ["FR", "DE", "ES"], dry_run=False)

    assert result["status"] == "updated"
    assert "DE" not in result["languages_written"]
    assert set(result["languages_written"]) == {"FR", "ES"}
    state = store.get_state("ticket", "48940", "TEST-TICKET-PARTIAL-TRANSLATE")
    assert "DE" not in state["translated_languages"], \
        "a language whose translation itself failed must never be recorded as translated"
    assert set(state["translated_languages"]) == {"FR", "ES"}


# ============================================================
# Nothing to clean up: since no code path ever writes a "pending" state ahead of a PUT,
# there is no stale-pending-flag scenario a crash could leave behind - confirms the
# state store never carries a partial/pending row for an entity that never had a
# successful write.
# ============================================================
def test_no_intermediate_pending_state_ever_exists_between_attempts(store):
    api = FakeAPI(succeed_after=None)
    translator = FakeTranslator()
    entry = ticket_entry(code="TEST-TICKET-NO-PENDING")

    sync_ticket.sync_ticket_from_data(api, translator, store, "48940", entry, ["FR"], dry_run=False)
    sync_ticket.sync_ticket_from_data(api, translator, store, "48940", entry, ["FR"], dry_run=False)
    sync_ticket.sync_ticket_from_data(api, translator, store, "48940", entry, ["FR"], dry_run=False)

    # Three failed attempts in a row - the state row must still not exist at all, not a
    # half-written or "pending" row of any kind.
    assert store.get_state("ticket", "48940", "TEST-TICKET-NO-PENDING") is None
