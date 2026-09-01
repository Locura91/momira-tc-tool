"""Tests for the ClosedTour child-discount visibility fix (2026-08-31).

CONFIRMED REAL GAP (product owner question, 2026-08-31): "creating a new ClosedTour, when a
child is allowed, so far I can only add a child, but there is nothing seen how much discount
per single, double, triple or quadruple room type the child has. So far no child discount is
seen at all."

Investigation confirmed TWO separate things:

  1. Single/Double child discount: genuine Travel Compositor platform limitation, NOT fixable -
     the real, live-used price list schema (PriceListPriceVO in schemas.py) has no field for it
     at all. Nothing to build here.
  2. Triple/Quadruple child discount: WAS already being computed and sent to Travel Compositor
     (builder.normalize_price_list()'s fallback_child_discount_percentage, applied automatically
     from the document-wide extracted child_discount_percentage - see build_closed_tour_payloads,
     builder.py ~line 1751) but had ZERO visibility on any review screen - not shown, not
     editable, effect on the actual price rows invisible.

The fix: ui_components.render_child_discount_editor() - shows/edits the document-wide
child_discount_percentage and previews exactly what will be sent on each Triple/Quadruple row,
using the SAME builder.normalize_price_list() call build_closed_tour_payloads uses right before
publish, so the preview can never drift from what's actually sent. Wired into all three
ClosedTour creation screens in app.py (single-tour "ct_single", and the two multi-modality flows
"mm_"/"mct_mod_"), right after the existing render_extra_child_notice() call.

These tests can't drive the Streamlit widget itself (no Streamlit test harness in this suite -
see test_2026_08_26_extra_child_allowed.py's own tests, which test compute_extra_child_plan
directly for the same reason). Instead they cover: (a) the exact real-payload behavior the
widget's preview claims to mirror, via the actual build_closed_tour_payloads pipeline, and
(b) that the widget is genuinely wired into all three screens, by reading app.py's source text
(not importing it - app.py runs top-level Streamlit calls on import and can't be safely
imported in a test process).
"""
import os

from builder import build_closed_tour_payloads, normalize_price_list, sold_occupancies
from ui_components import render_child_discount_editor
from test_builder_closed_tour import make_pre_config, minimal_extracted_data


_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


_TRIPLE_QUAD_PRICE_LIST = [{
    "startDate": "2027-01-01", "endDate": "2027-12-31",
    "price": {
        "singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
        "triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 200},
    },
}]


def test_document_wide_percentage_flows_into_the_real_published_payload(fake_api_client):
    """The exact claim the widget's caption makes ('will be sent to Travel Compositor on
    publish') - proven against the REAL builder pipeline, not a re-implementation."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=_TRIPLE_QUAD_PRICE_LIST, child_discount_percentage=15),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert price["tripleChildPercentageDiscount"] == 15.0
    assert price["quadrupleChildPercentageDiscount"] == 15.0


def test_no_document_wide_percentage_means_no_discount_sent(fake_api_client):
    """Confirms the widget's 'no discount will be sent' message is accurate when nothing was
    extracted/entered - must not invent a discount out of nowhere. (The payload dict always
    carries these two keys - PriceListPriceVO models them as Optional[float] = None - so "no
    discount" means the value is None, not that the key is absent.)"""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=_TRIPLE_QUAD_PRICE_LIST),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert price["tripleChildPercentageDiscount"] is None
    assert price["quadrupleChildPercentageDiscount"] is None


def test_a_rows_own_explicit_discount_still_wins_over_the_document_wide_value(fake_api_client):
    """The widget's docstring promises a row's own stated discount always wins over the
    document-wide fallback it edits - proven end to end, not just at the normalize_price_list
    unit level."""
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {
            "triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 200},
            "tripleChildPercentageDiscount": 40,  # this row's own stated value
        },
    }]
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=price_list, child_discount_percentage=15),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert price["tripleChildPercentageDiscount"] == 40.0      # row's own value untouched
    assert price["quadrupleChildPercentageDiscount"] == 15.0   # document-wide fallback applied


def test_single_and_double_never_carry_a_discount_field_at_all(fake_api_client):
    """CONFIRMED REAL LIMITATION: Travel Compositor's price list schema has no Single/Double
    child-discount field - the fallback must never invent one, however the document-wide
    percentage is set."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=_TRIPLE_QUAD_PRICE_LIST, child_discount_percentage=50),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert not any("ingle" in k and "hild" in k for k in price)
    assert not any("ouble" in k and "hild" in k for k in price)


def test_gating_condition_no_triple_or_quadruple_priced_means_nothing_to_preview():
    """Mirrors the widget's own early-return condition: with no Triple/Quadruple priced,
    sold_occupancies never includes either key, so the widget shows its 'nothing to set here'
    caption instead of a number input - checked here at the logic level the widget calls into."""
    single_double_only = [{"startDate": "2027-01-01", "endDate": "2027-12-31",
                           "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}}}]
    sold = sold_occupancies(single_double_only)
    assert not ({"triplePrice", "quadruplePrice"} & sold)


def test_gating_condition_triple_priced_means_something_to_preview():
    sold = sold_occupancies(_TRIPLE_QUAD_PRICE_LIST)
    assert {"triplePrice", "quadruplePrice"} & sold


def test_render_child_discount_editor_is_a_ui_components_export():
    """Basic import-surface guard - a typo'd/removed export here would silently break all three
    app.py call sites with an ImportError only visible when the app actually starts."""
    assert callable(render_child_discount_editor)


def test_app_py_imports_the_new_editor():
    source = _read_app_py()
    assert "render_child_discount_editor" in source


def test_app_py_wires_the_editor_into_all_three_closedtour_screens():
    """Regression guard for the exact gap reported: a future edit to any of the three ClosedTour
    pricing screens (single-tour, and the two multi-modality flows) could easily drop this call
    again while touching the surrounding pricing-table code, the same way the discount itself
    went unnoticed for missing UI in the first place."""
    source = _read_app_py()
    assert 'render_child_discount_editor(data, f"mm_{idx}", currency)' in source
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): the mct_mod_ call site's key_prefix
    # is now generation-scoped (flow_widget_key), to fix "Re-extract with updated hint" leaving
    # the previous extraction's Child Discount % on screen - see the comment on that call site
    # and on the "Re-extract" button above it in app.py. The bare f"mct_mod_{midx}" prefix this
    # test used to assert on is deliberately gone from THIS call only.
    assert 'render_child_discount_editor(data, flow_widget_key(f"mct_mod_{midx}", "cde"), currency)' in source
    assert 'render_child_discount_editor(data, "ct_single", currency)' in source
    # Every call site sits immediately after its matching render_extra_child_notice call, so the
    # two widgets are never shown out of order relative to one another.
    # Widened from 200 to 900 for the mct_mod_ pair - the generation-scoping fix above (see
    # test_render_currency_check_never_reruns_or_writes_session_state's own sibling in the
    # currency-lock tests for the established precedent on widening this kind of window) added
    # an explanatory comment between the two calls that the tighter window doesn't accommodate.
    for extra_child_call, discount_call, max_gap in [
        ('render_extra_child_notice(data, f"mm_{idx}")', 'render_child_discount_editor(data, f"mm_{idx}", currency)', 200),
        ('render_extra_child_notice(data, f"mct_mod_{midx}")',
         'render_child_discount_editor(data, flow_widget_key(f"mct_mod_{midx}", "cde"), currency)', 900),
        ('render_extra_child_notice(data, "ct_single")', 'render_child_discount_editor(data, "ct_single", currency)', 200),
    ]:
        assert source.index(extra_child_call) < source.index(discount_call)
        assert source.index(discount_call) - source.index(extra_child_call) < max_gap
