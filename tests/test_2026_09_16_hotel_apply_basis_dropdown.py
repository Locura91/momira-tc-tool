"""Regression test for a product-owner request (2026-09-16), sent mid-turn right after seeing the
"no apply basis" warning on a real Supplements table:

    "if this warning comes, the human shall easily select from a dropdown menu what to choose.
    No handwritten field is required"

The Offers/Supplements editable_table()s used a plain free-text "apply" column in the
st.data_editor (editable_table already accepts a column_config passthrough, unused here before
this fix), so a human had to type one of the 7 valid apply values exactly, by hand, with no
guardrail against a typo. Both tables now pass a column_config with a
st.column_config.SelectboxColumn for "apply", restricted to HOTEL_APPLY_VALUES (Supplements also
keeps "" selectable - an unfilled basis, the deliberate default the "no apply basis" warning is
about, must stay pickable, not force a value)."""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_offers_table_gets_a_selectbox_column_config_for_apply():
    src = _read_hotel_flow()
    idx = src.index('editable_table("Offers", offers_df, "hp_offers"')
    window = src[idx:idx + 300]
    assert "st.column_config.SelectboxColumn(" in window
    assert '"apply", options=HOTEL_APPLY_VALUES, required=False' in window


def test_supplements_table_gets_a_selectbox_column_config_for_apply():
    src = _read_hotel_flow()
    idx = src.index('editable_table("Supplements", supp_df, "hp_supplements"')
    window = src[idx:idx + 900]
    assert "st.column_config.SelectboxColumn(" in window
    assert '"apply", options=[""] + list(HOTEL_APPLY_VALUES), required=False' in window


def test_supplements_apply_dropdown_still_allows_the_unfilled_blank_state():
    # The blank string must be one of the selectable options, not squeezed out - an unfilled
    # apply basis is a valid, expected state (the whole reason the warning exists) until the
    # human deliberately picks one.
    src = _read_hotel_flow()
    idx = src.index('editable_table("Supplements", supp_df, "hp_supplements"')
    window = src[idx:idx + 900]
    assert 'options=[""] +' in window
