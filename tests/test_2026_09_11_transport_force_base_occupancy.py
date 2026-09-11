"""Regression tests for build_transport_payloads's new force_base_occupancy parameter
(builder.py, 2026-09-11).

Real-world trigger: the FTS supplier "TRANSFER MATRIX" CSV import (Sedan + Hiace, one row per
city pair). Sedan is 1-3 pax, Hiace is 1-8 pax, and the product owner explicitly confirmed
(2026-09-11): "Sedan is base price as one modality. Hiace is second modality and is used as
price supplement with Sedan... Hiace price-sedan price --> Difference is added as price
supplement."

Without force_base_occupancy, build_transport_payloads's default heuristic picks the WIDEST
occupancy bracket as base (see its own code comment - tuned for the real Aswan-Hurghada example
where the wide bracket genuinely was the base). For Sedan(1-3, width 2) vs Hiace(1-8, width 7),
that default would wrongly pick Hiace as base and give Sedan a NEGATIVE supplement (since Hiace
is always priced higher than Sedan across the whole FTS matrix) - the exact opposite of the
confirmed rule. These tests lock in that force_base_occupancy=(1, 3) correctly forces Sedan as
base regardless of width, and that the default (no override) still reproduces the old
widest-bracket behavior unchanged for every other caller.
"""
from schemas import TransportHumanPreConfig
from builder import build_transport_payloads


def _fts_extracted(sedan_price=215.0, hiace_price=270.0):
    return {
        "departure_name": "Cairo", "arrival_name": "Marsa Alam",
        "service_name": "Private Transfer",
        "charge_unit": "per_service",  # per-vehicle pricing, matches "per vehicle" matrix note
        "occupancy_brackets": [
            {"min_occupancy": 1, "max_occupancy": 3, "price": sedan_price},
            {"min_occupancy": 1, "max_occupancy": 8, "price": hiace_price},
        ],
    }


def test_force_base_occupancy_makes_sedan_the_base_not_the_wider_hiace_bracket(fake_api_client):
    pre_config = TransportHumanPreConfig(supplier_id="99999", currency="USD")
    extracted = _fts_extracted(sedan_price=215.0, hiace_price=270.0)
    result = build_transport_payloads(
        pre_config, extracted, fake_api_client, force_base_occupancy=(1, 3))
    assert result["transport_error"] is None
    assert result["force_base_occupancy_matched"] is True
    # Sedan (base) -> vehiclePrice = 215.0, no per-pax base fields (pricePerPax False).
    assert result["transport_payload"]["pricePerPax"] is False
    assert result["transport_payload"]["vehiclePrice"] == 215.0
    assert result["transport_payload"]["baseAdultPrice"] == 0.0

    by_range = {(o["min_occupancy"], o["max_occupancy"]): o for o in result["option_actions"]}
    sedan_option = by_range[(1, 3)]["option_payload"]
    hiace_option = by_range[(1, 8)]["option_payload"]
    # Sedan is the base bracket -> matches base exactly -> empty prices (house convention).
    assert sedan_option["prices"] == []
    # Hiace supplement = hiace_price - sedan_price = 270 - 215 = 55, POSITIVE.
    assert len(hiace_option["prices"]) == 1
    assert hiace_option["prices"][0]["adultPriceSupplement"] == 55.0


def test_without_force_base_occupancy_the_default_widest_bracket_heuristic_is_unchanged(fake_api_client):
    # Same FTS-shaped data but with NO override - confirms the historical default behavior
    # (widest bracket wins as base) is untouched for every other caller that doesn't pass
    # force_base_occupancy. Here that means Hiace (1-8, width 7) wins over Sedan (1-3, width 2).
    pre_config = TransportHumanPreConfig(supplier_id="99999", currency="USD")
    extracted = _fts_extracted(sedan_price=215.0, hiace_price=270.0)
    result = build_transport_payloads(pre_config, extracted, fake_api_client)
    assert result["transport_error"] is None
    assert result["force_base_occupancy_matched"] is False
    assert result["transport_payload"]["vehiclePrice"] == 270.0

    by_range = {(o["min_occupancy"], o["max_occupancy"]): o for o in result["option_actions"]}
    sedan_option = by_range[(1, 3)]["option_payload"]
    hiace_option = by_range[(1, 8)]["option_payload"]
    assert hiace_option["prices"] == []
    # Sedan supplement = 215 - 270 = -55, negative - this is the bug the FTS import must avoid.
    assert sedan_option["prices"][0]["adultPriceSupplement"] == -55.0


def test_force_base_occupancy_with_no_matching_bracket_falls_back_to_default_heuristic(fake_api_client):
    # Defensive: an override that doesn't correspond to any real bracket in this data should not
    # silently build against an empty/zero base - it should fall back to the normal heuristic and
    # say so via force_base_occupancy_matched=False, so a caller/review screen can flag it.
    pre_config = TransportHumanPreConfig(supplier_id="99999", currency="USD")
    extracted = _fts_extracted(sedan_price=215.0, hiace_price=270.0)
    result = build_transport_payloads(
        pre_config, extracted, fake_api_client, force_base_occupancy=(1, 99))
    assert result["transport_error"] is None
    assert result["force_base_occupancy_matched"] is False
    # Falls back to the widest-bracket default -> Hiace (1-8) wins, same as the no-override test.
    assert result["transport_payload"]["vehiclePrice"] == 270.0


def test_force_base_occupancy_none_default_value_behaves_identically_to_omitting_it(fake_api_client):
    pre_config = TransportHumanPreConfig(supplier_id="99999", currency="USD")
    extracted = _fts_extracted()
    result_omitted = build_transport_payloads(pre_config, extracted, fake_api_client)
    result_explicit_none = build_transport_payloads(
        pre_config, extracted, fake_api_client, force_base_occupancy=None)
    assert result_omitted["transport_payload"]["vehiclePrice"] == \
        result_explicit_none["transport_payload"]["vehiclePrice"]
    assert result_omitted["force_base_occupancy_matched"] == \
        result_explicit_none["force_base_occupancy_matched"] is False
