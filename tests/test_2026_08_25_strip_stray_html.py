"""Regression tests for the 2026-08-25 product-owner rule: "in no text never shall i see this
html code styles: </p>. We have to be more careful."

Plain-text customer-facing fields must never carry raw HTML markup, on ANY of the five products
(ClosedTour/Ticket/Transfer/Transport/Hotel) - the request was phrased as "in no text", not
scoped to one product. The two deliberate exceptions, confirmed against the real API, are left
untouched: ClosedTour's included/excluded (Travel Compositor genuinely expects `<ul><li>` there)
and Transport's description (the real live record stores it as `<p>...</p>` HTML too).

Covers both the pure strip_stray_html() helper directly, and its wiring into each product's
build_*_payloads pipeline so a stray tag can't slip through a field that doesn't obviously look
like "the voucher text".
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import builder
from builder import strip_stray_html, build_ticket_payloads, build_closed_tour_payloads
from schemas import HumanPreConfig
from test_builder_ticket import make_pre_config as make_ticket_pre_config, minimal_ticket_data
from test_builder_closed_tour import make_pre_config as make_tour_pre_config, minimal_extracted_data as minimal_tour_data


class _FakeAPI:
    def __getattr__(self, name):
        return lambda *a, **k: {}


# ---------------------------------------------------------------------------
# strip_stray_html() itself
# ---------------------------------------------------------------------------

def test_strips_the_exact_reported_tag():
    assert "</p>" not in strip_stray_html("Free cancellation.</p>")
    assert "<p>" not in strip_stray_html("<p>Free cancellation.</p>")


def test_block_tags_become_newlines_not_a_welded_run_on_sentence():
    result = strip_stray_html("<p>First paragraph.</p><p>Second paragraph.</p>")
    assert result == "First paragraph.\n\nSecond paragraph."


def test_br_becomes_a_line_break():
    result = strip_stray_html("Line one.<br>Line two.")
    assert result == "Line one.\nLine two."


def test_list_markup_stripped_with_break_between_items():
    result = strip_stray_html("<ul><li>Passport</li><li>Sun cream</li></ul>")
    assert "<" not in result and ">" not in result
    assert "Passport" in result and "Sun cream" in result


def test_inline_tags_also_stripped():
    assert strip_stray_html("This is <b>bold</b> and <i>italic</i>.") == "This is bold and italic."


def test_html_entities_decoded():
    assert strip_stray_html("Fish &amp; chips") == "Fish & chips"
    assert strip_stray_html("A&nbsp;gap") == "A gap"


def test_plain_text_with_no_html_is_a_no_op():
    assert strip_stray_html("Just plain text, nothing odd here.") == "Just plain text, nothing odd here."


def test_blank_and_none_pass_through_unchanged():
    assert strip_stray_html("") == ""
    assert strip_stray_html(None) is None


def test_excess_blank_lines_collapsed():
    result = strip_stray_html("<p>A</p>\n\n\n\n<p>B</p>")
    assert "\n\n\n" not in result


# ---------------------------------------------------------------------------
# Wired into Ticket's build pipeline (name, description, meeting point, includes/excludes,
# voucher remarks)
# ---------------------------------------------------------------------------

def test_ticket_voucher_remarks_never_carries_a_stray_closing_p_tag():
    data = minimal_ticket_data()
    data["voucher_remarks"] = "Free cancellation up to 30 days before arrival.</p>"
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    voucher = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "</p>" not in voucher
    assert "Free cancellation" in voucher


def test_ticket_name_never_carries_stray_html():
    data = minimal_ticket_data()
    data["ticket_name"] = "<p>Luxor Highlights Tour</p>"
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    assert result["main_ticket_payload"]["name"] == "Luxor Highlights Tour"


def test_ticket_meeting_point_never_carries_stray_html():
    data = minimal_ticket_data()
    data["meeting_point_summary"] = "Hotel Lobby<br>Main entrance"
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    datasheet = result["main_ticket_payload"]["datasheets"]["EN"]
    assert "<" not in datasheet["meetingPoint"]


def test_ticket_description_is_left_as_deliberate_html():
    """CONFIRMED BUG FIX (2026-08-26, product owner report: description formatting was "a bit
    wrong... sometimes the text just written as plain text"): a Ticket's description is a
    deliberate single HTML block per TicketDatasheetEN's own schema comment ("HTML, same
    day-by-day-style rules don't apply") and ai_extractor.py's own prompt ("Format:
    <p>paragraph(s)</p>") - stripping it here was the same class of bug as ClosedTour's
    description below, just for Tickets. This test previously (incorrectly) asserted it got
    stripped."""
    data = minimal_ticket_data()
    data["description"] = "<p>A lovely tour.</p>"
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    datasheet = result["main_ticket_payload"]["datasheets"]["EN"]
    assert datasheet["description"] == "<p>A lovely tour.</p>"


def test_ticket_includes_and_excludes_list_items_never_carry_stray_html():
    data = minimal_ticket_data()
    data["includes"] = ["<p>Hotel pickup</p>"]
    data["excludes"] = ["Entrance fees</p>"]
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    datasheet = result["main_ticket_payload"]["datasheets"]["EN"]
    assert datasheet["includes"] == ["Hotel pickup"]
    assert datasheet["excludes"] == ["Entrance fees"]


def test_entrance_fee_title_suffix_still_appends_correctly_onto_stripped_name():
    """strip_stray_html was added to the base name computation - confirm the entrance-fees
    notice (2026-08-24) still appends cleanly afterward rather than the two features colliding."""
    data = minimal_ticket_data()
    data["ticket_name"] = "<p>Luxor Highlights Tour</p>"
    data["entrance_fees_excluded"] = True
    result = build_ticket_payloads(make_ticket_pre_config(), data, _FakeAPI())
    assert result["main_ticket_payload"]["name"] == "Luxor Highlights Tour (Entrance fees not included)"


# ---------------------------------------------------------------------------
# Wired into ClosedTour's build pipeline (name, description, hotels, meeting point, policy
# remarks) - included/excluded must stay untouched, they're deliberately HTML
# ---------------------------------------------------------------------------

def test_closed_tour_name_never_carries_stray_html(fake_api_client):
    result = build_closed_tour_payloads(
        make_tour_pre_config(),
        minimal_tour_data(tour_name="<p>Test Nile Cruise</p>"),
        fake_api_client,
    )
    payload = result["main_tour_payload"]
    assert payload["name"] == "Test Nile Cruise"


def test_closed_tour_description_is_left_as_deliberate_day_by_day_html(fake_api_client):
    """CONFIRMED BUG FIX (2026-08-26, product owner report: "The day by day tour description is
    a bit wrong. Often one more space than needed and sometimes the text just written as plain
    text" - with a real example showing every <p>/<strong> day-header tag missing, everything
    run together as one plain-text block). Root cause: description was being run through
    strip_stray_html here, same as genuinely-plain-text fields - but ai_extractor.py's own
    ClosedTour prompt explicitly requires day-by-day HTML for this field
    (`<p><strong>Day 1: ...</strong></p><p>...</p><p><br></p>...`), the same kind of deliberate
    HTML included/excluded already got an exception for (see the test right below). This test
    previously (incorrectly) asserted description got stripped down to plain text."""
    day_by_day = ("<p><strong>Day 1: Arrival</strong></p><p>Welcome to the tour.</p><p><br></p>"
                 "<p><strong>Day 2: Departure</strong></p><p>Safe travels home.</p>")
    result = build_closed_tour_payloads(
        make_tour_pre_config(),
        minimal_tour_data(description=day_by_day),
        fake_api_client,
    )
    assert result["main_tour_payload"]["datasheets"]["EN"]["description"] == day_by_day


def test_closed_tour_included_excluded_are_left_as_deliberate_html(fake_api_client):
    """The one confirmed exception: Travel Compositor genuinely expects <ul><li> markup here."""
    result = build_closed_tour_payloads(
        make_tour_pre_config(),
        minimal_tour_data(included="<ul><li>Guide</li></ul>", excluded="<ul><li>Flights</li></ul>"),
        fake_api_client,
    )
    datasheet = result["main_tour_payload"]["datasheets"]["EN"]
    assert datasheet["included"] == "<ul><li>Guide</li></ul>"
    assert datasheet["excluded"] == "<ul><li>Flights</li></ul>"


# ---------------------------------------------------------------------------
# Hotel's shared _translation_list helper (descriptions/voucherRemarks/offer&supplement names)
# ---------------------------------------------------------------------------

def test_hotel_translation_list_strips_stray_html():
    result = builder._translation_list("<p>A beautiful beachfront hotel.</p>")
    assert result[0].description == "A beautiful beachfront hotel."


def test_hotel_translation_list_still_returns_empty_for_blank_text():
    assert builder._translation_list("") == []
    assert builder._translation_list(None) == []
