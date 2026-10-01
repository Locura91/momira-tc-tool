"""Regression tests for a real product report (2026-10-01, verbatim):

    "When Translating transfers and in the description of the origen is added a bullet
    point, we shall also add a bullet point to the other languages. This is not done all
    the time, here is an error"

ROOT CAUSE: translator.py's gap-filling logic (added 2026-09-30 for the Closed Tour
accommodation-fallback bug, now factored into the shared _fill_translation_gaps helper both
ClaudeTranslator.translate_fields and GeminiTranslator.translate_fields call) only ever checked
whether a field's translation came back PRESENT and non-empty. A translation that came back
present and non-empty - but with its <ul><li> bullet list silently collapsed into a plain
paragraph - passed that check every time, so nothing ever flagged it. This is the other half of
the exact same class of bug: a field can be "there" without actually matching the source's
structure.

FIX: translator._list_marker_count() counts bullet-list markers (HTML <li> elements, or plain
"- "/"* " lines) in a field's text. _fill_translation_gaps now compares the source's marker
count against the translation's marker count for every field that DID come back non-empty; a
mismatch is flagged into the same fallback_fields dict the 2026-09-30 fix introduced (surfaced
the same way in translation_tool.py's Summary warning - no separate code path, no separate UI
section), but - unlike the missing-value case - the AI's actual translated text is KEPT, since
there's a real translation there, just one that needs a human look.

Also strengthened SYSTEM_PROMPT's "Formatting integrity" rule to explicitly call out list
structure, so this should also happen less often at the source, not just get caught after the
fact.

SAME PRODUCT-OWNER POLICY, applied without re-asking (directly analogous to the 2026-09-30
decision made for the sibling bug, which the product owner explicitly chose for the "all entity
types" scope and "warning only, no auto-retry" behavior): visible warning only, no auto-retry,
Force re-translate is the manual remedy. This bug lives in the exact same shared function, so
every entity type (Tickets, Transfers, Transports, Hotels, Holiday Packages, Closed Tours) gets
this fix automatically - nothing per-entity-type to change.
"""
import inspect

import translation_tool as tt
from translator import _fill_translation_gaps, _list_marker_count, translate_in_batches


# ============================================================
# _list_marker_count: the raw counting helper
# ============================================================

def test_counts_html_li_tags_case_insensitively():
    assert _list_marker_count("<ul><li>A</li><li>B</li></ul>") == 2
    assert _list_marker_count("<UL><LI>A</LI></UL>") == 1


def test_counts_plain_dash_and_star_bullet_lines():
    assert _list_marker_count("- First\n- Second\n- Third") == 3
    assert _list_marker_count("* First\n* Second") == 2


def test_no_markers_in_plain_prose_counts_zero():
    assert _list_marker_count("Just an ordinary sentence, no lists here.") == 0


def test_non_string_input_counts_zero():
    assert _list_marker_count(None) == 0
    assert _list_marker_count(123) == 0


# ============================================================
# _fill_translation_gaps: the real detection logic both translators call
# ============================================================

def test_flags_a_field_whose_bullet_list_was_dropped():
    source = {
        "name": "Airport Transfer",
        "pickupInformation": "<ul><li>Hotel Lobby, Origin A</li><li>Reception, Origin B</li></ul>",
    }
    non_empty = dict(source)
    raw = {
        "DE": {
            "name": "[DE] Airport Transfer",
            # The bullet list collapsed into plain prose - present, non-empty, but the <li>
            # markers are gone. This is exactly what the old check could never catch.
            "pickupInformation": "[DE] Pickup is available from several hotels in the area.",
        },
        "FR": {
            "name": "[FR] Airport Transfer",
            "pickupInformation": "[FR] <li>Hotel Lobby, Origin A</li><li>Reception, Origin B</li>",
        },
    }
    result, fallback_fields = _fill_translation_gaps(source, non_empty, raw, ["DE", "FR"])

    assert fallback_fields == {"DE": ["pickupInformation"]}
    # The AI's real translated text is kept - NOT silently overwritten with the English source,
    # unlike the missing-value fallback case.
    assert result["DE"]["pickupInformation"] == "[DE] Pickup is available from several hotels in the area."
    assert "<li>" not in result["DE"]["pickupInformation"].lower()
    # FR's list was preserved, so it must not be flagged.
    assert "FR" not in fallback_fields


def test_matching_bullet_count_is_not_flagged():
    source = {"pickupInformation": "<ul><li>Point A</li><li>Point B</li></ul>"}
    raw = {"DE": {"pickupInformation": "<ul><li>Punkt A</li><li>Punkt B</li></ul>"}}
    result, fallback_fields = _fill_translation_gaps(source, source, raw, ["DE"])
    assert fallback_fields == {}


def test_plain_dash_bullet_mismatch_is_also_caught():
    source = {"description": "- Hotel lobby\n- Main entrance"}
    raw = {"DE": {"description": "Available from the hotel lobby or the main entrance."}}
    result, fallback_fields = _fill_translation_gaps(source, source, raw, ["DE"])
    assert fallback_fields == {"DE": ["description"]}


def test_a_field_with_no_bullets_in_the_source_is_never_flagged_for_formatting():
    """A field that never had a list to begin with must not be flagged just because the
    translation also has none - only a genuine mismatch (source has N, translation has a
    different count) is a problem."""
    source = {"name": "Airport Transfer - Private Car"}
    raw = {"DE": {"name": "[DE] Flughafentransfer - Privatwagen"}}
    result, fallback_fields = _fill_translation_gaps(source, source, raw, ["DE"])
    assert fallback_fields == {}


def test_missing_value_fallback_and_formatting_mismatch_both_use_the_same_dict_shape():
    """2026-10-01 is additive to the 2026-09-30 fix, not a parallel mechanism - sync_*.py
    callers, and translation_tool.py's Summary UI, need only ONE dict to watch."""
    source = {"name": "Airport Transfer", "pickupInformation": "<li>A</li><li>B</li>"}
    raw = {
        "DE": {"name": "", "pickupInformation": "Flat text, no list."},
        "FR": {"name": "[FR] Airport Transfer", "pickupInformation": "[FR] <li>A</li><li>B</li>"},
    }
    result, fallback_fields = _fill_translation_gaps(source, source, raw, ["DE", "FR"])
    assert fallback_fields["DE"] == ["name", "pickupInformation"]
    assert "FR" not in fallback_fields
    # The missing "name" still falls back to the English source, same as before.
    assert result["DE"]["name"] == "Airport Transfer"


# ============================================================
# translate_in_batches: confirms the real translator classes' fix reaches every caller, via
# a fake that exercises _fill_translation_gaps exactly the way ClaudeTranslator/GeminiTranslator
# do (raw provider response in, gaps-filled-and-flagged result out).
# ============================================================

class RawResponseTranslator:
    """Stands in for ClaudeTranslator/GeminiTranslator: takes a pre-baked raw provider
    response and runs it through the SAME _fill_translation_gaps helper the real classes now
    call, instead of re-implementing (and risking drifting from) the detection logic."""

    def __init__(self, raw_translations):
        self.raw_translations = raw_translations

    def translate_fields(self, source_fields, target_languages, retries=5):
        non_empty = {k: v for k, v in source_fields.items() if isinstance(v, str) and v.strip()}
        return _fill_translation_gaps(source_fields, non_empty, self.raw_translations, target_languages)


def test_translate_in_batches_surfaces_the_bullet_mismatch_for_a_real_translator_shape():
    source = {"pickupInformation": "<ul><li>Hotel Lobby</li><li>Main Entrance</li></ul>"}
    raw = {"DE": {"pickupInformation": "Abholung am Hotel oder am Haupteingang."}}
    tr = RawResponseTranslator(raw)

    combined, failed_languages, fallback_fields = translate_in_batches(tr, source, ["DE"], batch_size=10)

    assert failed_languages == set()
    assert fallback_fields == {"DE": ["pickupInformation"]}
    assert combined["DE"]["pickupInformation"] == "Abholung am Hotel oder am Haupteingang."


# ============================================================
# translation_tool.py: the Summary UI covers both causes in one place
# ============================================================

def test_summary_warning_text_mentions_both_missing_values_and_list_formatting():
    src = inspect.getsource(tt.render_translation_tool)
    warning_block = src[src.index("if fallback_warnings:"):src.index("with st.expander(\"Full result\")")]
    assert "English" in warning_block
    assert "bullet" in warning_block.lower()
    assert "Force re-translate" in warning_block
