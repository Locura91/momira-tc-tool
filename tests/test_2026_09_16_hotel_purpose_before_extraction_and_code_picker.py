"""Regression tests for two follow-up requests (product owner, 2026-09-16, same message):

1. "when udating a contracted hotel and the human provides the Document for the upload, would it
   not be smarter to ask before the AI reads the document, if the document is a: checking current
   period b: Add a new period c: mixture of both."

   The contract-purpose question (new_period vs check_current) already existed (2026-09-12), but
   only on the REVIEW screen, after extraction had already run - a display-only toggle for where
   the Price Audit tool appeared, never actually informing the extraction itself. This moves the
   question to Step 3, before the document is read, adds a third "mixture" option, and folds the
   answer into the extraction hint passed to extract_hotel_data.

2. "the Hotel code selection, after i chose the supplier is not working. i still have to add the
   Hotel code manually" - the existing-hotel-codes list added earlier the same day was read-only
   (a plain st.dataframe with nothing clickable) - nothing filled the code field for you. A
   selectbox + "Use this code" button now sets st.session_state["hp_provider_code"] (the text
   field's own key) before that widget renders, so picking a code and clicking Use actually fills
   the field.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# 1. contract-purpose question asked before extraction, with a third "mixture" option
# ---------------------------------------------------------------------------------------------

def test_contract_purpose_radio_appears_before_the_extract_button():
    src = _read_hotel_flow()
    radio_idx = src.index('st.session_state.hp_contract_purpose = st.radio(')
    extract_button_idx = src.index('st.button("🔎 Extract Hotel Contract"')
    assert radio_idx < extract_button_idx


def test_contract_purpose_radio_offers_a_third_mixture_option():
    src = _read_hotel_flow()
    radio_idx = src.index('st.session_state.hp_contract_purpose = st.radio(')
    window = src[radio_idx:radio_idx + 1200]
    assert '"new_period", "check_current", "mixture"' in window
    assert '"mixture":' in window


def test_contract_purpose_is_only_asked_for_an_existing_hotel():
    src = _read_hotel_flow()
    radio_idx = src.index('st.session_state.hp_contract_purpose = st.radio(')
    preceding = src[max(0, radio_idx - 100):radio_idx]
    assert "if existing_snapshot:" in preceding


def test_the_old_review_screen_radio_was_removed_not_duplicated():
    # There must be exactly ONE place that actually renders the radio widget now - the review
    # screen only re-reads the already-given answer via st.session_state.get(...).
    src = _read_hotel_flow()
    assert src.count("st.session_state.hp_contract_purpose = st.radio(") == 1
    assert 'hp_contract_purpose = st.session_state.get("hp_contract_purpose") if existing_snapshot else None' in src


def test_extraction_hint_is_built_from_the_contract_purpose_before_calling_extract_hotel_data():
    src = _read_hotel_flow()
    assert "_HP_CONTRACT_PURPOSE_EXTRACTION_HINTS" in src
    hint_build_idx = src.index("_hp_purpose_hint = _HP_CONTRACT_PURPOSE_EXTRACTION_HINTS.get(")
    extract_call_idx = src.index("extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=combined_hint)")
    assert hint_build_idx < extract_call_idx


def test_extraction_hints_dict_covers_all_three_purposes():
    src = _read_hotel_flow()
    dict_idx = src.index("_HP_CONTRACT_PURPOSE_EXTRACTION_HINTS = {")
    window = src[dict_idx:dict_idx + 1200]
    assert '"new_period":' in window
    assert '"check_current":' in window
    assert '"mixture":' in window


# ---------------------------------------------------------------------------------------------
# 2. hotel code picker actually fills the text field
# ---------------------------------------------------------------------------------------------

def test_code_picker_selectbox_exists():
    src = _read_hotel_flow()
    assert 'st.selectbox(\n                        "Pick an existing code"' in src


def test_use_this_code_button_sets_the_provider_code_session_state_key():
    src = _read_hotel_flow()
    button_idx = src.index('st.button("✅ Use this code"')
    window = src[button_idx:button_idx + 300]
    assert 'st.session_state["hp_provider_code"] = _hp_code_pick_options[_hp_code_pick_label]' in window
    assert "st.rerun()" in window


def test_code_picker_is_positioned_before_the_provider_code_text_input():
    src = _read_hotel_flow()
    button_idx = src.index('st.button("✅ Use this code"')
    input_idx = src.index('st.text_input(\n            "Hotel code (providerCode)"')
    assert button_idx < input_idx


# ---------------------------------------------------------------------------------------------
# 3. mid-turn follow-up (same day): "when rechecking the current price data, we only must
#    upload/change the information that really was detected as change. Not everything needs a
#    complete update." - see builder._hotel_rate_payload_unchanged for the actual diffing logic
#    (tested directly in test_2026_09_16_hotel_rate_unchanged_skip.py); this only checks the
#    publish loop actually SKIPS the API call for an "unchanged" rate rather than sending it.
# ---------------------------------------------------------------------------------------------

def test_publish_loop_skips_the_api_call_for_an_unchanged_rate():
    src = _read_hotel_flow()
    unchanged_idx = src.index('if res["action"] == "unchanged":')
    window = src[unchanged_idx:unchanged_idx + 200]
    assert "rate_unchanged_names.append(res.get(\"rate_name\"))" in window
    assert "continue" in window
    # must be checked BEFORE the update/create branch actually calls the API
    update_call_idx = src.index('resp = client.update_hotel_rates(')
    assert unchanged_idx < update_call_idx


def test_unchanged_rates_are_reported_in_the_publish_success_message():
    src = _read_hotel_flow()
    assert "rate_unchanged_names" in src
    assert "nothing to change, nothing sent" in src
