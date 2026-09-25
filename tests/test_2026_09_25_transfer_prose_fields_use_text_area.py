"""Regression test for an audit finding (2026-09-25): the same class of bug as the ClosedTour
Hotels widget-type fix (2026-09-24), found by a proactive audit rather than a fresh product-owner
report.

flows/multi_transfer.py wired up "location_notes", "description", "pickup_information" and
"cancellation_policy_text" with NO widget= argument at all, which defaults to editable_field's
single-line "text_input" (see ui_components.editable_field's own default). All four fields are
documented in ai_extractor.py's own TRANSFER_EXTRACTION_SYSTEM_PROMPT as multi-sentence prose:
"1-2 short plain-English sentences" (description), "informational text about location-conditional
costs" (location_notes), "any specific pickup logistics/instructions" (pickup_information) - and
cancellation_policy_text is the exact same field key Transport's own multi_transport.py already
correctly wires to widget="text_area". A human editing a Transfer's Description or Pickup
information saw a cramped single-line box for text that can run several sentences, while the
identical fields on Transport got a proper multi-line editor.

Fixed by adding widget="text_area" (matching Transport's own heights) to all four call sites.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_transfer_prose_fields_now_use_text_area_widget():
    src = _read("flows", "multi_transfer.py")
    for field_key in ("location_notes", "description", "pickup_information", "cancellation_policy_text"):
        marker = f'"{field_key}"'
        assert marker in src, f"expected an editable_field call for {field_key!r}"
        idx = src.index(marker)
        window = src[idx:idx + 200]
        assert 'widget="text_area"' in window, (
            f"{field_key!r} should be wired to widget=\"text_area\" (it stores multi-sentence "
            f"prose, per ai_extractor.py's own TRANSFER_EXTRACTION_SYSTEM_PROMPT) - found no "
            f"widget=\"text_area\" within 200 chars of its editable_field call")


def test_transfer_prose_fields_match_transport_widget_family():
    """Cross-check against Transport's own equivalent fields, which were never buggy - both
    products should agree on the widget family for the fields they share a key with."""
    transfer_src = _read("flows", "multi_transfer.py")
    transport_src = _read("flows", "multi_transport.py")
    for field_key in ("description", "cancellation_policy_text"):
        for src, label in ((transfer_src, "Transfer"), (transport_src, "Transport")):
            marker = f'"{field_key}"'
            idx = src.index(marker)
            window = src[idx:idx + 200]
            assert 'widget="text_area"' in window, f"{label}'s {field_key!r} should use text_area"
