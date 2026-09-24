"""Regression tests for a real product-owner report (2026-09-24, two screenshots of a new
ClosedTour's review screen, verbatim caption): "creating a new product and i still get often this
code case"

The screenshots showed two distinct, real display bugs on the same review screen:

1. The Description field's day-by-day bold headings showed as literal "**Day 1: Cairo to Fayoum
   Oasis, Wadi El Rayan and Magic Lake (L, D)**" - visible asterisks instead of actual bold text.
2. The Hotels field showed its raw stored HTML outright: "<p><strong>Planned hotels for this
   tour...</strong></p><ul><li>Fayoum - Fayoum Eco Lodge...</li>...</ul>".

Root causes (both in ui_components.py / the two ClosedTour flows):

1. ui_components.editable_field's read-only preview (for widget="html_text_area"/"html_list_area")
   already converted stored HTML to plain text using **bold**/*italic*/"- " markers (so the EDIT
   box under the pencil button never shows raw HTML) - but then just escaped that marked-up plain
   text and dropped it into a raw <div>, with nothing to turn the markers back into visible
   formatting. Fixed with ui_components._plain_marked_to_display_html(), applied AFTER
   html.escape() so nothing from the original document/human text can ever inject real markup -
   only the markers this code produced itself can become a tag again.
2. Separately, the ClosedTour "Hotels" field (hotels_text) is stored as HTML by ai_extractor.py's
   own prompt template (a <p><strong>...</strong></p><ul><li>...</li></ul> block) - same shape as
   Description - but was wired up with the generic, no-conversion widget="text_area" instead of
   "html_text_area" at BOTH ClosedTour call sites (single-tour in app.py, batch in
   flows/multi_tour.py), so it never went through any HTML-to-plain conversion at all, in either
   read-only or edit mode. Fixed by switching both call sites to widget="html_text_area", the same
   widget Description already correctly used.

app.py/flows/*.py can't be imported directly in this test process (matches this suite's established
convention - see test_2026_09_01_medium_batch1_app_py.py's own docstring), so the widget-type fix
is checked as a source-text assertion; the display-marker fix is checked by exercising the real
ui_components functions directly (importable standalone).
"""
import html as _html_module
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# Bug 1: **bold**/*italic*/"- " markers must render as real markup, not literal characters
# ======================================================================
def test_plain_marked_to_display_html_renders_real_bold_not_literal_asterisks():
    from ui_components import _plain_marked_to_display_html
    escaped = _html_module.escape("**Day 1: Cairo to Fayoum Oasis, Wadi El Rayan and Magic Lake "
                                  "(L, D)**\n\nYour adventure begins with an early morning "
                                  "departure.")
    rendered = _plain_marked_to_display_html(escaped)
    assert "**" not in rendered
    assert ("<strong>Day 1: Cairo to Fayoum Oasis, Wadi El Rayan and Magic Lake (L, D)</strong>"
            in rendered)
    assert "Your adventure begins with an early morning departure." in rendered


def test_plain_marked_to_display_html_renders_real_bullets_for_dash_prefixed_lines():
    from ui_components import _plain_marked_to_display_html
    escaped = _html_module.escape(
        "**Planned hotels for this tour**\n\n"
        "- Fayoum – Fayoum Eco Lodge / Desert Lodge\n"
        "- Bahariya Oasis – Ahmed Safari Eco Lodge")
    rendered = _plain_marked_to_display_html(escaped)
    assert "- Fayoum" not in rendered
    assert "<li>Fayoum – Fayoum Eco Lodge / Desert Lodge</li>" in rendered
    assert "<li>Bahariya Oasis – Ahmed Safari Eco Lodge</li>" in rendered
    assert "<ul>" in rendered and "</ul>" in rendered
    assert "<strong>Planned hotels for this tour</strong>" in rendered


def test_plain_marked_to_display_html_only_adds_tags_it_generated_itself():
    """Injection-safety guard: text that looks like a tag, but was NOT produced by this
    function's own **/*/"- " markers, must still show as inert escaped text - the docstring's
    core safety claim (escape() runs first, this function only ever ADDS tags on top)."""
    from ui_components import _plain_marked_to_display_html
    escaped = _html_module.escape("Ordinary text <script>alert(1)</script> plus **real bold**.")
    rendered = _plain_marked_to_display_html(escaped)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>real bold</strong>" in rendered


def test_editable_field_readonly_preview_uses_the_display_renderer_for_html_widgets(monkeypatch):
    """End-to-end: editable_field's own read-only branch actually calls the new renderer (not
    just that the renderer works standalone) - reproduces the exact reported screenshot content."""
    import streamlit as st
    import ui_components

    rendered = []

    class _FakeCol:
        def __init__(self, sink):
            self._sink = sink

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(st, "columns", lambda spec: (_FakeCol(rendered), _FakeCol(rendered)))
    monkeypatch.setattr(st, "markdown", lambda *a, **k: rendered.append(a[0] if a else ""))
    monkeypatch.setattr(st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(st, "write", lambda *a, **k: None)
    monkeypatch.setattr(st, "button", lambda *a, **k: False)

    data = {"description": "<p><strong>Day 1: Cairo to Fayoum Oasis, Wadi El Rayan and Magic "
                            "Lake (L, D)</strong></p><p>Your adventure begins.</p>"}
    ui_components.editable_field("Description", data, "description", widget="html_text_area")

    preview_calls = [c for c in rendered if isinstance(c, str) and "background:#f6f6f6" in c]
    assert preview_calls, "expected the read-only preview div to have been rendered"
    preview_html = preview_calls[0]
    assert "**" not in preview_html
    assert ("<strong>Day 1: Cairo to Fayoum Oasis, Wadi El Rayan and Magic Lake (L, D)</strong>"
            in preview_html)


# ======================================================================
# Bug 2: ClosedTour's Hotels field must use the HTML-aware widget, same as Description
# ======================================================================
def test_closedtour_single_flow_hotels_field_uses_html_text_area_widget():
    src = _read("app.py")
    assert 'editable_field("Hotels", data, "hotels_text", widget="html_text_area"' in src
    # Guard against reverting to the old, bug-causing widget type for this field.
    assert 'editable_field("Hotels", data, "hotels_text", widget="text_area"' not in src


def test_closedtour_batch_flow_hotels_field_uses_html_text_area_widget():
    src = _read("flows", "multi_tour.py")
    assert 'editable_field("Hotels", data, "hotels_text", widget="html_text_area"' in src
    assert 'editable_field("Hotels", data, "hotels_text", widget="text_area"' not in src


def test_closedtour_description_field_still_uses_html_text_area_widget():
    """Description already used the correct widget before this fix - confirms this round didn't
    accidentally touch it while fixing Hotels."""
    for path in (("app.py",), ("flows", "multi_tour.py")):
        src = _read(*path)
        assert 'editable_field("Description", data, "description", widget="html_text_area"' in src
