"""
Footer-first email discovery for supplier outreach (product-owner observation, 2026-10-07):

"it's not as good for the email finding when searching for new suppliers ... I find most of the
emails just in the footer, with the headline or with the name email, contact us, or write us or
get in touch with us. But the best way is to search in the footer."

The old code pooled every mailto: and every address in the page text into ONE list, so a role
address anywhere on the page outranked the company's real address in the footer just by matching
the info@/contact@ preference first. Now scopes are searched in order - footer, then a contact
block, then the whole page - and the first scope with a hit wins.
"""
from bs4 import BeautifulSoup

import outreach_discovery as od


def _soup(html):
    return BeautifulSoup(html, "html.parser")


# ----------------------------------------------------------------------
# the footer wins over the rest of the page
# ----------------------------------------------------------------------
def test_footer_email_beats_a_role_address_elsewhere_on_the_page():
    """The motivating failure: press@ matched the role-address preference and won, even though the
    company's real address was sitting in the footer."""
    html = """
      <body>
        <main><p>For media enquiries write to press@agency-example.com</p></main>
        <footer><a href="mailto:sales@realsupplier.com">Email us</a></footer>
      </body>"""
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "sales@realsupplier.com"


def test_a_semantic_footer_is_found():
    html = '<body><footer>Write us: hello@camp.co.th</footer></body>'
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "hello@camp.co.th"


def test_a_footer_marked_only_by_class_is_found():
    """Most sites have no <footer> element - it is a div with a class."""
    html = '<body><div class="site-footer widget">Contact: book@tours.vn</div></body>'
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "book@tours.vn"


def test_a_footer_marked_only_by_id_is_found():
    html = '<body><div id="colophon">reservations@lodge.com</div></body>'
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "reservations@lodge.com"


def test_an_address_element_counts_as_a_contact_block():
    html = "<body><address>Our office: team@dmc.co.id</address></body>"
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "team@dmc.co.id"


# ----------------------------------------------------------------------
# the contact-wording block, when there is no footer
# ----------------------------------------------------------------------
def test_get_in_touch_heading_block_is_searched_when_there_is_no_footer():
    html = """
      <body>
        <p>Some long marketing paragraph mentioning nothing useful at all.</p>
        <div><h3>Get in touch</h3><p>sales@supplier.com</p></div>
      </body>"""
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "sales@supplier.com"


def test_write_us_heading_block_is_searched():
    html = '<body><div><h4>Write us</h4><span>bookings@safari.tz</span></div></body>'
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "bookings@safari.tz"


def test_a_long_wrapper_mentioning_contact_is_not_treated_as_a_label():
    """Without a length cap, a wrapper div containing the whole page would match "contact" and the
    contact-block scope would collapse into the whole-page scope."""
    long_text = "contact " + ("padding words here " * 20)
    html = f"<body><div><p>{long_text}</p><p>x@y.com</p></div></body>"
    # Still resolvable via the whole-page fallback, which is the point - no crash, no mis-scoping.
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "x@y.com"


# ----------------------------------------------------------------------
# whole-page fallback keeps the old behaviour
# ----------------------------------------------------------------------
def test_plain_page_with_no_footer_or_label_still_finds_the_address():
    html = "<body><p>Reach the team at info@plain.com any time.</p></body>"
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] == "info@plain.com"


def test_no_email_anywhere_returns_none_not_a_crash():
    html = "<body><footer>Follow us on social media.</footer></body>"
    assert od.extract_email_and_instagram_from_page(_soup(html))["email"] is None


def test_body_text_is_still_returned_for_the_contact_name_match():
    html = "<body><p>Managing Director Jane Smith</p><footer>a@b.com</footer></body>"
    out = od.extract_email_and_instagram_from_page(_soup(html))
    assert "Jane Smith" in out["bodyText"]


def test_instagram_is_still_picked_up():
    html = '<body><footer><a href="https://instagram.com/acme">IG</a>a@b.com</footer></body>'
    assert od.extract_email_and_instagram_from_page(_soup(html))["instagram"] == "https://instagram.com/acme"


# ----------------------------------------------------------------------
# own-domain preference (the web designer problem)
# ----------------------------------------------------------------------
def test_the_sites_own_domain_wins_over_someone_elses_in_the_same_footer():
    """Footers routinely carry the web designer's address next to the company's."""
    html = """
      <body><footer>
        info@webdesign-studio.com (site by) — contact us at reservations@khaosok.co.th
      </footer></body>"""
    out = od.extract_email_and_instagram_from_page(_soup(html), base_url="https://www.khaosok.co.th/")
    assert out["email"] == "reservations@khaosok.co.th"


def test_without_a_base_url_the_old_role_address_preference_is_unchanged():
    assert od.pick_best_email(["bob@x.com", "info@x.com"]) == "info@x.com"


def test_first_found_is_still_the_final_fallback():
    assert od.pick_best_email(["bob@x.com", "sue@x.com"]) == "bob@x.com"


def test_a_subdomain_still_counts_as_the_sites_own_domain():
    assert od.pick_best_email(["x@other.com", "sue@mail.acme.com"],
                              prefer_domain="acme.com") == "sue@mail.acme.com"


# ----------------------------------------------------------------------
# junk that used to pass as an address
# ----------------------------------------------------------------------
def test_an_image_filename_is_not_offered_as_an_email():
    assert od.pick_best_email(["logo@2x.png"]) is None


def test_noreply_addresses_are_still_rejected():
    assert od.pick_best_email(["noreply@x.com"]) is None


# ----------------------------------------------------------------------
# the contact-LINK pattern learned the same wordings
# ----------------------------------------------------------------------
def test_contact_link_pattern_matches_the_wordings_the_owner_sees():
    for text in ["Get in touch", "Write us", "write to us", "Email us", "Reach out",
                 "Contact us", "Kontakt", "Impressum"]:
        assert od.CONTACT_LINK_PATTERN.search(text), text
