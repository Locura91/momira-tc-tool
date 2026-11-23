"""Rollover URL: the slug must not DELETE non-ASCII letters.

CONFIRMED REAL BUG (operator, 2026-11-23): "I tried to click on start rollover in Travel
Compositor, and now it falls back to the main page of Travel Compositor instead of opening
the URL and opening the package with the according button, book without any changes."

Root cause: _make_slug applied `[^a-z0-9\\s-]` to an un-normalised string, so every
non-ASCII letter was removed outright instead of folded to its base letter - "Zürich"
became "zrich". momira.travel cannot resolve such a slug and redirects to its homepage,
which is precisely the reported symptom. Momira is a German-market brand, so umlauts in
package titles are the common case, not an edge case.
"""
import re

from package_rollover_tool import (
    _make_slug,
    _momira_bare_url,
    _rollover_target_url,
)


def test_umlauts_fold_to_base_letters_instead_of_vanishing():
    assert _make_slug("Zürich & Zermatt Explorer") == "zurich-zermatt-explorer"
    assert _make_slug("Köln Düsseldorf Tour") == "koln-dusseldorf-tour"
    assert _make_slug("Safari Kenia 5 Nächte") == "safari-kenia-5-nachte"


def test_accented_latin_also_folds():
    assert _make_slug("Café de Paris Reise") == "cafe-de-paris-reise"
    assert _make_slug("Málaga Señorita") == "malaga-senorita"


def test_plain_ascii_titles_are_unchanged_by_the_fix():
    """The packages that already worked must keep producing the exact same slug."""
    assert _make_slug("Dubai & Abu Dhabi 7 Nights") == "dubai-abu-dhabi-7-nights"
    assert _make_slug("Thailand: Bangkok, Phuket & Koh Lipe") == "thailand-bangkok-phuket-koh-lipe"


def test_no_slug_ever_contains_a_non_ascii_byte():
    """Whatever the title, the slug has to be URL-safe ASCII - a stray multi-byte
    character is what produced the un-resolvable URL in the first place."""
    for title in ["Zürich", "Köln", "Nächte", "Café", "Señor", "Ærø", "Łódź"]:
        slug = _make_slug(title)
        assert re.fullmatch(r"[a-z0-9-]*", slug), f"{title!r} -> {slug!r} is not URL-safe"


def test_bare_url_has_no_guessed_slug():
    assert _momira_bare_url("59582825") == "https://momira.travel/en/idea/59582825/"


def test_target_url_prefers_a_momira_url_from_the_api():
    """The API's own URL is authoritative - no slug guessing needed."""
    info = {"ideaUrl": "https://momira.travel/en/idea/59582825/the-real-canonical-slug"}
    assert _rollover_target_url("59582825", "Some Guessed Title", info) == \
        "https://momira.travel/en/idea/59582825/the-real-canonical-slug"


def test_target_url_ignores_a_non_momira_api_url():
    """The browser extension's manifest matches momira.travel and nothing else, so an
    online.travelcompositor.com URL would open a page the extension never runs on - a
    silent downgrade. It must be rejected in favour of the momira.travel form."""
    info = {"ideaUrl": "https://online.travelcompositor.com/idea/59582825"}
    url = _rollover_target_url("59582825", "Zürich Explorer", info)
    assert url.startswith("https://momira.travel/")
    assert url == "https://momira.travel/en/idea/59582825/zurich-explorer"


def test_target_url_falls_back_to_bare_when_there_is_no_title():
    assert _rollover_target_url("59582825", "", {}) == \
        "https://momira.travel/en/idea/59582825/"
