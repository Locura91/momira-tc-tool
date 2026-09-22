"""Regression tests for a real product-owner bug report (2026-09-22, screenshot comparing an
original Transport's description against its duplicate):

    "It would be great, if the format of the text is the same as in the original one. It always
    shall be regardless if transfer or transport."

The screenshot showed a real "Cairo - Aswan Train Ticket" description with a genuine <ul><li>
bullet list ("AC First Class Seat" / "Sleeper Double Cabin" / "Sleeper Single Cabin", each its
own bullet) - the DUPLICATE's description had the same three lines, but with no bullets at all,
just plain unbulleted text.

Root cause: ui_components._html_to_plain_for_editing/_plain_to_html_for_saving (the shared
plain<->HTML round-trip every "html_text_area" description field in this app uses - not just the
duplicate flows) already flattened a <ul><li> list into bare plain-text lines with no marker
distinguishing a list item from an ordinary paragraph line, so _plain_to_html_for_saving had no
way to tell a list apart from a paragraph and always rebuilt <p><br> paragraphs, never <ul><li>.
This wasn't specific to Transfer or Transport, or even to the duplicate flow - it's the generic
widget every description field in this app goes through (ui_components.editable_field's
"html_text_area"), so ANY save through it silently downgraded a bulleted list into plain lines.

Fixed by marking each list item with a leading "- " in the plain-text representation (the widely
understood plain-text bullet convention) on the way OUT of HTML, which _plain_to_html_for_saving
now detects (a whole paragraph block where every line starts with "- ") to rebuild the exact same
<ul><li> structure on the way back IN.
"""
from ui_components import _html_to_plain_for_editing, _plain_to_html_for_saving


def test_bullet_list_survives_a_full_round_trip():
    original_html = (
        "<p>Train ticket from Cairo to Aswan. The estimated departure time may change slightly "
        "due to ticket availability.</p>"
        "<p>Three different categories are available:</p>"
        "<ul><li>AC First Class Seat</li><li>Sleeper Double Cabin</li><li>Sleeper Single Cabin</li></ul>"
    )
    plain = _html_to_plain_for_editing(original_html)
    rebuilt_html = _plain_to_html_for_saving(plain)
    assert "<ul>" in rebuilt_html and "</ul>" in rebuilt_html
    assert "<li>AC First Class Seat</li>" in rebuilt_html
    assert "<li>Sleeper Double Cabin</li>" in rebuilt_html
    assert "<li>Sleeper Single Cabin</li>" in rebuilt_html
    # the surrounding paragraphs are still real <p> paragraphs, untouched by the list fix
    assert "<p>Train ticket from Cairo to Aswan." in rebuilt_html
    assert "<p>Three different categories are available:</p>" in rebuilt_html


def test_html_to_plain_marks_each_list_item_with_a_leading_dash():
    html = "<p>Intro text.</p><ul><li>Item one</li><li>Item two</li></ul>"
    plain = _html_to_plain_for_editing(html)
    lines = [ln for ln in plain.split("\n") if ln.strip()]
    assert "- Item one" in lines
    assert "- Item two" in lines


def test_plain_to_html_rebuilds_a_dash_prefixed_block_as_a_real_bullet_list():
    plain = "Intro text.\n\n- Item one\n- Item two\n- Item three"
    html = _plain_to_html_for_saving(plain)
    assert html == (
        "<p>Intro text.</p><p><br></p><ul><li>Item one</li><li>Item two</li><li>Item three</li></ul>"
    )


def test_a_block_mixing_dash_lines_with_ordinary_text_is_treated_as_an_ordinary_paragraph():
    # Safer than guessing at a partial/malformed list - matches this function's existing
    # philosophy elsewhere (e.g. build_and_rewrite_transfer_swap_payload's own "never worse than
    # doing nothing" fallback).
    plain = "- Item one\nsome ordinary continuation line\n- Item two"
    html = _plain_to_html_for_saving(plain)
    assert "<ul>" not in html
    assert html.startswith("<p>")


def test_bold_and_italic_markers_still_work_inside_a_bullet_list_item():
    plain = "- **Bold** item\n- *italic* item"
    html = _plain_to_html_for_saving(plain)
    assert "<li><strong>Bold</strong> item</li>" in html
    assert "<li><em>italic</em> item</li>" in html


def test_plain_paragraphs_with_no_list_are_completely_unaffected_by_the_fix():
    # Regression guard: the fix must not change behavior for the overwhelmingly common case of
    # plain paragraph-only text (no bullets at all).
    plain = "First paragraph.\n\nSecond paragraph with **bold** text."
    html = _plain_to_html_for_saving(plain)
    assert html == "<p>First paragraph.</p><p><br></p><p>Second paragraph with <strong>bold</strong> text.</p>"


def test_a_transport_style_description_with_a_trailing_bullet_list_round_trips_through_the_ai_rewrite_plain_text_shape():
    # Mirrors exactly the real screenshot's shape - a lead paragraph, a "colon" paragraph
    # introducing the list, then the bulleted list itself - confirming the whole three-block
    # shape (not just a single list) survives the round trip end to end.
    original_html = (
        "<p>Train ticket from Cairo to Aswan. The estimated departure time may change slightly "
        "due to ticket availability.</p><p><br></p>"
        "<p>Three different categories are available:</p><p><br></p>"
        "<ul><li>AC First Class Seat</li><li>Sleeper Double Cabin</li><li>Sleeper Single Cabin</li></ul>"
    )
    plain = _html_to_plain_for_editing(original_html)
    # This is exactly the shape ai_extractor.rewrite_route_description_for_new_direction's own
    # system prompt now asks the AI to preserve (blank line between paragraphs, "- " per list item).
    blocks = [b for b in plain.split("\n\n") if b.strip()]
    assert len(blocks) == 3
    assert blocks[2].splitlines() == ["- AC First Class Seat", "- Sleeper Double Cabin", "- Sleeper Single Cabin"]
    rebuilt = _plain_to_html_for_saving(plain)
    assert rebuilt == original_html
