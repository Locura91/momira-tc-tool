"""Regression test for a real production failure (2026-09-08):

    Retrying RAK-T1's option (Ticket) failed with:

        code:Size must be between 1 and 50 (Day Trip from Marrakech to Atlas Mountains
        (Three Valleys: Ait Mizan, Sidi Fares, Ourika))

Root cause: Travel Compositor's Modality/Option 'code' field is capped at 50 characters
server-side, but nothing on this app's side enforced that before sending it. When a document
carries no separate short reference code, the full descriptive title ends up AS the Modality
Code (see HumanPreConfig.modality_code / TicketHumanPreConfig.modality_code docstrings) - here
an 89-character title - and sailed through every review step untouched, only failing at the
live API call.

Fix: both HumanPreConfig.modality_code (ClosedTour) and TicketHumanPreConfig.modality_code
(Ticket) now truncate to MODALITY_CODE_MAX_LENGTH (50, Travel Compositor's confirmed real limit)
instead of raising - same "sanitize instead of hard-reject" philosophy the product owner already
confirmed for the sibling slash-stripping case (2026-09-03). A root_validator captures the
ORIGINAL, untruncated text for modality_name's default-to-code fallback BEFORE the code field
itself is truncated, so the client-facing name (which builder.py falls back to modality_code for
when modality_name isn't separately given - see build_ticket_payloads/build_closed_tour_payloads)
never loses text a human never saw truncated.
"""
from schemas import HumanPreConfig, TicketHumanPreConfig, MODALITY_CODE_MAX_LENGTH

LONG_TITLE = "Day Trip from Marrakech to Atlas Mountains (Three Valleys: Ait Mizan, Sidi Fares, Ourika)"
assert len(LONG_TITLE) > MODALITY_CODE_MAX_LENGTH  # the test fixture must actually exercise the limit


def _ticket_config(**overrides):
    kwargs = dict(supplier_id="1", ticket_code="RAK-T1", currency="EUR",
                  modality_code=LONG_TITLE, modality_name=None)
    kwargs.update(overrides)
    return TicketHumanPreConfig(**kwargs)


def _closed_tour_config(**overrides):
    kwargs = dict(supplier_id="1", provider_code="RAK-2", min_pax=1, max_pax=9, currency="EUR",
                  modality_code=LONG_TITLE)
    kwargs.update(overrides)
    return HumanPreConfig(**kwargs)


def test_ticket_modality_code_is_truncated_to_the_real_travel_compositor_limit():
    cfg = _ticket_config()
    assert len(cfg.modality_code) == MODALITY_CODE_MAX_LENGTH
    assert cfg.modality_code == LONG_TITLE[:MODALITY_CODE_MAX_LENGTH]


def test_closed_tour_modality_code_is_truncated_the_same_way():
    cfg = _closed_tour_config()
    assert len(cfg.modality_code) == MODALITY_CODE_MAX_LENGTH
    assert cfg.modality_code == LONG_TITLE[:MODALITY_CODE_MAX_LENGTH]


def test_ticket_modality_name_keeps_the_full_untruncated_text_when_defaulted():
    cfg = _ticket_config()
    assert cfg.modality_name == LONG_TITLE
    assert len(cfg.modality_name) > MODALITY_CODE_MAX_LENGTH


def test_closed_tour_modality_name_keeps_the_full_untruncated_text_when_defaulted():
    cfg = _closed_tour_config()
    assert cfg.modality_name == LONG_TITLE
    assert len(cfg.modality_name) > MODALITY_CODE_MAX_LENGTH


def test_an_explicitly_given_modality_name_is_never_overridden_by_the_fallback():
    cfg = _ticket_config(modality_name="My Explicit Client-Facing Name")
    assert cfg.modality_name == "My Explicit Client-Facing Name"

    cfg2 = _closed_tour_config(modality_name="My Explicit Client-Facing Name")
    assert cfg2.modality_name == "My Explicit Client-Facing Name"


def test_short_modality_code_is_left_completely_unchanged():
    cfg = _ticket_config(modality_code="Standard")
    assert cfg.modality_code == "Standard"
    assert cfg.modality_name == "Standard"  # still defaults, just not truncated (nothing to truncate)


def test_slashes_are_still_stripped_alongside_the_new_length_cap():
    cfg = _ticket_config(modality_code="Turtles/Tortoises: Three Island Cruise (Praslin)")
    assert "/" not in cfg.modality_code
    assert len(cfg.modality_code) <= MODALITY_CODE_MAX_LENGTH

    cfg2 = _closed_tour_config(modality_code="Turtles/Tortoises: Three Island Cruise (Praslin)")
    assert "/" not in cfg2.modality_code
