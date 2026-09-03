"""Three real product-owner reports (2026-09-03), all about state leaking from one Ticket/
ClosedTour batch into the NEXT one:

1) "if I start a new batch for creating a new service, please allow to change the currency as
   this can be always vary" - Ticket's tk_cfg_currency and ClosedTour's cfg_currency lock
   ("once a currency has been set, it can never be changed") was meant to protect a single
   in-progress ticket/tour's own Modalities from an inconsistent currency mid-way through - but
   it never got cleared when a batch actually finished and a brand-new one started, so every
   batch after the first was permanently stuck with whatever currency the first one used, even
   from a completely different supplier/rate sheet.

2) "if I start a new batch, this cant be seen: I have not included images to the new servcie" -
   the per-item stock-photo picker (render_closable_image_section, keyed by the item's
   POSITIONAL index in the queue, e.g. mt_pixabay_{idx}_closed) remembers "closed, N image(s)
   added" - but two of the three Ticket-batch reset points only swept the generic
   SHARED_WIDGET_STATE_PREFIXES, never this flow's own "mt_"-prefixed keys (unlike the skip
   button a few lines below, which already did this correctly) - so a fresh batch's item 0
   inherited the PREVIOUS batch's item 0's "1 image(s) added" state and showed it as already
   done, even though nothing had been added to the new service at all.

3) "when searching for Coordinates, use the name of the City and then the Country. Example:
   Phuket, Thailand." - a bare city name is often ambiguous for a free-text geocoder; appending
   the country (resolved via Travel Compositor's own destination data, the same source already
   used for the Indonesia/Vietnam holiday rules) disambiguates it the way a human would
   naturally search.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
own wiring is verified by reading its source text, per this suite's established pattern.
builder.py and geocoding_client.py CAN be imported directly and are tested with real unit tests.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULE_BUILD = "2026-09-03-new-batch-currency-image-state-and-geo-country"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1) Currency unlocks on a genuinely new batch
# ======================================================================
def _multi_ticket_flow_source(src):
    start = src.index("def render_multi_ticket_flow(")
    end = src.index("\ndef ", start + 50)
    return src[start:end]


def test_ticket_start_a_new_batch_reopens_step3_and_clears_currency():
    window_all = _multi_ticket_flow_source(_read_app_py())
    idx = window_all.index('if st.button("🆕 Start a new batch"):')
    window = window_all[idx:idx + 2400]
    assert 'st.session_state.tk_step2_confirmed = False' in window
    assert 'st.session_state.pop("tk_cfg_currency", None)' in window
    # Still sweeps the mt_-prefixed widget state (see test 2 below) - both fixes landed on the
    # same button, this just confirms the currency half is really there too.
    assert '_clear_batch_widget_state(["mt_"] + SHARED_WIDGET_STATE_PREFIXES)' in window


def test_ticket_change_action_supplier_also_clears_currency():
    src = _read_app_py()
    idx = src.index('if st.button("🔄 Change action / supplier", key="tk_change_action"):')
    window = src[idx:idx + 900]
    assert 'st.session_state.pop("tk_cfg_currency", None)' in window


def test_closed_tour_reset_mct_state_reopens_step2_and_clears_currency():
    src = _read_app_py()
    idx = src.index("def _reset_mct_state():")
    window = src[idx:idx + 2600]
    assert 'st.session_state.step2_confirmed = False' in window
    assert 'st.session_state.pop("cfg_currency", None)' in window


def test_closed_tour_change_action_supplier_also_clears_currency():
    src = _read_app_py()
    idx = src.index('if st.button("🔄 Change action / supplier"):')
    window = src[idx:idx + 1100]
    assert 'st.session_state.pop("cfg_currency", None)' in window


# ======================================================================
# 2) Per-item image-picker state doesn't leak into the next batch
# ======================================================================
def test_ticket_cancel_batch_sweeps_mt_prefixed_widget_state():
    window_all = _multi_ticket_flow_source(_read_app_py())
    idx = window_all.index('if st.button("🔙 Cancel this batch - return to single-Ticket flow"')
    window = window_all[idx:idx + 1300]
    assert '_clear_batch_widget_state(["mt_"] + SHARED_WIDGET_STATE_PREFIXES)' in window


def test_ticket_start_a_new_batch_sweeps_mt_prefixed_widget_state():
    window_all = _multi_ticket_flow_source(_read_app_py())
    idx = window_all.index('if st.button("🆕 Start a new batch"):')
    window = window_all[idx:idx + 1600]
    assert '_clear_batch_widget_state(["mt_"] + SHARED_WIDGET_STATE_PREFIXES)' in window


# ======================================================================
# 3) Geocoding queries append the country
# ======================================================================
def test_build_place_query_appends_country_from_a_code():
    from geocoding_client import build_place_query
    assert build_place_query("Phuket", "TH") == "Phuket, Thailand"


def test_build_place_query_appends_country_from_a_full_name():
    from geocoding_client import build_place_query
    assert build_place_query("Praslin", "Seychelles") == "Praslin, Seychelles"


def test_build_place_query_falls_back_to_bare_place_name_with_no_country():
    from geocoding_client import build_place_query
    assert build_place_query("Phuket", None) == "Phuket"
    assert build_place_query("Phuket", "") == "Phuket"


def test_build_place_query_does_not_double_up_an_already_included_country():
    from geocoding_client import build_place_query
    assert build_place_query("Praslin, Seychelles", "SC") == "Praslin, Seychelles"


def test_build_place_query_handles_a_blank_place_name():
    from geocoding_client import build_place_query
    assert build_place_query("", "TH") == ""


def test_country_display_name_expands_known_codes():
    from geocoding_client import country_display_name
    assert country_display_name("SC") == "Seychelles"
    assert country_display_name("TH") == "Thailand"


def test_country_display_name_passes_through_an_unknown_two_letter_code():
    from geocoding_client import country_display_name
    assert country_display_name("ZZ") == "ZZ"


def test_ticket_builder_uses_the_supplier_countrys_destination_data_for_the_geo_query():
    import builder
    src_fn = builder.build_ticket_payloads.__code__
    # Confirms build_place_query is actually wired into the compiled function's constants/names,
    # i.e. the call really happens inside build_ticket_payloads and wasn't left unused.
    assert "build_place_query" in src_fn.co_names


def test_app_py_geo_search_default_helper_exists_and_is_used_at_all_four_sites():
    src = _read_app_py()
    assert "def _geo_search_default(client, place_name):" in src
    assert 'mt_geo = geocode(_geo_search_default(client, mt_city))' in src
    assert 'mt_geo_query = st.text_input("Search for a location", value=_geo_search_default(client, mt_city)' in src
    assert 'tk_geo_search_query = st.text_input("Search for a location", value=_geo_search_default(client, data.get("city", ""))' in src
    assert 'tk_geo_search_query2 = st.text_input("Search for a location", value=_geo_search_default(client, data.get("city", ""))' in src
