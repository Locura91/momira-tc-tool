"""Tests for the currency-lock fix (full-app audit HIGH #1, 2026-09-01).

CONFIRMED PRODUCT-OWNER RULE (2026-09-01, in response to being shown this finding):
"Once a currency has been set, it can never be changed and all Modalities are using the same
Currency." This supersedes an earlier 2026-08-19 rule that made the per-Modality currency
widget (ui_components.render_currency_check) editable as "an extra check."

CONFIRMED BUG the old editable behavior caused: changing currency from a later Modality's
widget correctly updated the shared `currency` variable going forward, but every EARLIER
Modality's price_list rows had already been saved with the OLD currency baked into each price
entry - builder.coerce_price_list_shape lets an already-stored row's own embedded "currency"
key win over whatever currency is passed in later (`row_currency = value.get("currency") or
currency`). So a ClosedTour/Ticket batch could silently publish part-EUR/part-USD. There were
TWO places currency could be re-set after Modality data already existed: the per-Modality
"extra check" widget, and the Step 3 "Change details" flow re-rendering the original currency
selectbox. Both had to be locked for the rule to actually hold.

Tests: render_currency_check is now display-only (unit-tested directly - it's a pure enough
function to call without a Streamlit test harness by monkeypatching st.caption). The app.py
Step 3 locks are read-only UI wiring behind a `disabled=True` selectbox once
cfg_currency/tk_cfg_currency is already set - tested by reading app.py's source text (it can't
be safely imported in a test process - same constraint as every other app.py-side regression
test in this suite, see test_2026_08_31_closedtour_child_discount_visibility.py).
"""
import os
from unittest.mock import patch

from ui_components import render_currency_check


_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# --- render_currency_check is now display-only ---

def test_render_currency_check_never_reruns_or_writes_session_state():
    with patch("ui_components.st") as mock_st:
        mock_st.session_state = {"cfg_currency": "EUR"}
        result = render_currency_check("EUR", ["EUR", "USD"], "cfg_currency", "some_widget_key")
    assert result == "EUR"
    mock_st.rerun.assert_not_called()
    # No selectbox (editable widget) is rendered anymore - only a read-only caption.
    mock_st.selectbox.assert_not_called()
    mock_st.caption.assert_called_once()
    # Session state was never touched by the function itself.
    assert mock_st.session_state == {"cfg_currency": "EUR"}


def test_render_currency_check_always_returns_the_currency_it_was_given():
    with patch("ui_components.st") as mock_st:
        mock_st.session_state = {}
        assert render_currency_check("USD", ["EUR", "USD"], "tk_cfg_currency", "k") == "USD"


# --- app.py: the Step 3 currency widgets are locked once already set ---

def test_closedtour_step3_currency_widget_is_locked_once_cfg_currency_is_set():
    source = _read_app_py()
    # A guard on the already-set state must exist, disabling the widget when it fires.
    marker = source.index('_currency_already_set = bool(st.session_state.get("cfg_currency"))')
    window = source[marker:marker + 700]
    assert 'currency_in = st.session_state.cfg_currency' in window
    assert 'disabled=True' in window
    # The un-set (first time) branch still lets the operator pick a currency.
    assert 'currency_in = st.selectbox("Currency", CURRENCY_OPTIONS)' in window
    # The guard must come BEFORE any bare (unconditional) selectbox call for this field -
    # i.e. the old always-editable version is gone, replaced entirely by the guarded version.
    unconditional_idx = source.index('currency_in = st.selectbox("Currency", CURRENCY_OPTIONS)')
    assert unconditional_idx > marker  # only reachable inside the "not yet set" branch


def test_ticket_step3_currency_widget_is_locked_once_tk_cfg_currency_is_set():
    source = _read_app_py()
    marker = source.index('_tk_currency_already_set = bool(st.session_state.get("tk_cfg_currency"))')
    window = source[marker:marker + 800]
    assert 'currency_in = st.session_state.tk_cfg_currency' in window
    assert 'disabled=True' in window
    assert 'currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tk_currency")' in window
    unconditional_idx = source.index('currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tk_currency")')
    assert unconditional_idx > marker


def test_closedtour_and_ticket_modality_currency_widgets_still_call_the_shared_display_only_helper():
    """The per-Modality "extra check" call sites (ClosedTour's mct_mod_ loop, Ticket's mt_/
    mtf_ loops) must still route through render_currency_check - now display-only itself, see
    the unit tests above - rather than growing their own inline editable widget."""
    source = _read_app_py()
    assert 'render_currency_check(currency, CURRENCY_OPTIONS, "cfg_currency", f"mct_mod_currency_{midx}")' in source
    assert 'render_currency_check(currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mt_currency_{idx}")' in source
    assert 'render_currency_check(currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtf_currency_{fi_idx}")' in source
