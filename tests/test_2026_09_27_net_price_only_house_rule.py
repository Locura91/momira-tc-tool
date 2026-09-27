"""Regression test for a real product-owner house rule (2026-09-27), verbatim: "if contract says
in one part of a list net prices and in the other part sales prices, we are ONLY using net prices
generally. In travel compositor we can ONLY add Net prices, never sales prices."

Some supplier documents show both a NET price (what Momira/the DMC actually pays) and a SALES/
RETAIL/GROSS price (a marked-up customer-facing price) for the same item, sometimes in different
parts of the same document or in separate columns of the same table. Travel Compositor only
accepts net prices - Momira/Travel Compositor applies its own markup on top - so every price
extraction prompt must instruct the AI to always use the NET figure and ignore the sales one.

Each extraction prompt in ai_extractor.py is a fully separate string constant with no shared
preamble, so this rule has to be duplicated into every prompt that extracts price fields. This
test checks the rule text is present in each of the 9 relevant prompts, and confirms
TICKET_MAIN_INFO_SYSTEM_PROMPT (which extracts no price fields) intentionally has none.
"""
import os
import re

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(_REPO_ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def _prompt_span(src, name, next_names):
    """Return the source text of one top-level PROMPT = \"\"\"...\"\"\" constant, up to whichever
    of next_names starts first after it."""
    start = src.index(f"\n{name} = ")
    end = len(src)
    for other in next_names:
        try:
            idx = src.index(f"\n{other} = ", start + 1)
        except ValueError:
            continue
        end = min(end, idx)
    return src[start:end]


PROMPT_ORDER = [
    "EXTRACTION_SYSTEM_PROMPT",
    "OPTION_ONLY_SYSTEM_PROMPT",
    "MODALITY_EXTRACTION_SYSTEM_PROMPT",
    "TICKET_EXTRACTION_SYSTEM_PROMPT",
    "TICKET_MAIN_INFO_SYSTEM_PROMPT",
    "TICKET_MODALITY_SYSTEM_PROMPT",
    "TICKET_OPTION_ONLY_SYSTEM_PROMPT",
    "TRANSFER_EXTRACTION_SYSTEM_PROMPT",
    "TRANSPORT_EXTRACTION_SYSTEM_PROMPT",
    "HOTEL_EXTRACTION_SYSTEM_PROMPT",
]

PRICE_EXTRACTING_PROMPTS = [p for p in PROMPT_ORDER if p != "TICKET_MAIN_INFO_SYSTEM_PROMPT"]


def _spans():
    src = _read("ai_extractor.py")
    return {
        name: _prompt_span(src, name, [n for n in PROMPT_ORDER if n != name])
        for name in PROMPT_ORDER
    }


def test_net_price_only_rule_present_in_every_price_extracting_prompt():
    spans = _spans()
    for name in PRICE_EXTRACTING_PROMPTS:
        assert "NET PRICE ONLY" in spans[name], (
            f"{name} extracts price fields and must carry the NET PRICE ONLY house rule"
        )
        assert "never sales prices" in spans[name] or "never sales prices." in spans[name]


def test_net_price_only_rule_absent_from_ticket_main_info_prompt():
    spans = _spans()
    assert "NET PRICE ONLY" not in spans["TICKET_MAIN_INFO_SYSTEM_PROMPT"], (
        "TICKET_MAIN_INFO_SYSTEM_PROMPT extracts no price fields (name/description/includes-"
        "excludes only) - it should not carry a pricing house rule"
    )


def test_rule_appears_exactly_once_per_prompt():
    spans = _spans()
    for name in PRICE_EXTRACTING_PROMPTS:
        count = spans[name].count("NET PRICE ONLY")
        assert count == 1, f"{name} should have exactly one NET PRICE ONLY rule block, found {count}"


def test_rule_mentions_net_and_sales_terminology_for_ai_recognition():
    src = _read("ai_extractor.py")
    for label in ("net rate", "cost price", "sales price", "sell price", "retail price", "rack rate"):
        assert label in src, f"expected the NET PRICE ONLY rule to mention '{label}' as a recognizable label"


def test_verbatim_product_owner_quote_present():
    src = _read("ai_extractor.py")
    quote = (
        "if contract\n  says in one part of a list net prices and in the other part sales prices, "
        "we are ONLY using\n  net prices generally. In travel compositor we can ONLY add Net "
        "prices, never sales prices."
    )
    assert src.count(quote) == 8, "expected the verbatim quote (indented form) in all 8 generic prompts"
