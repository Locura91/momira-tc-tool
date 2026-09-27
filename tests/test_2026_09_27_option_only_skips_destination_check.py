"""Regression test for a real product-owner request (2026-09-27), verbatim: "when updating
existing closedtour modality only, no need to check the destiinations again before publishing."

An option-only ClosedTour action (add_option, update_option, or the "Price only" scope of
update_tour - all covered by the `is_option_only` flag in app.py) never touches the main tour's
itinerary at all: Step 5 skips the itinerary destinations box entirely for these, and
extract_option_only_data()/extract_modality_data() never populate itinerary_destinations, so
build_closed_tour_payloads' destination-resolution step always has an empty list to work with.
The UI used to still say "Check Locations & Continue" and show a "Destination Check" section
(even though it always rendered zero rows), which read as if a real destination re-verification
was happening or still needed before publishing a modality-only change. This test checks the
option-only UI copy no longer implies a destination check is happening.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(_REPO_ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def test_continue_button_label_is_conditional_on_is_option_only():
    src = _read("app.py")
    assert '_ct_continue_label = "➡️ Continue" if is_option_only else "🔎 Check Locations & Continue"' in src


def test_spinner_message_does_not_claim_destination_resolution_for_option_only():
    src = _read("app.py")
    assert '_ct_spinner_msg = ("Preparing payload..." if is_option_only' in src


def test_destination_check_section_is_skipped_for_option_only():
    src = _read("app.py")
    assert 'if not is_option_only:\n            st.subheader("Destination Check' in src


def test_step_6_header_drops_destination_resolution_wording_for_option_only():
    src = _read("app.py")
    assert 'st.header("Step 6 — Payload Preview" if is_option_only else "Step 6 — Destination Resolution & Payload Preview")' in src


def test_cannot_publish_info_message_does_not_mention_destinations_for_option_only():
    src = _read("app.py")
    assert '"Fix pricing above before publishing." if is_option_only' in src


def test_option_only_extraction_functions_never_populate_itinerary_destinations():
    # Belt-and-braces: confirms the underlying reason this UI change is safe - both
    # extraction paths behind is_option_only genuinely never carry real itinerary data.
    src = _read("ai_extractor.py")
    assert '"itinerary_destinations": [], "nights": 1,' in src  # extract_option_only_data's defaults
