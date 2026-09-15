"""Regression tests for a real incident (product-owner report, 2026-08-25): "different languages
are always a problem within creating a ticket. Travel C logic would add every single language up
and the price would be too high and absolutely wrong. If a ticket has other language options
apart from the base modality, we must ignore it for the base modality - other languages must have
other modalities and... every ticket creation can have at first only one modality."

The 2026-08-24 change ("all extra costs are Supplement by dates, no need to distinguish") went too
far: it merged a genuinely DATED price change (a season, a holiday surcharge - correctly a
supplement on the SAME modality) with a priced CHOICE the customer picks between (a foreign-
language guide, a Seat-in-Coach/vehicle upgrade - a different product, not a date-based change) into
one undifferentiated list. Publishing a priced-choice row as a supplement let Travel Compositor
stack its price onto the base modality as if it were just another date-window extra.

The 2026-08-25 fix added an is_priced_choice (bool) flag - the "Needs own Modality?" checkbox -
so build_ticket_payloads could exclude those rows from what publishes and report their names back
as excluded_language_choice_extras.

RETIRED (product owner, 2026-09-15): "When Ticket creation and Supplement says: Needs own Modality,
we can ignore that information - we want to make the app simple and handy for humans in the
future." is_priced_choice is no longer read anywhere - every modality_supplements row publishes
onto the Modality, whatever kind of extra it is (see test_2026_09_15_needs_own_modality_retired.py
for the exclusion-removed coverage). The same_price_language_includes_line tests below are
unaffected - that is a different, still-active feature (same-price language options, not a priced
choice extra).

Second, related request: when a Modality's languages field carries 2+ same-price languages, Includes
gets one deterministic line - "You can choose between X-speaking Guide or Y-speaking Guide" - built
from builder.same_price_language_includes_line, the same LANGUAGE_CODE_NAMES app.py's Language
Options multiselect now imports rather than keeping its own duplicate copy.
"""
import builder
from builder import same_price_language_includes_line, LANGUAGE_CODE_NAMES
from test_builder_ticket import make_pre_config, minimal_ticket_data


# ---------------------------------------------------------------------------
# same_price_language_includes_line
# ---------------------------------------------------------------------------

def test_two_languages_produces_the_exact_confirmed_wording():
    assert same_price_language_includes_line(["EN", "DE"]) == (
        "You can choose between English-speaking Guide or German-speaking Guide")


def test_three_languages_uses_a_comma_list_with_a_trailing_or():
    assert same_price_language_includes_line(["EN", "DE", "FR"]) == (
        "You can choose between English-speaking Guide, German-speaking Guide or French-speaking Guide")


def test_single_language_produces_no_line():
    assert same_price_language_includes_line(["EN"]) is None


def test_empty_or_missing_languages_produces_no_line():
    assert same_price_language_includes_line([]) is None
    assert same_price_language_includes_line(None) is None


def test_unknown_code_falls_back_to_the_code_itself():
    assert same_price_language_includes_line(["EN", "ZZ"]) == (
        "You can choose between English-speaking Guide or ZZ-speaking Guide")


def test_language_code_names_has_every_ticket_language():
    for code in ["EN", "FR", "SL", "PL", "DE", "SK", "HU", "NL", "ES", "TR",
                 "RU", "NO", "SV", "RO", "CS", "EL", "FI", "PT", "DA", "IT"]:
        assert code in LANGUAGE_CODE_NAMES


# ---------------------------------------------------------------------------
# build_ticket_payloads: the Includes line for same-price languages
# ---------------------------------------------------------------------------

def test_two_same_price_languages_add_the_includes_line(fake_api_client):
    data = minimal_ticket_data(languages=["EN", "DE"])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert "You can choose between English-speaking Guide or German-speaking Guide" in \
        result["main_ticket_payload"]["datasheets"]["EN"]["includes"]


def test_single_language_adds_no_includes_line(fake_api_client):
    data = minimal_ticket_data(languages=["EN"])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert not any("choose between" in i.lower()
                  for i in result["main_ticket_payload"]["datasheets"]["EN"]["includes"])


def test_existing_includes_are_preserved_alongside_the_new_line(fake_api_client):
    data = minimal_ticket_data(languages=["EN", "DE"], includes=["Official Voucher", "Handling Fee"])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    includes = result["main_ticket_payload"]["datasheets"]["EN"]["includes"]
    assert "Official Voucher" in includes
    assert "Handling Fee" in includes
    assert "You can choose between English-speaking Guide or German-speaking Guide" in includes


def test_an_existing_choose_between_line_is_not_duplicated(fake_api_client):
    data = minimal_ticket_data(languages=["EN", "DE"],
                               includes=["You can already choose between English or German guiding"])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    includes = result["main_ticket_payload"]["datasheets"]["EN"]["includes"]
    assert sum("choose between" in i.lower() for i in includes) == 1
