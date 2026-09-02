"""Tests for cancellation_bulk_transport.py - bulk-changing the cancellation policy on a
supplier's already-live Transports (2026-08-28, product-owner request: "can i also include/
change the cancellation for a bulk or at least per supplier for transports?").

Uses the same offline platform_store isolation every other durable-storage test relies on
(see conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL) for the
cancellation_links.py interplay tests.
"""
import cancellation_links as cl
import cancellation_bulk_transport as cbt


def _reset_links(supplier_id=None):
    cl.set_type_link("Transport", [])
    if supplier_id:
        cl.set_supplier_link(supplier_id, "Transport", [])


# ----------------------------------------------------------------------
# default_new_tiers
# ----------------------------------------------------------------------

def test_default_new_tiers_falls_back_to_house_default_when_nothing_saved():
    _reset_links("SUP-CBT-1")
    tiers, label = cbt.default_new_tiers("SUP-CBT-1")
    assert tiers == [{"days": 30, "fee_percentage": 0.0}]
    assert "house default" in label


def test_default_new_tiers_uses_type_wide_link_when_set():
    _reset_links("SUP-CBT-2")
    cl.set_type_link("Transport", [{"days": 14, "fee_percentage": 50}])
    tiers, label = cbt.default_new_tiers("SUP-CBT-2")
    assert tiers == [{"days": 14, "fee_percentage": 50.0}]
    assert "company-wide" in label


def test_default_new_tiers_supplier_specific_wins_over_type_wide():
    _reset_links("SUP-CBT-3")
    cl.set_type_link("Transport", [{"days": 14, "fee_percentage": 50}])
    cl.set_supplier_link("SUP-CBT-3", "Transport", [{"days": 60, "fee_percentage": 0}])
    tiers, label = cbt.default_new_tiers("SUP-CBT-3")
    assert tiers == [{"days": 60, "fee_percentage": 0.0}]
    assert "this supplier" in label


def test_default_new_tiers_returns_a_fresh_copy_not_a_reference():
    _reset_links("SUP-CBT-4")
    cl.set_supplier_link("SUP-CBT-4", "Transport", [{"days": 21, "fee_percentage": 10}])
    tiers, _ = cbt.default_new_tiers("SUP-CBT-4")
    tiers[0]["days"] = 999
    tiers2, _ = cbt.default_new_tiers("SUP-CBT-4")
    assert tiers2[0]["days"] == 21


# ----------------------------------------------------------------------
# _wire_ranges_to_fee_tiers
# ----------------------------------------------------------------------

def test_wire_ranges_to_fee_tiers_converts_refund_to_fee():
    out = cbt._wire_ranges_to_fee_tiers([{"days": 30, "percentage": 100.0, "isBeforeStart": True}])
    assert out == [{"days": 30, "fee_percentage": 0.0}]


def test_wire_ranges_to_fee_tiers_handles_partial_refund():
    out = cbt._wire_ranges_to_fee_tiers([{"days": 7, "percentage": 25.0}])
    assert out == [{"days": 7, "fee_percentage": 75.0}]


def test_wire_ranges_to_fee_tiers_sorts_descending_by_days():
    out = cbt._wire_ranges_to_fee_tiers([{"days": 7, "percentage": 0.0}, {"days": 30, "percentage": 100.0}])
    assert [t["days"] for t in out] == [30, 7]


def test_wire_ranges_to_fee_tiers_empty_and_garbage_input():
    assert cbt._wire_ranges_to_fee_tiers(None) == []
    assert cbt._wire_ranges_to_fee_tiers([]) == []
    assert cbt._wire_ranges_to_fee_tiers([{"days": "not a number", "percentage": 100}]) == []
    assert cbt._wire_ranges_to_fee_tiers(["not a dict"]) == []


# ----------------------------------------------------------------------
# _tiers_equal
# ----------------------------------------------------------------------

def test_tiers_equal_same_content_different_order():
    a = [{"days": 30, "fee_percentage": 0}, {"days": 7, "fee_percentage": 100}]
    b = [{"days": 7, "fee_percentage": 100}, {"days": 30, "fee_percentage": 0}]
    assert cbt._tiers_equal(a, b) is True


def test_tiers_equal_different_content():
    a = [{"days": 30, "fee_percentage": 0}]
    b = [{"days": 30, "fee_percentage": 50}]
    assert cbt._tiers_equal(a, b) is False


def test_tiers_equal_both_empty():
    assert cbt._tiers_equal([], []) is True
    assert cbt._tiers_equal(None, None) is True


# ----------------------------------------------------------------------
# _current_cancellation_snippet / _swap_cancellation_paragraph
# ----------------------------------------------------------------------

def test_snippet_finds_the_cancellation_paragraph():
    html = "<p>Private transfer from the airport.</p><p>Free cancellation up to 30 days before arrival.</p>"
    assert cbt._current_cancellation_snippet(html) == "Free cancellation up to 30 days before arrival."


def test_snippet_returns_none_when_nothing_mentions_cancellation():
    html = "<p>Private transfer from the airport.</p><p>What to bring:\nPassport</p>"
    assert cbt._current_cancellation_snippet(html) is None


def test_snippet_prefers_the_first_match_over_a_manual_note_mentioning_it_too():
    # Composition order (see builder.py's _with_manual_notes/_with_what_to_bring): description,
    # then cancellation, then what-to-bring, then manual note LAST. A manual note that also
    # happens to mention cancellation (a real documented example: "this supplier's cancellation
    # terms changed in March") must never be picked over the real policy paragraph.
    html = ("<p>Private transfer from the airport.</p>"
           "<p>Free cancellation up to 30 days before arrival.</p>"
           "<p>This supplier's cancellation terms changed in March - call ahead.</p>")
    assert cbt._current_cancellation_snippet(html) == "Free cancellation up to 30 days before arrival."


def test_swap_replaces_existing_paragraph_leaving_others_untouched():
    html = ("<p>Private transfer from the airport.</p>"
           "<p>Free cancellation up to 30 days before arrival.</p>"
           "<p>What to bring:\nPassport</p>")
    new_html, found = cbt._swap_cancellation_paragraph(html, "NEW POLICY TEXT")
    assert found is True
    assert new_html == ("<p>Private transfer from the airport.</p>"
                        "<p>NEW POLICY TEXT</p>"
                        "<p>What to bring:\nPassport</p>")


def test_swap_only_replaces_the_first_matching_paragraph():
    html = ("<p>Desc.</p><p>Free cancellation up to 30 days before arrival.</p>"
           "<p>This supplier's cancellation terms changed in March.</p>")
    new_html, found = cbt._swap_cancellation_paragraph(html, "NEW POLICY TEXT")
    assert found is True
    assert new_html == ("<p>Desc.</p><p>NEW POLICY TEXT</p>"
                        "<p>This supplier's cancellation terms changed in March.</p>")


def test_swap_inserts_after_first_paragraph_when_nothing_found():
    html = "<p>Private transfer from the airport.</p><p>What to bring:\nPassport</p>"
    new_html, found = cbt._swap_cancellation_paragraph(html, "NEW POLICY TEXT")
    assert found is False
    assert new_html == ("<p>Private transfer from the airport.</p>"
                        "<p>NEW POLICY TEXT</p>"
                        "<p>What to bring:\nPassport</p>")


def test_swap_handles_a_single_paragraph_description():
    html = "<p>Private transfer from the airport.</p>"
    new_html, found = cbt._swap_cancellation_paragraph(html, "NEW POLICY TEXT")
    assert found is False
    assert new_html == "<p>Private transfer from the airport.</p><p>NEW POLICY TEXT</p>"


def test_swap_handles_a_completely_empty_description():
    new_html, found = cbt._swap_cancellation_paragraph("", "NEW POLICY TEXT")
    assert found is False
    assert new_html == "<p>NEW POLICY TEXT</p>"
    new_html2, found2 = cbt._swap_cancellation_paragraph(None, "NEW POLICY TEXT")
    assert found2 is False
    assert new_html2 == "<p>NEW POLICY TEXT</p>"


def test_swap_matches_a_paragraph_that_has_attributes():
    # CONFIRMED FIX (2026-08-30 audit): Travel Compositor's own editor writes attributed <p>
    # tags (e.g. dir="ltr") when a paragraph has been hand-edited there. A bare r"<p>(.*?)</p>"
    # regex would miss this paragraph entirely and fall through to the INSERT path, leaving the
    # old cancellation sentence live alongside the new one instead of replacing it.
    html = ('<p dir="ltr">Private transfer from the airport.</p>'
           '<p dir="ltr">Free cancellation up to 30 days before arrival.</p>')
    new_html, found = cbt._swap_cancellation_paragraph(html, "NEW POLICY TEXT")
    assert found is True
    assert new_html == ('<p dir="ltr">Private transfer from the airport.</p>'
                        "<p>NEW POLICY TEXT</p>")
    assert "Free cancellation up to 30 days before arrival" not in new_html


def test_snippet_finds_a_paragraph_that_has_attributes():
    html = ('<p dir="ltr">Private transfer from the airport.</p>'
           '<p class="policy">Free cancellation up to 30 days before arrival.</p>')
    assert cbt._current_cancellation_snippet(html) == "Free cancellation up to 30 days before arrival."


# ----------------------------------------------------------------------
# A tiny fake client for load_supplier_transports_for_cancellation / apply_proposals
# ----------------------------------------------------------------------

class _FakeTransportClient:
    """Hand-built fake, not Mock() - same philosophy as conftest.py's FakeTravelCompositorAPI:
    returns real, small, shape-accurate responses rather than an auto-mock that would hide a
    shape mismatch."""

    def __init__(self, transports=None, get_error=None, update_error_for=None):
        self._transports = transports or []
        self._get_error = get_error
        self._update_error_for = update_error_for or {}
        self.update_calls = []

    def get_transports(self, supplier_id):
        if self._get_error:
            return {"error": 500, "message": self._get_error}
        return {"transport": self._transports}

    def update_transport(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("id") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["id"]]}
        return {"id": payload.get("id"), "code": 200}


def _sample_record(id_="T1", name="Airport - Hotel X", days=30, refund_pct=100.0,
                   description="<p>Private transfer from the airport.</p><p>Free cancellation up to 30 days before arrival.</p>"):
    return {
        "id": id_,
        "name": name,
        "segments": [{"departureLocationCode": "SSH", "arrivalLocationCode": "HTL-X"}],
        "cancellationRanges": [{"days": days, "percentage": refund_pct, "isBeforeStart": True}],
        "datasheets": {"EN": {"name": name, "description": description}},
        "baseAdultPrice": 42.0,
        "currency": "EUR",
    }


# ----------------------------------------------------------------------
# load_supplier_transports_for_cancellation
# ----------------------------------------------------------------------

def test_load_returns_one_row_per_transport_with_current_state():
    client = _FakeTransportClient(transports=[_sample_record()])
    rows, err = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    assert err is None
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "T1"
    assert row["current_fee_tiers"] == [{"days": 30, "fee_percentage": 0.0}]
    assert row["current_cancellation_snippet"] == "Free cancellation up to 30 days before arrival."
    assert row["raw"]["id"] == "T1"


def test_load_sorts_rows_by_name():
    client = _FakeTransportClient(transports=[
        _sample_record(id_="T2", name="Zebra Route"),
        _sample_record(id_="T1", name="Alpha Route"),
    ])
    rows, err = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    assert err is None
    assert [r["name"] for r in rows] == ["Alpha Route", "Zebra Route"]


def test_load_surfaces_a_client_error():
    client = _FakeTransportClient(get_error="boom")
    rows, err = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    assert rows == []
    assert "boom" in err


def test_load_ignores_non_dict_records():
    client = _FakeTransportClient()
    client._transports = ["not a dict", _sample_record()]
    rows, err = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    assert err is None
    assert len(rows) == 1


# ----------------------------------------------------------------------
# build_proposals
# ----------------------------------------------------------------------

def test_build_proposals_flags_unchanged_when_current_matches_new():
    # The snippet must match what THIS SAME synthesis path (build_proposals' own
    # _cancellation_voucher_text(None, ranges) call) would produce for these tiers, not just
    # be semantically equivalent wording - see the test right below for the more common real
    # case where a record's existing text came from a different source and genuinely differs.
    matching_description = ("<p>Private transfer from the airport.</p>"
                            "<p>Cancellation Policy:\n- Free cancellation if cancelled at least 30 days before arrival.</p>")
    client = _FakeTransportClient(transports=[_sample_record(days=30, refund_pct=100.0, description=matching_description)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["unchanged"] is True


def test_build_proposals_not_unchanged_when_tiers_match_but_wording_differs():
    # A live record's cancellation sentence can come from a document's own stated wording
    # (or the plain default_text template) rather than this module's bullet-style synthesis -
    # same tiers, different sentence. Correctly NOT flagged as unchanged, so the human still
    # sees (and can accept) the wording gets normalized to the synthesized form.
    client = _FakeTransportClient(transports=[_sample_record(days=30, refund_pct=100.0)])  # default fixture wording
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["unchanged"] is False


def test_build_proposals_unchanged_uses_post_floor_new_tiers_not_raw_input():
    # CONFIRMED FIX (2026-08-30 audit): cancellation_links.py does not enforce the 30-day/
    # 100%-refund floor at SAVE time (only builder._cancellation_ranges_from_tiers does, at
    # APPLY time here) - so a saved link, or a value typed directly into this form, can read
    # e.g. {days:14, fee_percentage:0} even though applying it always floors to
    # {days:30, fee_percentage:0}. A Transport whose CURRENT tiers happen to already read that
    # same unfloored {days:14, fee_percentage:0} must NOT be marked unchanged - applying the
    # new policy would in fact push it out to 30 days, a real, live-visible change.
    matching_floored_description = (
        "<p>Private transfer from the airport.</p>"
        "<p>Cancellation Policy:\n- Free cancellation if cancelled at least 30 days before arrival.</p>"
    )
    client = _FakeTransportClient(transports=[
        _sample_record(days=14, refund_pct=100.0, description=matching_floored_description)
    ])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    assert rows[0]["current_fee_tiers"] == [{"days": 14, "fee_percentage": 0.0}]

    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 0.0}])
    # The floor pushes the applied policy out to 30 days - shown to the human as such...
    assert proposals[0]["new_fee_tiers"] == [{"days": 30, "fee_percentage": 0.0}]
    # ...and NOT flagged unchanged, even though the raw input matched the record's raw current
    # tiers exactly - applying it actually changes the live 14-day value to 30.
    assert proposals[0]["unchanged"] is False


def test_build_proposals_flags_changed_when_tiers_differ():
    client = _FakeTransportClient(transports=[_sample_record(days=30, refund_pct=100.0)])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    assert proposals[0]["unchanged"] is False
    assert proposals[0]["new_fee_tiers"] == [{"days": 14, "fee_percentage": 50.0}]


def test_build_proposals_floors_a_too_generous_new_tier_to_30_days():
    # Same standing house rule every other product already enforces (builder._cancellation_
    # ranges_from_tiers) - a 100%-refund tier requested at fewer than 30 days gets pushed out
    # to 30, even when a human typed it directly into this bulk-update form.
    client = _FakeTransportClient(transports=[_sample_record()])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 5, "fee_percentage": 0.0}])
    assert proposals[0]["new_ranges_wire"] == [{"days": 30, "percentage": 100.0, "isBeforeStart": True}]


def test_build_proposals_marks_existing_paragraph_found():
    client = _FakeTransportClient(transports=[_sample_record()])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    assert proposals[0]["existing_paragraph_found"] is True


def test_build_proposals_marks_paragraph_not_found_when_description_has_none():
    client = _FakeTransportClient(transports=[
        _sample_record(description="<p>Private transfer from the airport.</p>")
    ])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    assert proposals[0]["existing_paragraph_found"] is False


# ----------------------------------------------------------------------
# apply_proposals
# ----------------------------------------------------------------------

def test_apply_updates_cancellation_ranges_and_description_only():
    client = _FakeTransportClient(transports=[_sample_record()])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    results = cbt.apply_proposals(client, "SUP-X", proposals)
    assert results == [{"id": "T1", "name": "Airport - Hotel X", "ok": True, "detail": ""}]

    supplier_id, payload = client.update_calls[0]
    assert supplier_id == "SUP-X"
    assert payload["cancellationRanges"] == [{"days": 14, "percentage": 50.0, "isBeforeStart": True}]
    new_description = payload["datasheets"]["EN"]["description"]
    # CONFIRMED BUG FIX (audit 2026-09-01, MEDIUM/LOW batch 3): _cancellation_voucher_text's
    # partial-refund wording used to say "within N days OF arrival" (the opposite condition from
    # what the structured tier actually grants) - now says "less than N days BEFORE arrival",
    # matching the free-cancellation branch's existing correct phrasing.
    assert "50% cancellation fee if cancelled less than 14 days before arrival" in new_description
    assert "Free cancellation up to 30 days before arrival" not in new_description  # old text is gone
    assert "<p>Private transfer from the airport.</p>" in new_description  # unrelated paragraph kept
    # Everything else on the record is untouched.
    assert payload["baseAdultPrice"] == 42.0
    assert payload["currency"] == "EUR"
    assert payload["name"] == "Airport - Hotel X"


def test_apply_surfaces_a_per_row_error_without_stopping_the_others():
    client = _FakeTransportClient(
        transports=[_sample_record(id_="T1", name="Route One"), _sample_record(id_="T2", name="Route Two")],
        update_error_for={"T1": "inactive transport"},
    )
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    results = cbt.apply_proposals(client, "SUP-X", proposals)
    by_id = {r["id"]: r for r in results}
    assert by_id["T1"]["ok"] is False
    assert "inactive transport" in by_id["T1"]["detail"]
    assert by_id["T2"]["ok"] is True
    assert len(client.update_calls) == 2


def test_apply_only_touches_the_proposals_passed_in():
    client = _FakeTransportClient(transports=[
        _sample_record(id_="T1", name="Route One"), _sample_record(id_="T2", name="Route Two"),
    ])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}])
    only_first = [p for p in proposals if p["id"] == "T1"]
    results = cbt.apply_proposals(client, "SUP-X", only_first)
    assert len(results) == 1
    assert len(client.update_calls) == 1
