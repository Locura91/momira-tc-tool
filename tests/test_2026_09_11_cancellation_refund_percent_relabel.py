"""Regression test for the "Cancellation Fee %" -> refund% inversion bug (product owner,
2026-09-11) - see claude/bulk-cancellation-refund-fee-inversion-2026-09-11.md in project docs
for the full incident.

WHAT HAPPENED: the three screens where a HUMAN types the desired RESULT cancellation policy
directly (cancellation_links.render_cancellation_link_editor's saved-link tables, and the
near-identical "New policy" tables in render_transport_cancellation_bulk_flow and
render_generic_cancellation_bulk_flow in app.py) all asked for "Cancellation Fee %" - the same
convention ui_components.render_cancellation_policy_editor correctly uses when TRANSCRIBING a
supplier's own document (which genuinely states its terms as a fee). But a human setting the
policy directly naturally thinks in REFUND% - the house rule itself is phrased "30 days or
prior for 100% refund", and Travel Compositor's own wire field ("percentage" on
cancellationRanges) is the refund percentage too. Typing "100" meaning "100% refund" into a
Fee% field instead silently applied a 0%-refund/no-refund policy - confirmed live against 18+
Transports of one real supplier via a before/after GET diff.

THE FIX: storage is UNCHANGED (still {"days","fee_percentage"} internally - every downstream
consumer, builder._cancellation_ranges_from_tiers included, still expects that exact shape).
Only the human-facing column in these three screens changed, to "Refund % if cancelled by this
deadline", converted right at the UI boundary (refund = 100 - fee, and back). This test can't
drive the actual Streamlit widgets (no Streamlit runtime in this suite), so it verifies the
same thing two ways: (1) the boundary math itself, reimplemented identically to what each of
the three call sites now does, behaves correctly and is no longer invertible-by-accident; (2)
a structural check that the old "Cancellation Fee %" column string is gone from exactly the
three human-input screens (NOT from ui_components.render_cancellation_policy_editor, which is
correctly left alone - it transcribes a supplier document's own stated fee)."""
import re


def _safe_float(v, fallback=0.0):
    try:
        if v is None:
            return fallback
        return float(v)
    except (TypeError, ValueError):
        return fallback


def _refund_input_to_fee_percentage(refund_input):
    """Reimplements the exact boundary conversion now used at all three call sites (the
    _df_to_tiers closures in app.py x2 and cancellation_links.py x1): what a human types into
    the Refund% column becomes fee_percentage = 100 - refund, clamped 0-100."""
    refund_pct = max(0.0, min(100.0, _safe_float(refund_input, fallback=100.0)))
    return max(0.0, min(100.0, 100.0 - refund_pct))


def _fee_percentage_to_refund_display(fee_percentage):
    """Reimplements the exact boundary conversion now used for displaying a stored tier back as
    Refund% (the _tier_table closures at all three call sites)."""
    return round(100.0 - _safe_float(fee_percentage, fallback=0.0), 4)


def test_typing_100_now_means_full_refund_not_no_refund():
    # THE ACTUAL BUG: before this fix, typing 100 into "Cancellation Fee %" (meaning "100%
    # refund", matching the house rule's own wording) produced fee_percentage=100 - a 0%-
    # refund/no-refund policy, the exact opposite of what was intended.
    assert _refund_input_to_fee_percentage(100) == 0.0


def test_typing_0_now_means_no_refund_not_full_refund():
    assert _refund_input_to_fee_percentage(0) == 100.0


def test_house_default_fee_percentage_zero_displays_as_100_percent_refund():
    # _HOUSE_DEFAULT_TIERS in both cancellation_bulk_transport.py and cancellation_bulk.py is
    # still {"days": 30, "fee_percentage": 0.0} (storage unchanged) - it must now DISPLAY as
    # "100% refund" in the human-facing table, not "0%".
    assert _fee_percentage_to_refund_display(0.0) == 100.0


def test_round_trip_through_the_new_boundary_is_stable():
    for fee_in in (0.0, 25.0, 50.0, 100.0):
        refund_shown = _fee_percentage_to_refund_display(fee_in)
        fee_back = _refund_input_to_fee_percentage(refund_shown)
        assert abs(fee_back - fee_in) < 1e-6


def test_missing_refund_input_falls_back_to_full_refund_not_no_refund():
    # A blank/unparseable Refund% cell must default to the SAFE side (100% refund, matching the
    # house standard) - never silently default to 0% refund the way the old Fee% column's
    # fallback=0.0 effectively did before this fix (blank fee -> 0% fee -> 100% refund was
    # actually already safe there; the point here is the new column's own fallback must stay
    # on the safe side too, not flip to unsafe just because the column changed).
    assert _refund_input_to_fee_percentage(None) == 0.0
    assert _refund_input_to_fee_percentage("not a number") == 0.0


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_the_three_human_input_screens_no_longer_ask_for_cancellation_fee_percent():
    app_src = _read("app.py")
    links_src = _read("cancellation_links.py")

    # The two app.py "New policy" bulk-update screens (ctb_ = Transport, cb_ = ClosedTour/
    # Ticket/Transfer/Hotel) - isolate each function body and check neither still contains the
    # old inverted column label.
    ctb_match = re.search(
        r"def render_transport_cancellation_bulk_flow\(client\):.*?(?=\ndef \w)", app_src, re.S)
    cb_match = re.search(
        r"def render_generic_cancellation_bulk_flow\(client, product_type\):.*?(?=\ndef \w)",
        app_src, re.S)
    assert ctb_match and cb_match, "expected both bulk-cancellation render functions in app.py"
    # Check only actual code (not comments or docstrings) - the fix's own explanatory prose
    # legitimately still mentions the old phrase "Cancellation Fee %" for context.
    def _code_only(body):
        no_docstrings = re.sub(r'"""[\s\S]*?"""', "", body)
        return "\n".join(line for line in no_docstrings.splitlines() if not line.lstrip().startswith("#"))

    assert '"Cancellation Fee %"' not in _code_only(ctb_match.group(0))
    assert '"Cancellation Fee %"' not in _code_only(cb_match.group(0))
    assert "Refund %" in ctb_match.group(0)
    assert "Refund %" in cb_match.group(0)

    # cancellation_links.py's saved-link editor - same fix.
    editor_match = re.search(
        r"def render_cancellation_link_editor\(.*?(?=\ndef \w|\Z)", links_src, re.S)
    assert editor_match, "expected render_cancellation_link_editor in cancellation_links.py"
    assert '"Cancellation Fee %"' not in _code_only(editor_match.group(0))
    assert "Refund %" in editor_match.group(0)


def test_the_document_transcription_screen_is_deliberately_left_as_fee_percent():
    # ui_components.render_cancellation_policy_editor is for TRANSCRIBING a supplier's own
    # document, which genuinely states its terms as a fee (e.g. "25% fee if cancelled within 30
    # days") - this one is correct as Fee% and must NOT be changed by this fix.
    ui_src = _read("ui_components.py")
    editor_match = re.search(
        r"def render_cancellation_policy_editor\(.*?(?=\ndef \w|\Z)", ui_src, re.S)
    assert editor_match, "expected render_cancellation_policy_editor in ui_components.py"
    assert "Cancellation Fee %" in editor_match.group(0)
