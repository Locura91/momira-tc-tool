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

Fix: every modality_supplements entry now carries is_priced_choice (bool). build_ticket_payloads
excludes is_priced_choice=True rows from what publishes on the Modality (build_ticket_supplement_vos
never even sees them) and reports their names back as excluded_language_choice_extras, instead of
silently stacking or silently dropping them - same "never silent" pattern as the pre-existing
ignored_ticket_supplements field.

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
# build_ticket_payloads: priced-choice supplements excluded from the published Modality
# ---------------------------------------------------------------------------

def test_priced_choice_supplement_is_excluded_from_the_published_modality(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    names = [s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]]
    assert "French-speaking guide" not in names
    assert result["excluded_language_choice_extras"] == ["French-speaking guide"]


def test_dated_non_choice_supplement_still_publishes_normally(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "Holiday Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2025-12-24", "end_date": "2026-01-07",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    names = [s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]]
    assert "Holiday Season Surcharge" in names
    assert result["excluded_language_choice_extras"] == []


def test_a_mix_of_choice_and_dated_rows_splits_correctly(fake_api_client):
    """The exact real scenario from the report: several language-guide rows (choice) alongside a
    Holiday Season Surcharge (dated) on the same Modality."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "Italian-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "Spanish-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "Russian-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "Holiday Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2025-12-24", "end_date": "2026-01-07",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    published_names = {s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]}
    assert published_names == {"Holiday Season Surcharge"}
    assert set(result["excluded_language_choice_extras"]) == {
        "French-speaking guide", "Italian-speaking guide", "Spanish-speaking guide", "Russian-speaking guide"}


def test_missing_is_priced_choice_key_defaults_to_publishing_as_dated(fake_api_client):
    """Backward compatible with older extracted/saved data that predates this field - absence
    means false (a dated change), not an accidental exclusion."""
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "High Season", "adult_price_supplement": 10, "children_price_supplement": 10,
         "infant_price_supplement": 0},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    names = [s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]]
    assert "High Season" in names
    assert result["excluded_language_choice_extras"] == []


def test_build_ticket_supplement_vos_itself_is_unchanged_and_unaware_of_is_priced_choice():
    """The filter lives at the build_ticket_payloads call site, not inside build_ticket_supplement_vos
    - existing direct callers/tests of that function are unaffected by this change."""
    result = builder.build_ticket_supplement_vos([
        {"name": "German guide", "adult_price_supplement": 10, "children_price_supplement": 10,
         "infant_price_supplement": 0, "is_priced_choice": True},
    ], "2026-01-01", "2026-12-31")
    assert len(result) == 1
    assert result[0].translations["EN"].name == "German guide"


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
