"""
CONFIRMED PRODUCT-OWNER REQUEST (2026-08-27): "group the possible and smart themes with the
correct Places already. The goal shall be, the human sends on a daily basis place by place from
each country a correct Mail to the best fitting themes. If we group them: we match automatically
the needed service and we limitate the useless search."

Covers outreach_scope.py's theme-to-place matching: _clean() preserving the new "places" list
field on themes, group_themes_by_place() sorting themes under their real places (with a
country-wide bucket for the rest), planned_searches() building only the explicit (place, theme)
pairs a human actually ticked instead of a blind cross product, and add_theme() accepting an
optional places list for hand-added themes.

No test file previously existed for outreach_scope.py.
"""
import outreach_scope as osc


# ---------------------------------------------------------------------------
# _clean()
# ---------------------------------------------------------------------------

def test_clean_preserves_places_list_on_themes():
    themes = [{"name": "Nile Cruise", "why": "iconic", "places": ["Luxor", "Aswan"]}]
    cleaned = osc._clean(themes, list_keys=("places",))
    assert cleaned == [{"name": "Nile Cruise", "why": "iconic", "places": ["Luxor", "Aswan"]}]


def test_clean_defaults_missing_places_key_to_empty_list():
    themes = [{"name": "Custom Private Tour", "why": "flexible"}]
    cleaned = osc._clean(themes, list_keys=("places",))
    assert cleaned[0]["places"] == []


def test_clean_dedupes_and_trims_places_list():
    themes = [{"name": "Desert Safari", "why": "dunes",
               "places": [" Hurghada ", "hurghada", "Marsa Alam", ""]}]
    cleaned = osc._clean(themes, list_keys=("places",))
    assert cleaned[0]["places"] == ["Hurghada", "Marsa Alam"]


def test_clean_ignores_non_list_places_value():
    themes = [{"name": "Snorkeling", "why": "reef", "places": "not a list"}]
    cleaned = osc._clean(themes, list_keys=("places",))
    assert cleaned[0]["places"] == []


def test_clean_without_list_keys_still_works_as_before():
    places = [{"name": "Cairo", "region": "Greater Cairo", "why": "pyramids"}]
    cleaned = osc._clean(places)
    assert cleaned == [{"name": "Cairo", "region": "Greater Cairo", "why": "pyramids"}]


def test_clean_drops_duplicate_names_case_insensitively():
    themes = [{"name": "Snorkeling", "why": "a", "places": ["Hurghada"]},
              {"name": "snorkeling", "why": "b", "places": ["Sharm El Sheikh"]}]
    cleaned = osc._clean(themes, list_keys=("places",))
    assert len(cleaned) == 1
    assert cleaned[0]["why"] == "a"


# ---------------------------------------------------------------------------
# group_themes_by_place()
# ---------------------------------------------------------------------------

_PLACES = [
    {"name": "Luxor", "region": "Upper Egypt", "why": "temples"},
    {"name": "Cairo", "region": "Greater Cairo", "why": "pyramids"},
    {"name": "Hurghada", "region": "Red Sea", "why": "diving"},
]
_THEMES = [
    {"name": "Nile Cruise", "why": "iconic", "places": ["Luxor"]},
    {"name": "Snorkeling", "why": "reef", "places": ["Hurghada"]},
    {"name": "Pyramid Tour", "why": "classic", "places": ["Cairo"]},
    {"name": "Custom Private Tour", "why": "flexible", "places": []},
    {"name": "Airport Transfer", "why": "logistics"},  # no "places" key at all
]


def test_group_themes_by_place_matches_each_theme_to_its_place():
    per_place, countrywide = osc.group_themes_by_place(_PLACES, _THEMES)
    by_name = {slot["place"]["name"]: [t["name"] for t in slot["themes"]] for slot in per_place}
    assert by_name["Luxor"] == ["Nile Cruise"]
    assert by_name["Cairo"] == ["Pyramid Tour"]
    assert by_name["Hurghada"] == ["Snorkeling"]


def test_group_themes_by_place_never_puts_snorkeling_under_cairo():
    per_place, _countrywide = osc.group_themes_by_place(_PLACES, _THEMES)
    cairo_themes = next(slot["themes"] for slot in per_place if slot["place"]["name"] == "Cairo")
    assert "Snorkeling" not in [t["name"] for t in cairo_themes]


def test_group_themes_by_place_puts_empty_and_missing_places_in_countrywide():
    _per_place, countrywide = osc.group_themes_by_place(_PLACES, _THEMES)
    names = {t["name"] for t in countrywide}
    assert names == {"Custom Private Tour", "Airport Transfer"}


def test_group_themes_by_place_puts_unmatched_place_name_in_countrywide():
    # A theme naming a place that isn't in the places list (AI slip, or a place never added)
    # must not vanish - it lands in countrywide instead.
    themes = [{"name": "Oasis Tour", "why": "desert", "places": ["Siwa"]}]
    per_place, countrywide = osc.group_themes_by_place(_PLACES, themes)
    assert all(not slot["themes"] for slot in per_place)
    assert [t["name"] for t in countrywide] == ["Oasis Tour"]


def test_group_themes_by_place_theme_sold_in_several_places_appears_under_each():
    themes = [{"name": "Desert Safari", "why": "dunes", "places": ["Cairo", "Hurghada"]}]
    per_place, countrywide = osc.group_themes_by_place(_PLACES, themes)
    by_name = {slot["place"]["name"]: [t["name"] for t in slot["themes"]] for slot in per_place}
    assert by_name["Cairo"] == ["Desert Safari"]
    assert by_name["Hurghada"] == ["Desert Safari"]
    assert countrywide == []


def test_group_themes_by_place_preserves_place_order():
    per_place, _countrywide = osc.group_themes_by_place(_PLACES, _THEMES)
    assert [slot["place"]["name"] for slot in per_place] == ["Luxor", "Cairo", "Hurghada"]


def test_group_themes_by_place_handles_empty_inputs():
    assert osc.group_themes_by_place([], []) == ([], [])
    per_place, countrywide = osc.group_themes_by_place(_PLACES, [])
    assert countrywide == []
    assert all(slot["themes"] == [] for slot in per_place)


# ---------------------------------------------------------------------------
# planned_searches()
# ---------------------------------------------------------------------------

def test_planned_searches_builds_one_entry_per_explicit_pair():
    pairs = [("Luxor", "Nile Cruise"), ("Cairo", "")]
    planned = osc.planned_searches("Egypt", pairs)
    assert planned == [
        {"country": "Egypt", "city": "Luxor", "keyword": "Nile Cruise"},
        {"country": "Egypt", "city": "Cairo", "keyword": ""},
    ]


def test_planned_searches_countrywide_theme_has_empty_city():
    planned = osc.planned_searches("Egypt", [("", "Airport Transfer")])
    assert planned == [{"country": "Egypt", "city": "", "keyword": "Airport Transfer"}]


def test_planned_searches_never_builds_a_blind_cross_product():
    # Two places and two themes ticked, but only two of the four possible combinations were
    # actually offered as pairs (the other two would be nonsensical, e.g. Snorkeling in Cairo) -
    # planned_searches must not silently expand back to the full 2x2 grid.
    pairs = [("Cairo", "Pyramid Tour"), ("Hurghada", "Snorkeling")]
    planned = osc.planned_searches("Egypt", pairs)
    assert len(planned) == 2


def test_planned_searches_drops_duplicate_pairs():
    pairs = [("Cairo", "Pyramid Tour"), ("Cairo", "Pyramid Tour")]
    assert len(osc.planned_searches("Egypt", pairs)) == 1


def test_planned_searches_skips_fully_empty_pair():
    assert osc.planned_searches("Egypt", [("", "")]) == []


def test_planned_searches_empty_without_country():
    assert osc.planned_searches("", [("Cairo", "Pyramid Tour")]) == []


def test_planned_searches_empty_pairs_list():
    assert osc.planned_searches("Egypt", []) == []


# ---------------------------------------------------------------------------
# add_theme()
# ---------------------------------------------------------------------------

def test_add_theme_stores_places_list():
    osc.save_scope("Testland", {"places": [{"name": "Capital City", "why": "central"}],
                                "themes": []})
    assert osc.add_theme("Testland", "City Tour", "walkable", places=["Capital City"])
    scope = osc.get_cached_scope("Testland")
    added = next(t for t in scope["themes"] if t["name"] == "City Tour")
    assert added["places"] == ["Capital City"]


def test_add_theme_defaults_to_countrywide_when_no_places_given():
    osc.save_scope("Testland2", {"places": [], "themes": []})
    assert osc.add_theme("Testland2", "Airport Transfer")
    scope = osc.get_cached_scope("Testland2")
    added = next(t for t in scope["themes"] if t["name"] == "Airport Transfer")
    assert added["places"] == []


def test_add_theme_then_group_themes_by_place_finds_it():
    osc.save_scope("Testland3", {"places": [{"name": "Riverside", "why": "scenic"}],
                                 "themes": []})
    osc.add_theme("Testland3", "Boat Trip", "on the river", places=["Riverside"])
    scope = osc.get_cached_scope("Testland3")
    per_place, countrywide = osc.group_themes_by_place(scope["places"], scope["themes"])
    assert [t["name"] for t in per_place[0]["themes"]] == ["Boat Trip"]
    assert countrywide == []
