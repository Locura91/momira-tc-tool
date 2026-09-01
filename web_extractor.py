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

# Stamped on every delivery - see platform_store.py's own header for why. CONFIRMED FIX
# (2026-08-30 audit): this module had never carried a build stamp, so a partial deploy that
# updated every other file but this one would have gone undetected by app.py's own
# _module_build_mismatches() check. Added here and to that check's module list together.
MODULE_BUILD = "2026-09-01-audit-high-closedtour-ticket-flows"

import argparse
import json
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads
from ai_extractor import extract_structured_data, detect_tour_variants

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"

# Full, realistic browser request headers - CONFIRMED REAL FAILURE (2026-08):
# a site (farahnilecruise.com) rejected a bare-User-Agent-only request with
# "406 Client Error: Not Acceptable". Many sites' bot-protection checks for a
# plausible Accept/Accept-Language set (a real browser always sends these
# together) and rejects requests missing them, even when the User-Agent
# itself looks like a normal browser. Used for every outbound fetch in this
# module so all of them benefit, not just page-text fetching.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


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
    response = requests.get(target_url, headers=_BROWSER_HEADERS, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    main_content = soup.find("article") or soup.find("main") or soup

    for tag in main_content(["script", "style", "nav", "footer"]):
        tag.decompose()

    return main_content.get_text(separator="\n", strip=True)


def _first_image_src(img) -> str:
    """
    Returns the best real source URL/path for one <img> tag, or "" if none
    found. CRITICAL ORDER: checks the common lazy-load attribute names
    (data-src/data-lazy-src/data-original) BEFORE the plain src - when a
    lazy-load attribute is present at all, the real src is almost always
    still pointing at a tiny throwaway placeholder (a 1x1 pixel, a
    "blank.gif"/"spacer.png") until JS swaps it in on scroll, so checking
    src first would just return that placeholder instead of falling through
    to the real image. Only when NO lazy-load attribute is present does
    plain src win. Falls back to srcset/data-srcset last (takes the first
    URL listed, before any width descriptor like " 800w"). Skips inline
    data: URIs (base64 placeholders, not real hosted images) everywhere.
    """
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        val = (img.get(attr) or "").strip()
        if val and not val.startswith("data:"):
            return val
    for attr in ("srcset", "data-srcset"):
        val = (img.get(attr) or "").strip()
        if val:
            first_entry = val.split(",")[0].strip()
            first_url = first_entry.split(" ")[0].strip()
            if first_url and not first_url.startswith("data:"):
                return first_url
    return ""


def get_page_images(target_url: str) -> list:
    """
    Heuristic-only image grab (AI text extraction can't see <img> tags).
    Returns real, absolute, hosted image URLs found on the page.

    CONFIRMED REAL BUG (reported: a real supplier page - a plain multi-page
    PHP site, http://sabenagroup.com/kahila/boat_facilities.php - returned
    ZERO images even though it has real usable photos): the old filter
    required img.get("src") to ALREADY start with "http", which silently
    drops every image referenced by a RELATIVE path (e.g. "images/boat1.jpg"
    or "/kahila/images/boat1.jpg") - extremely common on older/simpler sites
    that don't use a CDN with absolute URLs. Fixed by resolving every
    candidate src (including relative and protocol-relative "//..." forms)
    against the page's own URL via urljoin(), instead of requiring it to
    already be absolute. Also now falls back to common lazy-load attributes
    (see _first_image_src) for sites that defer loading via JS, since a
    blank/placeholder src there used to look like "no image" too.
    """
    response = requests.get(target_url, headers=_BROWSER_HEADERS, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    main_content = soup.find("article") or soup.find("main") or soup

    extracted_images = []
    seen = set()
    for img in main_content.find_all("img"):
        raw_src = _first_image_src(img)
        if not raw_src:
            continue
        absolute_url = urljoin(target_url, raw_src)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if "logo" in absolute_url.lower():
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        extracted_images.append(absolute_url)
        if len(extracted_images) >= 12:
            break

    return extracted_images


def _looks_like_real_image(content: bytes) -> bool:
    """True only if `content` genuinely starts with a recognized image file signature.

    CONFIRMED REAL GAP (2026-08-30, reported: images scraped from a page "not working at all"
    in the picker, even though the download itself returned HTTP 200 and non-empty bytes for
    every candidate): `res.raise_for_status()` only catches HTTP error status codes - it says
    nothing about whether the 200 response body is actually an image. A site's bot-protection
    or hotlink-protection layer commonly returns a 200 with an HTML challenge/"access denied"
    page (or a tiny 1x1 tracking pixel) exactly where a real photo was expected, and neither of
    those raises an exception anywhere in this pipeline - they'd previously sail straight
    through, get uploaded to R2 as "successful," and then fail to render as an image in the
    picker with no error anywhere pointing at why. Checking the actual byte signature (not the
    server's own, sometimes-wrong Content-Type header) is what every real image format
    guarantees, regardless of what any header claims."""
    head = (content or b"")[:16]
    if head.startswith(b"\xff\xd8\xff"):              # JPEG
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):          # PNG
        return True
    if head.startswith((b"GIF87a", b"GIF89a")):        # GIF
        return True
    if head.startswith(b"BM"):                          # BMP
        return True
    if head[:4] == b"RIFF" and (content or b"")[8:12] == b"WEBP":  # WEBP
        return True
    return False


def get_page_image_bytes(target_url: str, errors: list = None) -> list:
    """
    Same discovery logic as get_page_images(), but DOWNLOADS each image's
    raw bytes server-side instead of handing the browser a raw URL to fetch
    directly from the source site.

    errors: optional list to append ONE message to when the page genuinely had candidate <img>
    tags but NOT A SINGLE ONE could be downloaded/verified as a real image - as opposed to the
    page simply having no <img> tags at all, which stays silent (nothing wrong, nothing to
    report). CONFIRMED REAL BUG (reported, 2026-08-31: "the App never even shows me available
    images, even though...the URL has some images included") - a site whose bot/hotlink
    protection blocks every single one of these server-side requests (a very real, already-
    confirmed failure mode - see _looks_like_real_image's own docstring) used to look byte-for-
    byte identical to "this page has zero images", with no way for a human to tell the two apart.

    CONFIRMED REAL BUG (reported: URL-scraped images showed as broken/blank
    in the app's picker, while document-embedded images worked fine): the
    two picture sources used to be treated differently - document images
    are downloaded once during extraction and re-hosted via R2 (your private
    Cloudflare bucket - always a working, HTTPS-served URL), but URL-scraped images were
    instead handed to the browser as a raw hotlink straight back to the
    ORIGINAL source site. Two real things break that in practice: (1) mixed
    content blocking - this app is served over HTTPS, and modern browsers
    silently upgrade an http:// <img> src to https:// and drop it entirely
    if that upgraded request fails, which it will for a site with no valid
    HTTPS setup (confirmed against a real supplier site - a certificate
    hostname mismatch on the exact domain that surfaced this bug); (2)
    hotlink protection some sites apply, blocking direct image requests
    from other domains/referrers. Downloading server-side here sidesteps
    both, since the browser then never talks to the original source site at
    all for these images - only to Streamlit itself (raw bytes) or to
    R2 (a reliable, always-HTTPS host you control), exactly like document
    images already do.

    Returns [(filename, bytes), ...] - the SAME shape as the app's
    doc_raw_images - so callers can feed this straight into the existing
    document-image hosting/preview pipeline instead of needing a separate
    one. Each individual image download is best-effort: a failure on ONE
    image (404, timeout, non-image response) is skipped rather than
    aborting the whole batch.
    """
    candidate_urls = get_page_images(target_url)
    if not candidate_urls:
        return []

    results = []
    skip_reasons = []
    for i, img_url in enumerate(candidate_urls):
        try:
            res = requests.get(img_url, headers=_BROWSER_HEADERS, timeout=10)
            res.raise_for_status()
        except requests.RequestException as e:
            skip_reasons.append(f"{img_url} - {e}")
            continue
        if not res.content:
            skip_reasons.append(f"{img_url} - empty response")
            continue
        if not _looks_like_real_image(res.content):
            # A 200 response that isn't actually a photo - a bot/hotlink-protection challenge
            # page, a tracking pixel, a redirect target - see _looks_like_real_image's docstring.
            # Skipped exactly like a failed download, not treated as a found image.
            skip_reasons.append(f"{img_url} - response wasn't a real image (likely the site's "
                                 f"own bot/hotlink protection)")
            continue

        path_only = img_url.split("?", 1)[0].split("#", 1)[0]
        ext = path_only.rsplit(".", 1)[-1].lower() if "." in path_only.rsplit("/", 1)[-1] else ""
        if ext not in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
            ext = "jpg"
        results.append((f"page_img{i + 1}.{ext}", res.content))

    if not results and skip_reasons and errors is not None:
        errors.append(
            f"Found {len(candidate_urls)} image(s) on the page, but none could be downloaded - "
            f"the site is likely blocking these requests. First reason: {skip_reasons[0]}")
    return results


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
