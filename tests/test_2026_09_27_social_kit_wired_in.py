"""Social kit (2026-09-27): a Holiday Package ID in, three ready-to-post captions and a sized
JPG out, for whichever brand sells that inventory. Ported in as a self-contained module
(social_kit.py + social_kit_ui.py) - see README_SOCIAL_KIT.md and claude/
social-kit-wired-in-2026-09-27.md for the full story of what it is and where it was wired in.

This suite is a smoke test, not a full behavioral spec for a module we didn't write: it checks
the two things that matter for "wired in and working" - (1) both brands produce distinct,
correctly-priced, correctly-worded captions from the same package, and (2) every format/style
combination renders at the exact pixel size the network wants. It does NOT re-litigate the
module's own internal design choices (hook selection, upscale threshold, etc) - those are
documented and tested by the module itself; this suite would just be duplicating docstrings.
"""
import os

import pytest
from PIL import Image

import social_kit as sk

_SAMPLE = sk.Package(
    id="63989764",
    title="Kenya Safari & Zanzibar Beach",
    description="A two-week journey combining the savannah with a week of white sand.",
    days=14,
    nights=13,
    price=1899.0,
    currency="EUR",
    destinations=[{"name": "Nairobi", "country": "Kenya"}, {"name": "Zanzibar", "country": "Tanzania"}],
    themes=["Safari", "Beach"],
    gallery=["https://example.com/photo1.jpg"],
    departures=["2099-11-03", "2099-11-17"],
    flights=2,
    hotels=3,
)


def test_both_brands_are_registered_with_the_confirmed_language_and_currency():
    assert sk.BRANDS["momira"].lang == "en" and sk.BRANDS["momira"].currency == "EUR"
    assert sk.BRANDS["multiwander"].lang == "pl" and sk.BRANDS["multiwander"].currency == "PLN"


def test_momira_quotes_the_euro_price_unconverted():
    price = sk.format_price(_SAMPLE, sk.BRANDS["momira"], pln_rate=4.30)
    assert price == "1 899 €"


def test_multiwander_converts_to_pln_using_the_given_rate():
    price = sk.format_price(_SAMPLE, sk.BRANDS["multiwander"], pln_rate=4.30)
    assert price == "8 166 zł"


def test_multiwander_without_a_rate_shows_the_honest_euro_figure_rather_than_a_guess():
    price = sk.format_price(_SAMPLE, sk.BRANDS["multiwander"], pln_rate=None)
    assert price.endswith("€")


def test_captions_are_written_in_the_brands_own_language():
    en = sk.captions(_SAMPLE, sk.BRANDS["momira"].url, sk.BRANDS["momira"], pln_rate=4.30)
    pl = sk.captions(_SAMPLE, sk.BRANDS["multiwander"].url, sk.BRANDS["multiwander"], pln_rate=4.30)
    assert "savannah" in en["inspiracja"].lower() or "sound" in en["inspiracja"].lower()
    assert "sawannie" in pl["inspiracja"].lower()
    assert en["inspiracja"] != pl["inspiracja"]


def test_all_three_captions_stay_under_their_platform_character_limits():
    texts = sk.captions(_SAMPLE, sk.BRANDS["momira"].url, sk.BRANDS["momira"], pln_rate=4.30)
    assert len(texts["inspiracja"]) <= 2200
    assert len(texts["konkret"]) <= 2200
    assert len(texts["gbp"]) <= 1500


def test_hashtags_lead_with_destination_and_theme_then_the_brands_own_set():
    tags = sk.hashtags(_SAMPLE, sk.BRANDS["momira"])
    assert tags[0] == "#Kenya"
    assert "#MomiraTravel" in tags


@pytest.mark.parametrize("fmt", list(sk.FORMATS))
@pytest.mark.parametrize("style", list(sk.STYLES))
@pytest.mark.parametrize("brand_key", list(sk.BRANDS))
def test_every_format_style_brand_combination_renders_at_the_exact_network_size(fmt, style, brand_key):
    photo = Image.new("RGB", (900, 720), (80, 140, 160))
    image = sk.render(_SAMPLE, fmt, style, photo, "center", 1.0, sk.BRANDS[brand_key], 4.30)
    assert image.size == sk.FORMATS[fmt][:2]
    jpeg_bytes = sk.to_jpeg(image)
    assert jpeg_bytes[:2] == b"\xff\xd8"  # a real JPEG, not an empty/corrupt buffer


def test_gallery_falls_back_to_finding_image_urls_by_shape_when_the_named_fields_are_empty():
    """CONFIRMED REAL GAP (2026-09-27): Chris's first live test hit exactly the risk
    claude/multiwander-tc-api-briefing-2026-09-05.md flagged in advance - Holiday Package's
    image field name was never confirmed against a real response, and the named-field guess
    (imageUrls/images/gallery) came up empty on a package that does have photographs. This
    checks the field-name-agnostic fallback that was added in response: it finds photographs by
    shape (an http URL ending in a normal image extension) anywhere in the nested response,
    however deeply the real field is nested."""
    info = {
        "title": "Test package",
        "content": {"media": {
            "hero": "https://cdn.example.com/photos/hero-1234.jpg",
            "thumbs": ["https://cdn.example.com/photos/thumb1.webp", "not a url", "https://cdn.example.com/desc.txt"],
        }},
    }
    pack = sk.normalise("1", info, {}, {})
    assert pack.gallery == [
        "https://cdn.example.com/photos/hero-1234.jpg",
        "https://cdn.example.com/photos/thumb1.webp",
    ]


def test_a_named_gallery_field_when_present_is_used_instead_of_the_fallback():
    pack = sk.normalise("2", {"title": "x", "imageUrls": ["https://cdn.example.com/a.jpg"]}, {}, {})
    assert pack.gallery == ["https://cdn.example.com/a.jpg"]


def test_package_keeps_the_raw_responses_for_on_the_spot_diagnosis():
    info, detail, calendar = {"title": "x"}, {"y": 1}, {"z": 2}
    pack = sk.normalise("3", info, detail, calendar)
    assert pack.raw == {"info": info, "detail": detail, "calendar": calendar}


def test_the_bundled_font_files_are_present_so_rendering_never_falls_back_to_pil_default():
    font_dir = os.path.join(os.path.dirname(os.path.abspath(sk.__file__)), "fonts")
    assert os.path.exists(os.path.join(font_dir, "mw-bold.ttf"))
    assert os.path.exists(os.path.join(font_dir, "mw-regular.ttf"))


def test_app_wires_social_kit_in_as_its_own_tool_alongside_package_rollover():
    """Confirms the wiring app.py actually needs, read as source text like every other
    Streamlit-coupled check in this suite (importing app.py directly runs its top-level
    Streamlit calls)."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_source = open(os.path.join(repo_dir, "app.py"), encoding="utf-8").read()

    assert "import social_kit_ui" in app_source
    assert 'TOOL_SOCIALKIT = "📱 Social Kit"' in app_source
    assert "social_kit_ui.render_social_kit()" in app_source


def test_the_app_screen_never_mentions_multiwander_or_offers_a_brand_choice():
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-09-27, verbatim): "in the app we use only momira
    travel with english and euro. No need to mention multiwander.com there." social_kit.py's own
    BRANDS dict keeps MultiWander for reuse as a library (see its module docstring and
    README_SOCIAL_KIT.md's "Using it as a library" section) - only the Streamlit screen must
    stay Momira-only, so this checks social_kit_ui.py specifically, not social_kit.py."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui_source = open(os.path.join(repo_dir, "social_kit_ui.py"), encoding="utf-8").read()

    # Strip the module docstring (lines 1-18), which quotes "multiwander" and "MultiWander" only
    # to document why the code below it does not - the code itself must be clean of both.
    code_only = ui_source.split('"""', 2)[-1]

    assert "multiwander" not in code_only.lower()
    assert 'sk.BRANDS["momira"]' in ui_source
    assert '"Brand"' not in code_only  # the brand-picker selectbox's label, specifically
    assert "PLN" not in code_only and "zł" not in code_only  # no currency-conversion input either
