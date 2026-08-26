"""Tests for the review screen's editable results table (outreach_tool.py).

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "it must be possible to change all field in the
'Untick anyone you don't want to contact...' field also it must be possible to add more
partners to the list." Previously only Send/Name/Email were editable, and a new row could only
be added via the separate "Add a supplier by hand" expander. Now every column in the review
table is editable, and st.data_editor's own num_rows="dynamic" lets a row be added or removed
directly in the table too.

_apply_review_table_edits/_new_supplier_from_table_row are pulled out as pure functions (same
convention test_outreach_tool_queue.py already established for _merge_one_job_result/
_finalize_queue_result) specifically so this logic can be tested without a running Streamlit
script - a data_editor widget only exists inside one.

The diff shape tested here (`{"edited_rows": {int_row_index: {col: val}}, "added_rows":
[{col: val}, ...], "deleted_rows": [int_row_index, ...]}`) is Streamlit's own documented
st.data_editor session-state shape for num_rows="dynamic" - confirmed against the installed
streamlit package's own EditingState TypedDict (streamlit/elements/widgets/data_editor.py),
including that edited_rows keys deserialize to real Python ints (not strings), since the raw
widget state round-trips through JSON.
"""
import outreach_tool as ot


def _supplier(name, email=None, website=None, social=None, listing=None, rating=None,
             reason="Found via web search."):
    return {
        "id": f"id-{name}", "name": name, "email": email, "website": website, "social": social,
        "listingUrl": listing, "rating": rating, "selectionReason": reason,
        "selected": bool(email), "sources": [], "isMock": False,
    }


# ======================================================================
# _apply_review_table_edits - editing an existing row
# ======================================================================
def test_editing_send_checkbox_updates_selected():
    suppliers = [_supplier("A", email="a@x.com")]
    diff = {"edited_rows": {0: {"Send": False}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["selected"] is False


def test_editing_name_updates_name():
    suppliers = [_supplier("Old Name", email="a@x.com")]
    diff = {"edited_rows": {0: {"Name": "  Corrected Name  "}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["name"] == "Corrected Name"


def test_clearing_name_keeps_the_old_name_a_supplier_always_needs_one():
    suppliers = [_supplier("Real Business", email="a@x.com")]
    diff = {"edited_rows": {0: {"Name": "   "}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["name"] == "Real Business"


def test_editing_website_social_listing_all_apply():
    suppliers = [_supplier("A", email="a@x.com")]
    diff = {"edited_rows": {0: {
        "Website": "https://new-site.example", "Social": "https://instagram.com/new",
        "Listing": "https://tripadvisor.com/new",
    }}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["website"] == "https://new-site.example"
    assert result[0]["social"] == "https://instagram.com/new"
    assert result[0]["listingUrl"] == "https://tripadvisor.com/new"


def test_clearing_website_sets_it_to_none_not_empty_string():
    suppliers = [_supplier("A", email="a@x.com", website="https://old.example")]
    diff = {"edited_rows": {0: {"Website": ""}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["website"] is None


def test_editing_rating_parses_a_valid_number():
    suppliers = [_supplier("A", email="a@x.com")]
    diff = {"edited_rows": {0: {"Rating": "4.5"}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["rating"] == 4.5


def test_editing_rating_with_garbage_leaves_it_none_instead_of_crashing():
    suppliers = [_supplier("A", email="a@x.com")]
    diff = {"edited_rows": {0: {"Rating": "not-a-number"}}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["rating"] is None


def test_editing_why_selected_updates_selection_reason():
    suppliers = [_supplier("A", email="a@x.com", reason="Original reason.")]
    diff = {"edited_rows": {0: {"Why selected": "Personally verified by phone."}},
           "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["selectionReason"] == "Personally verified by phone."


def test_an_unedited_row_is_untouched():
    suppliers = [_supplier("Untouched", email="u@x.com", rating=4.2)]
    diff = {"edited_rows": {}, "added_rows": [], "deleted_rows": []}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert result[0]["name"] == "Untouched"
    assert result[0]["rating"] == 4.2


# ======================================================================
# _apply_review_table_edits - deleting a row
# ======================================================================
def test_deleting_a_row_removes_it():
    suppliers = [_supplier("Keep", email="k@x.com"), _supplier("Remove", email="r@x.com")]
    diff = {"edited_rows": {}, "added_rows": [], "deleted_rows": [1]}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert [s["name"] for s in result] == ["Keep"]


def test_deleted_row_index_is_by_ORIGINAL_position_not_shifted():
    """The diff's row indices refer to positions in the ORIGINAL suppliers list, not a
    post-edit renumbering - deleting row 0 while also editing row 1 must still land the edit
    on the right supplier."""
    suppliers = [_supplier("Delete Me", email="d@x.com"), _supplier("Edit Me", email="e@x.com")]
    diff = {"edited_rows": {1: {"Name": "Edited Name"}}, "added_rows": [], "deleted_rows": [0]}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert len(result) == 1
    assert result[0]["name"] == "Edited Name"


# ======================================================================
# _apply_review_table_edits / _new_supplier_from_table_row - adding a row
# ======================================================================
def test_adding_a_row_via_the_table_appends_a_new_supplier():
    suppliers = [_supplier("Existing", email="e@x.com")]
    diff = {"edited_rows": {}, "deleted_rows": [], "added_rows": [
        {"Name": "New Partner", "Email": "new@example.com", "Website": "https://new.example"},
    ]}
    result = ot._apply_review_table_edits(suppliers, diff)
    assert len(result) == 2
    added = result[1]
    assert added["name"] == "New Partner"
    assert added["email"] == "new@example.com"
    assert added["website"] == "https://new.example"
    assert added["addedManually"] is True
    assert added["selected"] is True  # a real email was given, so it's pre-ticked


def test_adding_a_row_with_no_email_is_not_pre_ticked():
    diff = {"edited_rows": {}, "deleted_rows": [], "added_rows": [{"Name": "No Email Yet"}]}
    result = ot._apply_review_table_edits([], diff)
    assert len(result) == 1
    assert result[0]["email"] is None
    assert result[0]["selected"] is False


def test_a_blank_placeholder_added_row_with_no_name_is_skipped():
    """num_rows='dynamic' always shows one blank row at the bottom ready to be filled in - if
    it's still empty when the script reruns, it must not become a junk supplier record."""
    diff = {"edited_rows": {}, "deleted_rows": [], "added_rows": [{"Name": "", "Email": ""}]}
    result = ot._apply_review_table_edits([], diff)
    assert result == []


def test_new_supplier_from_table_row_matches_to_supplier_record_shape():
    """Same fields to_supplier_record() (outreach_discovery.py) produces, so a hand-added row
    behaves identically to a search-found one everywhere downstream (sending, blocklisting)."""
    row = ot._new_supplier_from_table_row({"Name": "Hand Added Co", "Email": "hand@example.com"})
    expected_keys = {
        "id", "name", "email", "social", "socialPlatform", "website", "listingUrl",
        "listingSource", "selectionReason", "reviewSummary", "rating", "reviewCount",
        "sources", "selected", "isMock",
    }
    assert expected_keys.issubset(row.keys())


# ======================================================================
# An empty diff (nothing edited/added/deleted this run) leaves the list unchanged
# ======================================================================
def test_empty_diff_is_a_no_op():
    suppliers = [_supplier("A", email="a@x.com"), _supplier("B", email="b@x.com")]
    result = ot._apply_review_table_edits(suppliers, {})
    assert [s["name"] for s in result] == ["A", "B"]
