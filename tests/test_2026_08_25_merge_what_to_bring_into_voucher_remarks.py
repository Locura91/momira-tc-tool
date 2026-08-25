"""Regression tests for a real product-owner request (2026-08-25), about the ticket-creation
screen: "Voucher Remarks (shown to the customer)" and "What to bring (added to voucher remarks)"
were two separate editable boxes that always ended up concatenated at publish time anyway (see
builder._with_what_to_bring). "They can be combined in one." -> the ticket-editing screens now
show a single "Voucher Remarks" box; merge_what_to_bring_into_voucher_remarks (ui_components.py)
folds what_to_bring into voucher_remarks once, up front, using the exact same "What to bring:"
block format the builder itself used to append at publish time - and clears what_to_bring so the
builder's still-shared append logic (used by the other four products, which keep two fields)
finds nothing left to add and doesn't double it up.
"""
from ui_components import merge_what_to_bring_into_voucher_remarks
from builder import _with_what_to_bring


def test_merges_what_to_bring_into_voucher_remarks_with_a_blank_line():
    data = {"voucher_remarks": "Please arrive 15 minutes early.",
            "what_to_bring": "Passport, sun cream, hat"}
    merge_what_to_bring_into_voucher_remarks(data)
    assert data["voucher_remarks"] == (
        "Please arrive 15 minutes early.\n\nWhat to bring:\nPassport, sun cream, hat")
    assert data["what_to_bring"] == ""


def test_merge_with_no_existing_voucher_remarks_uses_the_block_alone():
    data = {"voucher_remarks": "", "what_to_bring": "Passport, sun cream"}
    merge_what_to_bring_into_voucher_remarks(data)
    assert data["voucher_remarks"] == "What to bring:\nPassport, sun cream"
    assert data["what_to_bring"] == ""


def test_merge_is_a_no_op_when_what_to_bring_is_already_empty():
    data = {"voucher_remarks": "Some remark.", "what_to_bring": ""}
    merge_what_to_bring_into_voucher_remarks(data)
    assert data["voucher_remarks"] == "Some remark."


def test_merge_is_a_no_op_on_missing_keys():
    data = {}
    merge_what_to_bring_into_voucher_remarks(data)
    assert data == {}


def test_merge_is_idempotent_across_reruns():
    """Streamlit reruns the script on every interaction - calling this on every rerun must not
    keep re-appending the same block."""
    data = {"voucher_remarks": "Arrive early.", "what_to_bring": "Passport"}
    merge_what_to_bring_into_voucher_remarks(data)
    merge_what_to_bring_into_voucher_remarks(data)
    merge_what_to_bring_into_voucher_remarks(data)
    assert data["voucher_remarks"] == "Arrive early.\n\nWhat to bring:\nPassport"


def test_builders_own_append_is_a_no_op_after_the_ui_merge():
    """Once the UI has folded what_to_bring into voucher_remarks and cleared it, the builder's
    shared _with_what_to_bring helper (still used by every product's publish step) must not add
    a second "What to bring:" block on top."""
    data = {"voucher_remarks": "Arrive early.", "what_to_bring": "Passport"}
    merge_what_to_bring_into_voucher_remarks(data)
    published_text = _with_what_to_bring(data["voucher_remarks"], data)
    assert published_text == data["voucher_remarks"]
    assert published_text.count("What to bring:") == 1
