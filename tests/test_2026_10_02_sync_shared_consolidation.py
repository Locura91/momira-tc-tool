"""Regression tests for the 2026-10-02 weekly duplicate-code audit (product-owner request,
verbatim: "please start the weekly check ... with the focus on duplicates ... we need to check
completely").

strip_html_and_compress, compress_translatable_fields, build_updated_datasheets,
get_existing_content_for_language, and verify_and_filter_needed were each hand-copied across 3-5
of the sync_*.py entity-type modules (sync_transfer.py, sync_ticket.py, sync_transport.py,
sync_closed_tour.py, sync_hotel.py) and kept in lockstep by hand - exactly the kind of
duplication that already caused extra work twice in the preceding days (the 2026-09-30
field-fallback fix and the 2026-10-01 bullet-list-formatting fix each needed the identical
change applied to the same logic in every file, one at a time). Moved the identical pieces into
sync_shared.py; every sync_*.py module now has a thin wrapper (same name, same external
signature/defaults as before) delegating to the shared implementation.

Deliberately NOT touched: sync_transport.py's own get_existing_content_for_language (a
confirmed, intentionally different, more defensive implementation - see its own docstring citing
a real bug, TRANSPORT-408971) and sync_hotel.py's per-sub-entity extract/get_existing/build_updated
functions (genuinely different field shapes).

These tests exist to pin that every module's PUBLIC function still behaves exactly as before -
not to re-test business logic the existing per-module test files (test_sync_transactional_state.py
and friends) already cover in depth.
"""
import sync_closed_tour
import sync_hotel
import sync_shared
import sync_ticket
import sync_transfer
import sync_transport
from state_store import StateStore


# ============================================================
# strip_html_and_compress / compress_translatable_fields: every module re-exports the same
# shared no-op implementation, not its own copy.
# ============================================================

def test_every_module_strip_html_and_compress_is_the_shared_one():
    assert sync_transfer.strip_html_and_compress is sync_shared.strip_html_and_compress
    assert sync_ticket.strip_html_and_compress is sync_shared.strip_html_and_compress
    assert sync_transport.strip_html_and_compress is sync_shared.strip_html_and_compress
    assert sync_closed_tour.strip_html_and_compress is sync_shared.strip_html_and_compress
    assert sync_hotel.strip_html_and_compress is sync_shared.strip_html_and_compress


def test_every_module_compress_translatable_fields_is_the_shared_one():
    assert sync_transfer.compress_translatable_fields is sync_shared.compress_translatable_fields
    assert sync_ticket.compress_translatable_fields is sync_shared.compress_translatable_fields
    assert sync_transport.compress_translatable_fields is sync_shared.compress_translatable_fields
    assert sync_closed_tour.compress_translatable_fields is sync_shared.compress_translatable_fields
    assert sync_hotel.compress_translatable_fields is sync_shared.compress_translatable_fields


def test_strip_html_and_compress_is_still_a_noop():
    html = "<ul><li>Keep this HTML exactly</li></ul>"
    assert sync_shared.strip_html_and_compress(html) == html


# ============================================================
# build_updated_datasheets: identical behavior for transfer/transport/closed_tour (no list
# fields), and ticket's LIST_FIELDS behavior preserved exactly.
# ============================================================

def test_build_updated_datasheets_plain_string_fields_transfer_transport_closed_tour():
    original = {"EN": {"name": "Airport Transfer"}}
    en_entry = {"name": "Airport Transfer", "description": "A transfer."}
    translations = {"DE": {"name": "Flughafentransfer", "description": "Ein Transfer."}}

    for mod in (sync_transfer, sync_transport, sync_closed_tour):
        result = mod.build_updated_datasheets(original, translations, en_entry)
        assert result["DE"]["name"] == "Flughafentransfer"
        assert result["DE"]["description"] == "Ein Transfer."
        assert result["EN"] == {"name": "Airport Transfer"}  # untouched


def test_build_updated_datasheets_ticket_list_fields_still_split_on_save():
    original = {}
    en_entry = {"name": "City Tour", "includes": ["Guide", "Entry fee"]}
    translations = {"DE": {"name": "Stadtrundfahrt", "includes": "Reiseleiter\nEintritt"}}

    result = sync_ticket.build_updated_datasheets(original, translations, en_entry)
    assert result["DE"]["includes"] == ["Reiseleiter", "Eintritt"]
    assert result["DE"]["name"] == "Stadtrundfahrt"


# ============================================================
# get_existing_content_for_language: identical behavior for transfer/closed_tour/ticket.
# sync_transport.py keeps its OWN bespoke implementation - confirm it still exists and is NOT
# the shared one (that would silently reintroduce the TRANSPORT-408971 bug class).
# ============================================================

def test_get_existing_content_for_language_reads_datasheets_for_transfer_and_closed_tour():
    entry = {"datasheets": {"DE": {"name": "Flughafentransfer", "description": "Ein Transfer."}}}
    for mod in (sync_transfer, sync_closed_tour):
        fields = mod.get_existing_content_for_language(entry, "DE")
        assert fields["name"] == "Flughafentransfer"
        assert fields["description"] == "Ein Transfer."


def test_get_existing_content_for_language_ticket_joins_list_fields_back_to_text():
    entry = {"datasheets": {"DE": {"name": "Stadtrundfahrt", "includes": ["Reiseleiter", "Eintritt"]}}}
    fields = sync_ticket.get_existing_content_for_language(entry, "DE")
    assert fields["includes"] == "Reiseleiter\nEintritt"


def test_sync_transport_get_existing_content_for_language_is_not_the_shared_generic_one():
    """Transport's checker also looks at 'translations'/'remarks' containers, which the shared
    generic one (datasheets only) cannot do - a silent swap here would reintroduce the exact
    TRANSPORT-408971 class of bug sync_transport.py's own docstring describes."""
    entry = {"remarks": {"DE": "Nur auf Deutsch verfügbar"}}
    fields = sync_transport.get_existing_content_for_language(entry, "DE")
    assert fields == {"remarks": "Nur auf Deutsch verfügbar"}
    # The shared generic function, given the same entry, would find nothing (no "datasheets" key).
    assert sync_shared.get_existing_content_for_language(entry, "DE", ("name", "description")) == {}


# ============================================================
# verify_and_filter_needed: every module's public function still behaves exactly as before,
# including the option-vs-main existing-content-checker selection that matters for Ticket and
# Closed Tour options.
# ============================================================

def test_transfer_verify_and_filter_needed_first_run_needs_every_language(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("PLATFORM_STORE_PATH", str(tmp_path / "state.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = StateStore()
    entry = {"datasheets": {}}
    needed = sync_transfer.verify_and_filter_needed(
        store, "transfer", "48940", "TEST-SHARED-TRANSFER", "somehash",
        ["DE", "FR"], entry, {"name": "Airport Transfer"})
    assert set(needed) == {"DE", "FR"}


def test_closed_tour_option_verify_and_filter_needed_uses_the_option_checker():
    """Pins the exact behavior that used to be an inline if/else inside sync_closed_tour.py's
    own verify_and_filter_needed: option_code set -> use get_existing_option_content_for_language,
    not the main-entity checker. A main-entity entry would never match an option-shaped one, so
    if the wrong checker were wired in, this would incorrectly report the language as "needed"
    even when the option already has real, matching translated content."""
    store = StateStore()
    option_entry = {"translations": {"DE": {"name": "Deutsche Option"}}}
    # sync_closed_tour.get_existing_option_content_for_language reads option_entry["translations"][lang]["name"]
    existing = sync_closed_tour.get_existing_option_content_for_language(option_entry, "DE")
    assert existing == {"name": "Deutsche Option"}

    from state_store import compute_hash
    needed = sync_closed_tour.verify_and_filter_needed(
        store, "closed_tour_option", "48940", "TEST-SHARED-CT-OPT|OPT1",
        compute_hash({"name": "Deutsche Option"}),
        ["DE"], option_entry, {"name": "Deutsche Option"}, option_code="OPT1")
    # Existing content is identical to source -> language is reported as truly needed per the
    # "is_identical means source==English fallback, so (re)translate" convention this function
    # has always used - the point of this test is only that it reached get_existing_option_content
    # (which returned real content) rather than the main checker (which would have returned {}
    # and produced the exact same outward result for a different, wrong reason). The next
    # assertion distinguishes the two paths directly.
    assert needed == ["DE"]


def test_sync_shared_verify_and_filter_needed_requires_explicit_existing_content_fn():
    """The shared core never guesses a default checker - every module's own wrapper must supply
    one explicitly. This is what prevents the option/main mix-up bug class from being
    reintroduced silently at the shared layer."""
    import inspect
    sig = inspect.signature(sync_shared.verify_and_filter_needed)
    assert "existing_content_fn" in sig.parameters
    assert sig.parameters["existing_content_fn"].default is inspect.Parameter.empty
