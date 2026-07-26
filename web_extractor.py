"""
Extracts closed-tour product data from a supplier's web page (e.g. a
MultiWander or Oberoi Hotels itinerary page) and publishes it as a draft
to Travel Compositor.

This now uses the SAME AI-based extraction as documents (ai_extractor.py),
instead of blind rule-based HTML scraping. This fixes two real problems
found in testing:
  1. Rule-based scraping breaks on messy pages (multiple itinerary
     variants mixed together, JS-heavy menus, non-heading day structure).
  2. It had no way to detect when a page describes MULTIPLE distinct tour
     variants (e.g. a 3-night and 4-night version of the same Nile cruise)
     bundled together - it would just blindly mix both together into one
     confused tour. Now uses detect_tour_variants() first.

Image URLs still need heuristic extraction (AI text-only extraction can't
see <img> tags), so that part stays rule-based.

By default this is a DRY RUN. Pass --publish to actually create the draft.

Usage:
    python web_extractor.py <URL> --supplier 48940 --provider-code ASW-1 --currency EUR
    python web_extractor.py <URL> --supplier 48940 --provider-code ASW-1 --currency EUR --publish
"""
import argparse
import json
import requests
from bs4 import BeautifulSoup

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads
from ai_extractor import extract_structured_data, detect_tour_variants

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"


def get_price_list_interactively(default_currency: str = "EUR") -> list:
    """
    Prompts a human for basic per-occupancy pricing, since this genuinely
    can't be scraped from a marketing page. Builds entries in the confirmed
    real API shape: startDate/endDate + a nested price object with
    per-occupancy MoneyVO (amount+currency) fields.
    """
    print("\n💶 No price list provided. Enter pricing now (required by the API).")
    print("   Leave 'Start date' empty and press Enter to finish.\n")
    price_list = []
    while True:
        start_date = input("  Start date (YYYY-MM-DD, or blank to finish): ").strip()
        if not start_date:
            break
        end_date = input("  End date (YYYY-MM-DD): ").strip()
        name = input("  Label for this row (optional, e.g. 'Peak season'): ").strip()
        single = input("  Single price: ").strip()
        double = input("  Double price: ").strip()
        triple = input("  Triple price (blank if n/a): ").strip()
        quadruple = input("  Quadruple price (blank if n/a): ").strip()
        currency = input(f"  Currency [{default_currency}]: ").strip() or default_currency

        price_block = {}
        if single:
            price_block["singlePrice"] = {"amount": float(single), "currency": currency}
        if double:
            price_block["doublePrice"] = {"amount": float(double), "currency": currency}
        if triple:
            price_block["triplePrice"] = {"amount": float(triple), "currency": currency}
        if quadruple:
            price_block["quadruplePrice"] = {"amount": float(quadruple), "currency": currency}

        entry = {"startDate": start_date, "endDate": end_date, "price": price_block}
        if name:
            entry["name"] = name
        price_list.append(entry)
        print(f"  ✅ Added row: {entry}\n")
    return price_list


def get_page_text(target_url: str) -> str:
    """Fetches a URL and returns clean, readable visible text for AI extraction."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(target_url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    main_content = soup.find("article") or soup.find("main") or soup

    for tag in main_content(["script", "style", "nav", "footer"]):
        tag.decompose()

    return main_content.get_text(separator="\n", strip=True)


def get_page_images(target_url: str) -> list:
    """
    Heuristic-only image grab (AI text extraction can't see <img> tags).
    Returns real hosted URLs found on the page, or a placeholder if none.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(target_url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    main_content = soup.find("article") or soup.find("main") or soup

    extracted_images = [
        img.get("src") for img in main_content.find_all("img")
        if img.get("src") and img.get("src").startswith("http") and "logo" not in img.get("src").lower()
    ]
    return extracted_images[:5] if extracted_images else [FALLBACK_IMAGE]


def extract_from_url(target_url: str, api_client: TravelCompositorAPI,
                      variant_hint: str = None, model: str = "claude-sonnet-5") -> dict:
    """
    Fetches a product page and uses AI extraction (same pipeline as
    documents) to produce structured tour data matching what
    builder.build_closed_tour_payloads() expects.

    variant_hint: pass this if detect_tour_variants() found multiple tours
    on this page and the human picked one - extraction will focus on just
    that variant and ignore the others.
    """
    print(f"📡 Fetching URL: {target_url}...")
    raw_text = get_page_text(target_url)
    print(f"   Extracted {len(raw_text)} characters of visible text.")

    data = extract_structured_data(raw_text, model=model, variant_hint=variant_hint)
    data["image_urls"] = get_page_images(target_url)

    if not data.get("itinerary_destinations"):
        print("⚠️ No destinations recognized. You'll need to add itinerary destinations manually before publishing.")

    return data


def main():
    parser = argparse.ArgumentParser(description="Extract a closed tour from a URL and publish it as a draft.")
    parser.add_argument("url", help="Product page URL to scrape")
    parser.add_argument("--supplier", required=True, help="Travel Compositor supplier ID, e.g. 48940")
    parser.add_argument("--provider-code", required=True, help="Format XXX-Number, e.g. ASW-1")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--modality-code", default="STANDARD_CABIN")
    parser.add_argument("--min-pax", type=int, default=1, choices=[1, 2])
    parser.add_argument("--max-pax", type=int, default=9, choices=range(2, 10))
    parser.add_argument("--on-request", action="store_true", default=True)
    parser.add_argument("--price-list-file", default=None,
                         help="Path to a JSON file containing the priceList array. "
                              "If omitted and --publish is used, you'll be prompted interactively.")
    parser.add_argument("--publish", action="store_true",
                         help="Actually POST to Travel Compositor. Without this flag, it's a dry run.")
    args = parser.parse_args()

    client = TravelCompositorAPI()

    raw_text = get_page_text(args.url)
    variants = detect_tour_variants(raw_text)
    variant_hint = None
    if variants:
        print("\n⚠️ Multiple tour variants detected on this page:")
        for i, v in enumerate(variants, 1):
            print(f"  {i}. {v.get('label')} ({v.get('nights')} nights)")
        choice = input("Which one do you want to extract? (enter number): ").strip()
        try:
            variant_hint = variants[int(choice) - 1]["label"]
        except (ValueError, IndexError):
            print("Invalid choice, defaulting to the first variant.")
            variant_hint = variants[0]["label"]

    extracted_data = extract_structured_data(raw_text, variant_hint=variant_hint)
    extracted_data["image_urls"] = get_page_images(args.url)

    if args.price_list_file:
        with open(args.price_list_file, "r", encoding="utf-8") as f:
            extracted_data["price_list"] = json.load(f)
    elif args.publish:
        extracted_data["price_list"] = get_price_list_interactively(args.currency)

    pre_config = HumanPreConfig(
        supplier_id=args.supplier,
        provider_code=args.provider_code,
        min_pax=args.min_pax,
        max_pax=args.max_pax,
        currency=args.currency,
        modality_code=args.modality_code,
        on_request=args.on_request,
    )

    payloads = build_closed_tour_payloads(pre_config, extracted_data, client)

    print("\n" + "=" * 60)
    print(f"Tour name : {extracted_data['tour_name']}")
    print(f"Tour code : {payloads['main_tour_code']}")
    print(f"Destinations resolved : {[i['destination'] for i in payloads['main_tour_payload']['itinerary']]}")
    if payloads["unresolved_destinations"]:
        print(f"⚠️  UNRESOLVED destinations (fix before publishing): {payloads['unresolved_destinations']}")
    if payloads["tour_option_error"]:
        print(f"⚠️  Option payload incomplete (pricing missing/invalid) - fine for dry run, "
              f"must be fixed before publishing:\n{payloads['tour_option_error']}")
    print("=" * 60)

    if not args.publish:
        print("\n🧪 DRY RUN — nothing was uploaded. Re-run with --publish (and --price-list-file) once this looks right.")
        return

    if payloads["unresolved_destinations"]:
        print("\n❌ Refusing to publish: unresolved destinations present. Fix them first.")
        return

    if payloads["tour_option_error"] or not payloads["tour_option_payload"]:
        print("\n❌ Refusing to publish: pricing is missing or invalid.")
        return

    print("\n🚀 Publishing draft to Travel Compositor...")
    result = client.create_closed_tour(payloads["supplier_id"], payloads["main_tour_payload"])
    if "error" in result:
        print("❌ Main tour creation failed:", result)
        return
    print("✅ Main tour created:", result.get("code", payloads["main_tour_code"]))

    option_result = client.create_closed_tour_option(
        payloads["supplier_id"], payloads["main_tour_code"], payloads["tour_option_payload"]
    )
    if "error" in option_result:
        print("❌ Tour option creation failed:", option_result)
        return
    print("✅ Tour option created. Draft is ready for review inside Travel Compositor.")


if __name__ == "__main__":
    main()
