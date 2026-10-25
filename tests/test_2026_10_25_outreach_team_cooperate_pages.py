"""
Outreach email discovery — "Our team" and "Cooperate with us" page fallbacks
(product-owner observation, 2026-10-25):

"We must check a mail address from the footer, from a page if called contact us and from
a page called our team and from a page called cooperate with us."

find_contact_and_terms_links already returned one contact + one terms link.  This extends
it to also return a team/cooperate page so scrape_website_contact has a third place to look
when the primary contact page yields nothing.
"""
from bs4 import BeautifulSoup

import outreach_discovery as od


def _soup(html):
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# _TEAM_PAGE_PATTERN recognises the relevant link wordings
# ---------------------------------------------------------------------------
def test_team_pattern_matches_our_team():
    assert od._TEAM_PAGE_PATTERN.search("Our team")

def test_team_pattern_matches_team_bare():
    assert od._TEAM_PAGE_PATTERN.search("team")

def test_team_pattern_matches_cooperate_with_us():
    assert od._TEAM_PAGE_PATTERN.search("Cooperate with us")

def test_team_pattern_matches_cooperate_bare():
    assert od._TEAM_PAGE_PATTERN.search("cooperate")

def test_team_pattern_matches_partner_with_us():
    assert od._TEAM_PAGE_PATTERN.search("Partner with us")

def test_team_pattern_matches_work_with_us():
    assert od._TEAM_PAGE_PATTERN.search("Work with us")

def test_team_pattern_matches_about_us():
    assert od._TEAM_PAGE_PATTERN.search("About us")

def test_team_pattern_matches_b2b():
    assert od._TEAM_PAGE_PATTERN.search("b2b")

def test_team_pattern_matches_for_operators():
    assert od._TEAM_PAGE_PATTERN.search("for tour operators")

def test_team_pattern_does_not_match_random_word():
    assert not od._TEAM_PAGE_PATTERN.search("booking")


# ---------------------------------------------------------------------------
# find_contact_and_terms_links returns team page as second priority
# ---------------------------------------------------------------------------
def test_team_page_is_returned_when_contact_is_absent():
    html = """<body>
      <nav>
        <a href="/team">Our team</a>
        <a href="/terms">Terms</a>
      </nav>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert "/team" in links[0]


def test_team_page_is_returned_after_contact_page():
    html = """<body>
      <nav>
        <a href="/contact">Contact us</a>
        <a href="/our-team">Our team</a>
      </nav>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert len(links) == 2
    assert "/contact" in links[0]
    assert "/our-team" in links[1]


def test_cooperate_page_is_returned_as_team_slot():
    html = """<body>
      <nav>
        <a href="/contact">Contact us</a>
        <a href="/cooperate">Cooperate with us</a>
        <a href="/terms">Terms</a>
      </nav>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert any("/cooperate" in l for l in links)
    # cooperate comes before terms
    cooperate_idx = next(i for i, l in enumerate(links) if "/cooperate" in l)
    terms_idx = next(i for i, l in enumerate(links) if "/terms" in l)
    assert cooperate_idx < terms_idx


def test_all_three_tiers_are_returned():
    html = """<body>
      <nav>
        <a href="/contact">Contact us</a>
        <a href="/team">Our team</a>
        <a href="/terms">Terms &amp; Conditions</a>
      </nav>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert len(links) == 3


def test_off_site_team_link_is_ignored():
    html = """<body>
      <a href="https://other.com/team">Our team</a>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert links == []


def test_no_duplicate_when_contact_also_matches_team():
    """A link with text 'Contact our team' matches CONTACT_LINK_PATTERN first and
    should NOT also be added as the team link."""
    html = """<body>
      <a href="/contact-team">Contact our team</a>
    </body>"""
    links = od.find_contact_and_terms_links(_soup(html), "https://example.com/")
    assert len(links) == 1
