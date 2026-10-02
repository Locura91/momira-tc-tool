"""Regression test for the 2026-10-02 weekly duplicate-code audit's constants pass:
DEFAULT_TARGET_LANGUAGES ("reduced from 30 to 19 target languages" product-owner decision) used
to be defined independently, byte-identically, in translation_tool.py and run_sync_tickets.py.
Moved to sync_shared.py (already a dependency of both, via sync_ticket.py); both modules now
import the same list instead of each keeping their own hand-copied one."""
import run_sync_tickets
import sync_shared
import translation_tool


def test_translation_tool_and_run_sync_tickets_share_the_same_language_list_object():
    assert translation_tool.DEFAULT_TARGET_LANGUAGES is sync_shared.DEFAULT_TARGET_LANGUAGES
    assert run_sync_tickets.DEFAULT_TARGET_LANGUAGES is sync_shared.DEFAULT_TARGET_LANGUAGES


def test_default_target_languages_is_still_the_19_language_list():
    assert sync_shared.DEFAULT_TARGET_LANGUAGES == [
        "FR", "SL", "PL", "DE", "SK", "HU", "NL", "ES", "TR",
        "RU", "NO", "SV", "RO", "CS", "EL", "FI",
        "PT", "DA", "IT",
    ]
