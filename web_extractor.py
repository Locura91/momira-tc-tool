"""
Extracts closed-tour product data from a supplier's web page (e.g. a
MultiWander or Oberoi Hotels itinerary page) and publishes it as a draft
to Travel Compositor.

This replaces step3_uploader.py:
  - No more hardcoded credentials -> uses TravelCompositorAPI (.env)
  - No more 5-entry hardcoded IATA_LOOKUP -> uses the real 16,000+
    destination list via api_client.find_destinations_in_text()
  - No more hand-built payload dict -> routes through schemas.py /
    builder.py, so it gets the same validation as every other input source
  - By default this is a DRY RUN (prints the payload, does not upload).
    Pass --publish to actually create the draft in Travel Compositor.

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

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"


def get_price_list_interactively() -> list:
    """
    Prompts a human for basic per-occupancy pricing, since this genuinely
    can't be scraped from a marketing page. Enter as many rows as needed;
    press Enter on an empty 'from date' to finish.
    """
    print("\n💶 No price list provided. Enter pricing now (required by the API).")
    print("   Leave 'From date' empty and press Enter to finish.\n")
    price_list = []
    while True:
        from_date = input("  From date (YYYY-MM-DD, or blank to finish): ").strip()
        if not from_date:
            break
        to_date = input("  To date (YYYY-MM-DD): ").strip()
        single = input("  Single price: ").strip()
        double = input("  Double price: ").strip()
        triple = input("  Triple price (blank if n/a): ").strip()
        quadruple = input("  Quadruple price (blank if n/a): ").strip()
        entry = {
            "from": from_date,
            "to": to_date,
            "singlePrice": float(single) if single else 0.0,
            "doublePrice": float(double) if double else 0.0,
        }
        if triple:
            entry["triplePrice"] = float(triple)
        if quadruple:
            entry["quadruplePrice"] = float(quadruple)
        price_list.append(entry)
        print(f"  ✅ Added row: {entry}\n")
    return price_list


def extract_from_url(target_url: str, api_client: TravelCompositorAPI) -> dict:
    """
    Scrapes a product page and returns a dict shaped to match what
    builder.build_closed_tour_payloads() expects in `extracted_dmc_data`.

    NOTE: This is heuristic HTML scraping, not AI extraction. It's a
    reasonable first pass for well-structured pages; genuinely messy
    supplier pages will likely need the AI-extraction step (still to be
    built) instead of, or in addition to, this.
    """
    print(f"📡 Scraping URL: {target_url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(target_url, headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    main_content = soup.find("article") or soup.find("main") or soup

    title_tag = main_content.find("h1") or soup.find("h1")
    tour_name = title_tag.get_text(strip=True) if title_tag else "Untitled Tour"

    desc_paragraphs = [
        p.get_text(strip=True) for p in main_content.find_all("p")
        if len(p.get_text(strip=True)) > 50
    ][:3]
    full_description = "<p>" + "</p><p>".join(desc_paragraphs) + "</p>" if desc_paragraphs else f"<p>{tour_name}</p>"

    included_items = [
        li.get_text(strip=True) for li in main_content.find_all("li")
        if 15 < len(li.get_text(strip=True)) < 150
    ][:8]
    included_html = (
        "<ul>" + "".join(f"<li>{item}</li>" for item in included_items) + "</ul>"
        if included_items else "<ul><li>Accommodation as per itinerary</li></ul>"
    )

    extracted_images = [
        img.get("src") for img in main_content.find_all("img")
        if img.get("src") and img.get("src").startswith("http") and "logo" not in img.get("src").lower()
    ]
    final_images = extracted_images[:5] if extracted_images else [FALLBACK_IMAGE]

    # Destination resolution: scan the ENTIRE content body (paragraphs, list
    # items, and headings, in document order) for real destination names,
    # using the actual Travel Compositor destination list. Destinations are
    # often embedded inside day-by-day paragraphs (e.g. "Day 2: drive to
    # Edfu via Esna"), not in headings - so we can't rely on headings alone.
    itinerary_destination_names = []
    text_blocks = main_content.find_all(["h2", "h3", "p", "li"])
    for block in text_blocks:
        block_text = block.get_text(strip=True)
        if not block_text:
            continue
        matches = api_client.find_destinations_in_text(block_text)
        for m in matches:
            if not itinerary_destination_names or itinerary_destination_names[-1] != m["name"]:
                itinerary_destination_names.append(m["name"])

    if not itinerary_destination_names:
        print("⚠️ No destinations recognized anywhere in the page content. "
              "You'll need to add itinerary destinations manually before publishing.")

    print("⚠️ NOTE: 'included'/'excluded' are extracted from ANY bullet list on the page "
          "(this can pick up unrelated content like ship facilities lists). "
          "Always review these in the Review UI before publishing.")

    nights = max(1, len(itinerary_destination_names) - 1) if itinerary_destination_names else 1

    return {
        "tour_name": tour_name,
        "description": full_description,
        "hotels_text": "",
        "included": included_html,
        "excluded": "<ul><li>International flights</li><li>Personal expenses</li></ul>",
        "meeting_point": "",
        "policy_remarks": "",
        "image_urls": final_images,
        # builder.py will resolve these names -> codes itself via api_client.resolve_destination()
        "itinerary_destinations": itinerary_destination_names,
        "nights": nights,
        "operational_days": [1, 2, 3, 4, 5, 6, 7],
        "price_list": []  # NOTE: priceList is REQUIRED by the API - must be filled before --publish will succeed
    }


def main():
    parser = argparse.ArgumentParser(description="Extract a closed tour from a URL and publish it as a draft.")
    parser.add_argument("url", help="Product page URL to scrape")
    parser.add_argument("--supplier", required=True, help="Travel Compositor supplier ID, e.g. 48940")
    parser.add_argument("--provider-code", required=True, help="Format XXX-Number, e.g. ASW-1")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--modality-code", default="STANDARD_CABIN")
    parser.add_argument("--min-pax", type=int, default=1, choices=[1, 2])
    parser.add_argument("--max-pax", type=int, default=9, choices=[4, 5, 6, 7, 8, 9])
    parser.add_argument("--on-request", action="store_true", default=True)
    parser.add_argument("--price-list-file", default=None,
                         help="Path to a JSON file containing the priceList array. "
                              "If omitted and --publish is used, you'll be prompted interactively.")
    parser.add_argument("--publish", action="store_true",
                         help="Actually POST to Travel Compositor. Without this flag, it's a dry run.")
    args = parser.parse_args()

    client = TravelCompositorAPI()

    extracted_data = extract_from_url(args.url, client)

    if args.price_list_file:
        with open(args.price_list_file, "r", encoding="utf-8") as f:
            extracted_data["price_list"] = json.load(f)
    elif args.publish:
        extracted_data["price_list"] = get_price_list_interactively()

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