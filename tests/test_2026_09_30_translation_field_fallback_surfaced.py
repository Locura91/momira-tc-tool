"""Regression tests for a real product report (2026-09-30, verbatim):

    "when translating ClosedTour, we have information of the accommodation. The accommodation
    is not being translated, i guess there is an error in the API." - with a live example
    (momira.travel/de/idea/64800076) showing the English "hotels" (planned accommodation) text
    unchanged on an otherwise-translated German page.

ROOT CAUSE FOUND: sync_closed_tour.py's "hotels" field was always in TEXT_FIELDS (never
excluded) - the bug was one level down, in translator.py. ClaudeTranslator.translate_fields and
GeminiTranslator.translate_fields have always silently substituted the English source for any
ONE field the AI model's response left out or returned empty for a given language - common on a
long, multi-field batch (name/description/included/excluded/hotels/remarks... all in one call)
where the model truncates or skips a field. Before this fix, that was only a print() statement
nobody saw in production; the language still counted as fully translated (correct for every
OTHER field), so the silently-English field was invisible and never flagged. This bug was never
specific to Closed Tours - translator.py is the shared engine behind every entity type.

PRODUCT-OWNER DECISIONS (2026-09-30, via AskUserQuestion):
  1. Surface it as a visible warning only - a language with a per-field fallback still counts
     as translated (not auto-retried); Force re-translate is the manual remedy.
  2. Fix it across every entity type this translator powers (Tickets, Transfers, Transports,
     Hotels, Holiday Packages, Closed Tours), not just Closed Tours.

These tests pin: translate_in_batches now returns (combined, failed_languages,
fallback_fields); every sync_*_from_data function captures it and returns
"fields_fallback_to_english" in its result; translation_tool.py's Summary section surfaces it
as a warning; and a language with a per-field fallback is still recorded as translated (no
auto-retry), per the product owner's explicit choice.
"""
import inspect

import pytest

from state_store import StateStore
import sync_closed_tour
import sync_ticket
import translation_tool as tt
import translator


@pytest.fixture
def store():
    return StateStore()


class FakeAPI:
    def update_closed_tour(self, supplier_id, payload):
        return {"ok": True}

    def update_ticket(self, supplier_id, payload):
        return {"ok": True}


class FieldDroppingTranslator:
    """Simulates the real bug: every field translates except ONE, for ONE language, which the
    model's response simply leaves out - exactly what translate_fields' own gap-filling used to
    paper over silently."""

    def __init__(self, dropped_field, dropped_lang):
        self.dropped_field = dropped_field
        self.dropped_lang = dropped_lang

    def translate_fields(self, source_fields, target_languages, retries=5):
        result = {}
        fallback_fields = {}
        for lang in target_languages:
            lang_result = {}
            for field, value in source_fields.items():
                if lang == self.dropped_lang and field == self.dropped_field:
                    # The model "forgot" this field for this language - falls back to English,
                    # exactly as ClaudeTranslator/GeminiTranslator do for real.
                    lang_result[field] = value
                    fallback_fields.setdefault(lang, []).append(field)
                else:
                    lang_result[field] = f"[{lang}] {value}"
            result[lang] = lang_result
        return result, fallback_fields


def closed_tour_entry(code="TEST-TOUR-FALLBACK"):
    return {"code": code, "active": True, "datasheets": {"EN": {
        "name": "Bush Camp Safari",
        "description": "A multi-day safari.",
        "hotels": "African-Style Luxury Tents: 20 upscale canvas tents.",
    }}}


# ============================================================
# translator.translate_in_batches: the shared plumbing
# ============================================================

def test_translate_in_batches_returns_a_three_tuple_including_fallback_fields():
    tr = FieldDroppingTranslator(dropped_field="hotels", dropped_lang="DE")
    combined, failed_languages, fallback_fields = translator.translate_in_batches(
        tr, {"name": "Safari", "hotels": "Tents."}, ["DE", "FR"], batch_size=10
    )
    assert failed_languages == set()
    assert fallback_fields == {"DE": ["hotels"]}
    # The field itself is still present in the result (as the English source) - not dropped
    # from the payload, just not translated.
    assert combined["DE"]["hotels"] == "Tents."
    assert combined["FR"]["hotels"] != "Tents."


def test_translate_in_batches_merges_fallback_fields_across_concurrent_batches():
    tr = FieldDroppingTranslator(dropped_field="hotels", dropped_lang="DE")
    # batch_size=1 forces DE and FR into separate concurrent batches - the real shape a Closed
    # Tour translation run uses (DATASHEET_BATCH_SIZE=1).
    combined, failed_languages, fallback_fields = translator.translate_in_batches(
        tr, {"name": "Safari", "hotels": "Tents."}, ["DE", "FR"], batch_size=1
    )
    assert fallback_fields == {"DE": ["hotels"]}


def test_single_batch_whole_failure_does_not_also_report_a_fallback():
    class AlwaysRaises:
        def translate_fields(self, source_fields, target_languages, retries=5):
            raise RuntimeError("simulated provider outage")

    combined, failed_languages, fallback_fields = translator.translate_in_batches(
        AlwaysRaises(), {"name": "Safari"}, ["DE"], batch_size=10
    )
    assert failed_languages == {"DE"}
    # A whole-batch failure is already reported via failed_languages - no need to double-report
    # every field of that language as a "fallback" too.
    assert fallback_fields == {}


# ============================================================
# sync_closed_tour.py: the exact reported scenario
# ============================================================

def test_closed_tour_hotels_field_falling_back_is_surfaced_in_the_result(store):
    """Pins the exact real report: translating a Closed Tour whose accommodation ("hotels")
    field the model dropped for German must show up in the result, not just a server log."""
    api = FakeAPI()
    translator_fake = FieldDroppingTranslator(dropped_field="hotels", dropped_lang="DE")
    entry = closed_tour_entry()

    result = sync_closed_tour.sync_closed_tour_from_data(
        api, translator_fake, store, "48940", entry, ["DE", "FR"], "TEST-TOUR-FALLBACK", dry_run=False)

    assert result["status"] == "updated"
    assert result["fields_fallback_to_english"] == {"DE": ["hotels"]}


def test_closed_tour_fallback_language_still_counts_as_translated_not_auto_retried(store):
    """CONFIRMED PRODUCT-OWNER DECISION (2026-09-30): "just show a warning, no auto-retry" - a
    language with a per-field fallback is still recorded as fully translated in the state
    store, exactly like before this fix. Force re-translate remains the manual remedy."""
    api = FakeAPI()
    translator_fake = FieldDroppingTranslator(dropped_field="hotels", dropped_lang="DE")
    # Own code, distinct from the other test above - state is a shared sqlite file for the
    # whole test session (see conftest.py's PLATFORM_STORE_PATH), so reusing a code would let
    # that test's already-translated DE/FR state make this run look "up_to_date" instead of
    # actually exercising the write path this test is pinning.
    entry = closed_tour_entry(code="TEST-TOUR-FALLBACK-RETRY")

    result = sync_closed_tour.sync_closed_tour_from_data(
        api, translator_fake, store, "48940", entry, ["DE", "FR"], "TEST-TOUR-FALLBACK-RETRY", dry_run=False)

    assert "DE" in result["languages_written"]
    state = store.get_state("closed_tour", "48940", "TEST-TOUR-FALLBACK-RETRY")
    assert "DE" in state["translated_languages"]


def test_closed_tour_result_has_no_fallback_entry_when_nothing_fell_back(store):
    class PerfectTranslator:
        def translate_fields(self, source_fields, target_languages, retries=5):
            return {lang: {f: f"[{lang}] {v}" for f, v in source_fields.items()}
                    for lang in target_languages}, {}

    api = FakeAPI()
    entry = closed_tour_entry(code="TEST-TOUR-PERFECT")

    result = sync_closed_tour.sync_closed_tour_from_data(
        api, PerfectTranslator(), store, "48940", entry, ["DE", "FR"], "TEST-TOUR-PERFECT", dry_run=False)

    assert result["fields_fallback_to_english"] == {}


def test_fallback_surfaced_for_ticket_too_not_just_closed_tours():
    """CONFIRMED PRODUCT-OWNER DECISION (2026-09-30): fix applies across every entity type this
    translator powers, not only Closed Tours - the same result key must exist there too."""
    src = inspect.getsource(sync_ticket.sync_ticket_from_data)
    assert "fields_fallback_to_english" in src
    assert "fallback_fields" in src


@pytest.mark.parametrize("module_name, func_name", [
    ("sync_closed_tour", "sync_closed_tour_from_data"),
    ("sync_closed_tour", "sync_closed_tour_option_from_data"),
    ("sync_ticket", "sync_ticket_from_data"),
    ("sync_ticket", "sync_ticket_option_from_data"),
    ("sync_transfer", "sync_transfer_from_data"),
    ("sync_transport", "sync_transport_from_data"),
    ("sync_transport", "sync_transport_option_from_data"),
    ("sync_hotel", "sync_hotel_main"),
    ("sync_hotel", "sync_room"),
    ("sync_hotel", "sync_supplement"),
    ("sync_hotel", "sync_offer"),
    ("sync_holiday_package", "sync_one_package_entry"),
])
def test_every_entity_types_sync_function_captures_and_returns_fallback_fields(module_name, func_name):
    import importlib
    mod = importlib.import_module(module_name)
    func = getattr(mod, func_name, None)
    if func is None:
        pytest.skip(f"{module_name}.{func_name} does not exist")
    src = inspect.getsource(func)
    assert "translate_in_batches(" in src, f"{module_name}.{func_name} doesn't call translate_in_batches"
    assert "fallback_fields" in src, f"{module_name}.{func_name} doesn't capture translate_in_batches' 3rd return value"
    assert "fields_fallback_to_english" in src, f"{module_name}.{func_name} doesn't surface it in its result"


# ============================================================
# translation_tool.py: visible in the app, not just a server log
# ============================================================

def test_summary_section_surfaces_fallback_warnings():
    src = inspect.getsource(tt.render_translation_tool)
    assert "fields_fallback_to_english" in src
    assert "fallback_warnings" in src
    # 2026-10-01: generalized to also cover bullet-list-formatting mismatches, not just
    # missing values - see tests/test_2026_10_01_bullet_list_formatting_mismatch_flagged.py.
    assert 'st.expander("⚠️ Fields flagged for review' in src


def test_summary_warning_mentions_force_retranslate_as_the_manual_remedy():
    """Since this is a visible-warning-only fix (no auto-retry, per product-owner decision), the
    warning text itself must tell the human what to do about it."""
    src = inspect.getsource(tt.render_translation_tool)
    warning_block = src[src.index("if fallback_warnings:"):src.index("with st.expander(\"Full result\")")]
    assert "Force re-translate" in warning_block
