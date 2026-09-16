"""Regression tests for two follow-up requests (product owner, 2026-09-16), reported right after
a real publish attempt that both showed 4 supplements rejected as duplicates AND surfaced a raw
Travel Compositor room-price exception (that second bug - room_name_to_distributions not seeded
from the existing hotel's own rooms - is covered separately in
test_2026_09_16_hotel_room_distributions_seeded_from_existing.py):

1. "also, when we have an offer we must also activate the offer" - an offer created without an
   explicit active field published but wasn't actually live/bookable, needing a manual activation
   step in Travel Compositor's back office. ContractHotelOffersVO now defaults active=True, same
   confirmed default every other product type's own 'active' field already uses on create.

2. The literal error report:
       Compulsory Christmas Gala Dinner (24/12) - BB: {"error":["Supplement
       HRG-H1-SUPP-COMPULSORYCHRIST-1 already exists for contract HRG-H1"],"status":"BAD_REQUEST"}
   Offers/supplements are confirmed create-only (no PUT endpoint) and use a deterministic
   placeholder providerCode - so a republish (fixing one unrelated failure, e.g. the room-price
   bug above, and clicking Publish again) that re-sends an offer/supplement ALREADY created in an
   earlier attempt this session gets rejected by Travel Compositor with this exact "already
   exists" message. That's not a real problem - the item is already live under that exact code -
   so it must not be reported as a failure requiring yet another fix-and-retry cycle.
"""
import os

from schemas import ContractHotelOffersVO

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# 1. offers default to active
# ---------------------------------------------------------------------------------------------

def test_offer_vo_defaults_to_active_true():
    offer = ContractHotelOffersVO(providerCode="AUTO123", type="PERCENT", apply="LODGING")
    assert offer.active is True


def test_offer_payload_includes_active_true_by_default():
    offer = ContractHotelOffersVO(providerCode="AUTO123", type="PERCENT", apply="LODGING")
    assert offer.dict()["active"] is True


# ---------------------------------------------------------------------------------------------
# 2. "already exists" on a republish is treated as a no-op success, not a failure
# ---------------------------------------------------------------------------------------------

def _hp_already_exists_error(message):
    """Re-implemented here rather than imported - flows/hotel.py can't be imported in a test
    process (circular import with app.py, no Streamlit runtime - same established limitation
    every other flows/hotel.py test in this suite works around with source-text checks). Kept
    byte-for-byte in sync with the real function's logic; test_offer_already_exists_response_*
    below additionally checks the real source wires it in at the right call sites."""
    return bool(message) and "already exists" in str(message).lower()


def test_hp_already_exists_error_recognizes_the_real_error_shape():
    assert _hp_already_exists_error(
        "Supplement HRG-H1-SUPP-COMPULSORYCHRIST-1 already exists for contract HRG-H1") is True


def test_hp_already_exists_error_is_false_for_other_messages():
    assert _hp_already_exists_error("Bean Validation constraint(s) violated") is False
    assert _hp_already_exists_error(None) is False
    assert _hp_already_exists_error("") is False


def test_the_real_hp_already_exists_error_function_exists_in_source():
    src = _read_hotel_flow()
    assert 'def _hp_already_exists_error(message):' in src
    assert 'return bool(message) and "already exists" in str(message).lower()' in src


def test_offer_already_exists_response_maps_to_the_placeholder_code_not_a_failure():
    src = _read_hotel_flow()
    idx = src.index("resp = client.create_hotel_offer(")
    window = src[idx:idx + 700]
    assert "_hp_already_exists_error(resp.get(\"message\"))" in window
    assert 'offer_map[name] = res["offer_payload"].get("providerCode")' in window


def test_supplement_already_exists_response_maps_to_the_placeholder_code_not_a_failure():
    src = _read_hotel_flow()
    idx = src.index("resp = client.create_hotel_supplement(")
    window = src[idx:idx + 700]
    assert "_hp_already_exists_error(resp.get(\"message\"))" in window
    assert 'supplement_map[name] = res["supplement_payload"].get("providerCode")' in window
