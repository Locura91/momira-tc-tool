"""HumanPreConfig.provider_code (the ClosedTour "Tour Code") no longer enforces a rigid shape.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): a real Tour Code, "Rak-2", was rejected by
publish-preview with `providerCode must strictly follow the format 'XXX-Number' (e.g., ASW-1)` -
"is not needed, it is just a style and the app must still be able to publish this tour."

The old validator (`schemas.py`) required exactly 3 UPPERCASE letters, a dash, then digits
(`^[A-Z]{3}-\\d+$`) - "Rak-2" failed only because "Rak" isn't all-uppercase, a cosmetic
difference with no bearing on whether Travel Compositor can accept the code. No other product's
own code field enforces anything like this shape (TicketHumanPreConfig.ticket_code,
TransferHumanPreConfig/TransportHumanPreConfig's equivalents, HotelHumanPreConfig.provider_code
are all free strings) - this was a ClosedTour-only leftover restriction, not a genuine API
requirement.

Still guarded: the code is embedded directly into a URL path
(api_client.create_closed_tour_option), so '/' or '\\' would genuinely break that lookup - same
reasoning TicketHumanPreConfig.modality_code's own "no_slash_in_modality_code" validator already
uses for the same reason.

UPDATE (2026-09-03): a '/' or '\\' is now silently stripped rather than hard-rejected - see
test_2026_09_03_ticket_modality_code_slash_fix_and_batch_recovery.py for the full story (the
sibling Ticket Modality Code validator hit the exact same real-world failure from a human-typed
value the UI's own sanitization hadn't covered, and hard-rejecting forced a manual re-type
instead of just fixing it automatically).
"""
import pytest
from pydantic import ValidationError

from schemas import HumanPreConfig


def _config(provider_code):
    return HumanPreConfig(
        supplier_id="48940", provider_code=provider_code, min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )


def test_a_mixed_case_tour_code_is_now_accepted():
    """The exact real-world code from the product-owner's report."""
    cfg = _config("Rak-2")
    assert cfg.provider_code == "Rak-2"


def test_a_lowercase_tour_code_is_accepted():
    cfg = _config("bkk-1")
    assert cfg.provider_code == "bkk-1"


def test_a_tour_code_with_more_than_three_letters_is_accepted():
    cfg = _config("MARRAKECH-2")
    assert cfg.provider_code == "MARRAKECH-2"


def test_a_tour_code_with_no_dash_at_all_is_accepted():
    cfg = _config("RAK2")
    assert cfg.provider_code == "RAK2"


def test_the_classic_xxx_dash_number_shape_still_works_too():
    cfg = _config("ASW-1")
    assert cfg.provider_code == "ASW-1"


def test_a_blank_tour_code_is_still_rejected():
    with pytest.raises(ValidationError, match="cannot be blank"):
        _config("")


def test_a_tour_code_with_a_forward_slash_has_the_slash_silently_stripped():
    """It becomes part of a URL path (create_closed_tour_option) - a slash would break the
    lookup, same reasoning modality_code's own no-slash rule uses. Sanitized (2026-09-03), not
    hard-rejected - see this file's module docstring UPDATE note."""
    cfg = _config("RAK/2")
    assert cfg.provider_code == "RAK2"


def test_a_tour_code_with_a_backslash_has_the_backslash_silently_stripped():
    cfg = _config("RAK\\2")
    assert cfg.provider_code == "RAK2"


def test_a_tour_code_that_is_only_slashes_is_still_rejected_as_blank():
    with pytest.raises(ValidationError, match="cannot be blank"):
        _config("//")
