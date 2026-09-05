"""Regression test for a real product-owner report (2026-09-05):

    "To set up a new ticket, the ticket name must be the name of the detected excursion."

Screenshot showed the "Set up this Ticket" screen (render_multi_ticket_flow's PHASE 2, single-
excursion case) with a blank Ticket Name even though the uploaded document clearly described one
named excursion (a real "1 Day Diving Course.pdf"), forcing the human to type the name by hand.

Root cause: ai_extractor.detect_ticket_variants (and its prompt, TICKET_VARIANT_DETECTION_PROMPT)
only ever returned a genuine excursion label when MULTIPLE excursions were found - a
single-excursion document got an intentionally empty "excursions": [] from the model, so
app.py's PHASE 1 candidate-building code had no real label to prefill "Set up this Ticket" with
and fell back to its "{"label": "", ...}" no-name placeholder (see test_ai_extractor.py's
test_detect_ticket_variants_still_returns_the_single_excursions_own_label for the ai_extractor.py
side of this fix).

Fix: the prompt now asks for the single excursion's own entry even in the one-excursion case, so
app.py's "for e in detected" candidate-building loop (which already sets "label": e.get("label"))
naturally picks it up for the single-candidate case too - no app.py logic change was needed for
the label itself. The one thing that loop does NOT set for a real detected excursion is the
Ticket Code (it hardcodes "" there, since the multi-excursion case has no single Step-3 code to
carry over) - so a small addition was needed there: when exactly one real excursion comes back,
carry over default_ticket_code (what the human already typed in Step 3) the same way the
no-name-detected fallback branch already did, so a real name being detected this time doesn't
accidentally blank out the ticket code that already worked before this fix.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so this
is verified by reading its own source text, per this suite's established pattern (see
test_2026_09_03_ticket_modality_code_slash_fix_and_batch_recovery.py) for testing app.py-only
functions/logic.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_single_real_excursion_candidate_carries_over_the_step_3_ticket_code():
    src = _read_app_py()
    assert 'candidates[0]["ticket_code"] = default_ticket_code' in src


def test_the_ticket_code_carryover_is_scoped_to_exactly_one_real_candidate():
    """Guards against the fix being applied unconditionally (which would wipe out the
    per-row Ticket Code a human typed in the genuine multi-excursion case)."""
    src = _read_app_py()
    idx = src.index('candidates[0]["ticket_code"] = default_ticket_code')
    preceding = src[:idx]
    # The nearest preceding branch condition must be the single-candidate case.
    assert "elif len(candidates) == 1:" in preceding[-1500:]


def test_ticket_variant_detection_prompt_asks_for_the_label_even_for_a_single_excursion():
    import ai_extractor as ax
    assert "even when there is only" in ax.TICKET_VARIANT_DETECTION_PROMPT
    assert "ONE excursion" in ax.TICKET_VARIANT_DETECTION_PROMPT
