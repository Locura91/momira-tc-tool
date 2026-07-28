"""
Uses the Claude API to turn raw, messy DMC document text (any language)
into structured English tour data, matching the shape builder.py expects.

Requires ANTHROPIC_API_KEY in .env (get one at console.anthropic.com).
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

VARIANT_DETECTION_PROMPT = """You are checking whether a DMC (Destination Management Company) supplier document/page describes ONE tour, or MULTIPLE distinct tour variants bundled together (e.g. a 3-night and a 4-night version of the same Nile cruise, or a Luxor-to-Aswan and an Aswan-to-Luxor direction of the same itinerary).

Only count it as multiple variants if they are genuinely DIFFERENT products a customer would choose between (different duration, different direction/route, or different itinerary) - not just different room categories, optional add-ons, or price tiers for the SAME single itinerary.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_variants": true or false,
  "variants": [
    {"label": "short human-readable label, e.g. '3 nights - Luxor to Aswan'", "nights": 3}
  ]
}
If there is only one tour, set "multiple_variants": false and "variants": [] ."""

EXTRACTION_SYSTEM_PROMPT = """You are extracting structured travel product data from a DMC (Destination Management Company) supplier document for Momira Travel.

Rules:
- Translate ALL content into English, regardless of the source document's original language.
- Output ONLY valid JSON. No markdown code fences, no explanation, no preamble.
- Never fabricate information that isn't present in the source document. Use empty string "" or empty list [] for anything you can't determine.
- description MUST be formatted as day-by-day HTML using this EXACT pattern (confirmed against a real published tour) - one block per day, each day title in bold, separated by an empty paragraph:
  <p><strong>Day 1: Short title for the day</strong></p><p>Description of what happens on this day.</p><p><br></p><p><strong>Day 2: Short title for the day</strong></p><p>Description of what happens on this day.</p><p><br></p>...
  Keep going for every day in the itinerary. Regardless of how the source presents each day - a time-by-time schedule (e.g. "12:00pm Embarkation, 2:00pm Visit temple"), a bare bullet list, or already flowing prose - always REWRITE it into natural, engaging, SEO-strong flowing sentences for that day's paragraph, not a copy of the raw format. Use ONLY facts, places, and activities that are actually present in the source - never invent or add details, opening hours, prices, or claims that aren't there. The goal is better PROSE, not more information.
  LENGTH LIMIT: keep each day's paragraph to 3-4 sentences MAXIMUM (roughly 60-90 words) - pick the most compelling highlights rather than listing everything mentioned. This is a firm limit, not a suggestion - shorter, punchier prose reads better anyway and keeps the response fast to generate.
  MEAL CODES: if the source indicates which meals are included each day (e.g. "[B, L, D]", "[-, L, D]", "Breakfast and lunch included"), add the SAME meal codes in parentheses right after that day's title, e.g. "Day 1: Short title (B, L)". Only include codes for meals actually mentioned for that day - if a day has no meals mentioned, add nothing in parentheses. Use these codes: B=Breakfast, L=Lunch, D=Dinner, P=Picnic (add other single-letter codes only if the source uses a different one you can map clearly). At the very END of the full description (after the last day's closing </p><p><br></p>), add ONE final legend paragraph explaining only the codes actually used anywhere in the description, e.g.: <p><em>B = Breakfast | L = Lunch | D = Dinner</em></p>. Omit any code not actually used. If the source gives no meal information at all, skip both the parenthetical codes and the legend entirely.
- hotels_text MUST always follow this EXACT template (confirmed against a real published tour) - a fixed intro paragraph (always exactly this wording), then a bulleted list:
  <p><strong>Planned hotels for this tour (subject to availability; equivalent alternatives may be used and the tour price may be adjusted if necessary)</strong></p><ul><li>City1 – Hotel Name 1</li><li>City2 – Hotel Name 2 (or Alternative Hotel Name)</li></ul>
  IMPORTANT: only add a new bullet when the accommodation actually CHANGES. If the tour is a cruise/riverboat and the client stays in the SAME vessel/cabin the whole time (even while visiting different destinations along the way), that is ONE hotel/accommodation, not one per destination - write a single bullet like "RV [Ship Name] – Deluxe Cabin (entire cruise)" rather than repeating the ship name per city. Only include cities/stops and hotel names actually found in the source - never invent one. If the source gives no hotel names at all, still use the intro paragraph but list each destination with "Hotel to be confirmed" instead of fabricating a name.
- hotels_count: the number of DIFFERENT accommodations/hotels the client actually stays in (count the bullets you just wrote in hotels_text - e.g. a cruise with one ship the whole way is 1, a land tour through 3 different-hotel cities is 3).
- supplements: TRUE OPTIONAL add-ons the customer only pays for if they choose them - upgrades (better hotel/room/meal category) or optional excursions (e.g. "Optional: Dinner at X Restaurant - 55 EUR", "Optional half-day excursion to Y - 40 USD"). Do NOT include anything that's already covered in included/excluded - only things explicitly marked optional/extra with their own separate price.
  CRITICAL - MANDATORY peak season/holiday surcharges are NOT supplements (a supplement implies the
  customer chooses to pay it - a mandatory surcharge doesn't). Two cases:
  (a) The surcharge applies to the WHOLE TRIP/booking uniformly (e.g. "20% higher during Christmas") -
      do NOT model this as a supplement at all. Instead, this must become a SEPARATE ROW in price_list
      with the elevated price for that date range (see price_list rules below) - describe this clearly
      in pricing_notes so the human knows an extra season row is needed.
  (b) The surcharge is PER NIGHT/PER UNIT tied to a SPECIFIC hotel or component (e.g. "USD 11 per person
      per night surcharge at the Deluxe Cabin during peak season") - THIS case can be modeled as a
      supplement, but pre-calculate the TOTAL amount by multiplying the per-night rate by the actual
      number of nights spent at that specific hotel/component (determine this from the day-by-day
      itinerary if possible - e.g. rate $11 x 2 nights at that hotel = $22 supplement total). Name the
      supplement clearly with the calculation shown, e.g. "Peak season surcharge - Hotel X (2 nights)".
      If the number of nights at that specific component genuinely can't be determined from the source,
      say so plainly in pricing_notes rather than guessing.
  For each TRUE supplement (optional add-on, or case (b) above), output:
  {
    "name": "clear, specific short label - always required, never leave blank",
    "price": per-person amount as a number,
    "per_pax": true if this charge applies per traveler (the normal case), false if the source says it's a flat/one-time charge regardless of group size,
    "mandatory": true only if the source says this is required despite being listed separately (rare - most supplements are false),
    "on_request": true if the source says this needs advance request/confirmation rather than being instantly bookable,
    "travel_start_date": "YYYY-MM-DD" ONLY if this supplement is restricted to a specific date range (e.g. a seasonal excursion) - otherwise omit/empty string,
    "travel_end_date": "YYYY-MM-DD" - same condition as above
  }
  If nothing optional with its own price is mentioned, leave this as an empty list - don't invent any.
- itinerary_destinations must be a list of plain place names in the EXACT order they appear in the source, NOT codes - codes get resolved separately. CRITICAL: include EVERY stop mentioned in the day-by-day itinerary (use the day headers like "Day 2 | Ban Kao - Sai Yok" as your source of truth for which places to include - don't skip any of them), including the tour's return to its starting city if it ends there (e.g. a tour starting and ending in Bangkok must list "Bangkok" both at the start AND the end of this list) - never deduplicate or drop repeated destinations, the itinerary must mirror the real route exactly. Do NOT include the name of a ship, cruise vessel, train, or vehicle (e.g. "RV River Kwai") as if it were a destination - only real geographic places count.
  CRITICAL - CONFIRMED API RULE: never list the SAME destination twice in a row (consecutively) - if the itinerary stays overnight in the same place for multiple consecutive days (e.g. "Day 3: Siwa Oasis" and "Day 4: Siwa Oasis"), that's still only ONE entry in this list for that place, not one per day. Only add a new entry when the destination actually changes from the previous one. Repeating a destination LATER after visiting other places in between (non-consecutively) is fine and required if the route genuinely returns there.
- included and excluded MUST be formatted as proper HTML bullet lists, one distinct item per bullet, matching this EXACT structure (confirmed against a real published tour) - never a single run-on sentence with semicolons:
  <ul><li>First inclusion/exclusion item</li><li>Second item</li><li>Third item</li></ul>
  Split the source's inclusions/exclusions into separate, natural bullet points even if the source presents them as one paragraph.
  GUIDE LANGUAGE RULE: if the source mentions a base/standard guide language (most often English, e.g.
  "English-speaking guide" or just "local guide" with no language stated - assume English if genuinely
  unstated but a guide is included), make sure "English-speaking guide" (or the stated base language)
  is one of the included bullets explicitly - don't leave it implicit. If the source ALSO mentions OTHER
  languages are available (e.g. "German or French speaking guide subject to availability", "other
  languages on request"), do NOT add those to included/excluded - instead add EACH alternate language as
  its own entry in supplements (see below) so guests clearly see they can request a different language,
  e.g. {"name": "German-speaking guide (upon request)", "price": 0 unless a price is stated, "on_request": true}.
  The goal is always maximum clarity for the guest: what's the standard language, and what other options exist.
- policy_remarks: include genuinely relevant policy info such as child age policy, payment/deposit
  schedule, or other booking terms. CRITICAL - CONFIRMED RULE: NEVER include the source document's own
  cancellation policy/terms here (e.g. "25% charged for cancellations 60-31 days before", "no-show =
  100% fee", any tiered cancellation percentages/timelines). The actual cancellation policy is ALWAYS
  fixed at 30 days / 100% regardless of what the source says, and including the source's different
  wording here would be legally incorrect and contradict the real configured setting. If the source's
  policy section is ONLY about cancellation, leave policy_remarks empty entirely rather than including
  any of it.
- start_time, end_time: if the source states a specific departure/start time and/or end/return time for the tour (e.g. "Starting Time: 8:00 a.m.", "returns around 6pm"), extract as "HH:MM" (24-hour, e.g. "08:00"). Leave both as empty strings if no specific time is stated.
- schedule_notes: if the source describes WHEN this tour departs (e.g. "departs every Tuesday and Saturday", "departs only on the first Monday of each month", "daily departures"), summarize that in plain English here. Do NOT try to convert this into operational_days or specific dates yourself - just describe what you found, a human will translate it into the actual schedule fields.
- operational_days must be a list of weekday NAME strings in uppercase English (e.g. "MONDAY", "TUESDAY"), not numbers. If not specified in the document, use all seven days.
- price_list: only populate this if the document contains an actual pricing table (dates + per-occupancy prices). If pricing is vague, marketing-only, or absent, return an empty list - do not guess numbers. Use this EXACT shape for each entry (confirmed against the real API schema):
  {
    "name": "optional label, e.g. the season or date range description",
    "startDate": "YYYY-MM-DD",
    "endDate": "YYYY-MM-DD",
    "price": {
      "singlePrice": {"amount": 0, "currency": "EUR"},
      "doublePrice": {"amount": 0, "currency": "EUR"},
      "triplePrice": {"amount": 0, "currency": "EUR"},
      "quadruplePrice": {"amount": 0, "currency": "EUR"}
    }
  }
  If the document only gives a single arrival date per row (not a date range), use that same date for both startDate and endDate. Use the currency mentioned in the document.
  IMPORTANT - occupancy/group-size-tiered pricing tables (columns like "1", "2", "3-5", "6-8", "9-14", "15-up" showing per-person price by TOTAL group size, not room-sharing): this schema only has 4 slots (single/double/triple/quadruple) tied to room-sharing, so a table with more than 4 tiers CANNOT be fully represented. Map tiers onto slots by closest fit: the "2" tier -> doublePrice, the tier containing "3" -> triplePrice, the tier containing "4" (or the next one up) -> quadruplePrice, "1" -> singlePrice (omit if the source says N/A for 1 pax). Any tier beyond quadruple (e.g. "9-14", "15-up") CANNOT be included in price_list - instead, describe exactly what was approximated and what had to be dropped (with the real numbers) in the pricing_notes field, so a human can review before publishing. Never silently lose pricing information without flagging it there.
  CRITICAL: singlePrice/doublePrice/triplePrice/quadruplePrice for the SAME date range MUST all go into the price object of ONE SINGLE price_list entry - NEVER create multiple separate entries that share the same or overlapping startDate/endDate just to hold different occupancy tiers. Travel Compositor ADDS TOGETHER the prices from any entries with overlapping dates within one option, so multiple rows for the same period would silently produce a wrong, inflated total price. Only create a NEW entry when the dates genuinely change (e.g. a different season).
- pricing_notes: leave empty UNLESS you had to approximate or drop something while fitting an occupancy/group-size pricing table into the 4-slot Distribution schema (see above) - in that case, explain exactly what was mapped where and what was dropped, including the real numbers, so the human can catch and adjust it.

Output this exact JSON structure:
{
  "tour_name": "",
  "description": "",
  "hotels_text": "",
  "hotels_count": 1,
  "supplements": [],
  "included": "",
  "excluded": "",
  "meeting_point": "",
  "policy_remarks": "",
  "itinerary_destinations": [],
  "nights": 0,
  "start_time": "", "end_time": "",
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "schedule_notes": "",
  "pricing_notes": "",
  "price_list": []
}"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _call_claude(system_prompt: str, user_content: str, model: str, max_tokens: int = 4096) -> dict:
    """Shared helper: calls Claude, strips code fences, parses JSON, raises clearly on failure."""
    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package isn't installed. Run: pip install anthropic --break-system-packages"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in .env. Get one at console.anthropic.com "
            "(Settings -> API Keys -> Create Key) and add it to your .env file."
        )

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw_response = "".join(block.text for block in response.content if block.type == "text")
    cleaned = _strip_code_fences(raw_response)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass  # try a fallback extraction below before giving up

    # Fallback: the model may have added stray text before/after the JSON despite
    # instructions not to. Try isolating just the outermost {...} block.
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(cleaned[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    stop_reason = getattr(response, "stop_reason", None)
    hint = (
        " The response appears to have been cut off (hit the token limit) - try a shorter "
        "document/URL, or this may need a higher max_tokens setting."
        if stop_reason == "max_tokens" else ""
    )
    raise RuntimeError(f"Claude's response wasn't valid JSON.{hint} Raw response:\n{raw_response[:1500]}")


MODALITY_DETECTION_PROMPT = """You are checking whether a DMC supplier document/page describes pricing for
MULTIPLE distinct room/cabin/ticket categories (e.g. "Standard Cabin", "Deluxe Cabin", "Suite" each with
their own price table) for what is otherwise the SAME single tour/ticket product.

This is DIFFERENT from checking for tour variants (different itineraries/durations) - here we're looking
for multiple PRICING CATEGORIES within the same product that would each need to become a separate
Modality/Option in Travel Compositor.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_modalities": true or false,
  "modalities": [
    {"label": "short human-readable label, e.g. 'Standard Cabin'", "suggested_code": "e.g. Standard Cabin (no / + - characters)"}
  ]
}
If there's only one pricing category (or pricing is a single flat table), set "multiple_modalities": false
and "modalities": [] ."""


def detect_multiple_modalities(raw_text: str, model: str = "claude-sonnet-5") -> list:
    """
    Checks whether the source describes MULTIPLE distinct pricing
    categories (room/cabin types) that should become separate Modalities,
    as opposed to one single price table. Returns an empty list if only
    one is found, or a list of {"label": ..., "suggested_code": ...} dicts.
    """
    print("🔎 Checking for multiple pricing categories/modalities in this content...")
    result = _call_claude(MODALITY_DETECTION_PROMPT, raw_text, model, max_tokens=1024)
    modalities = result.get("modalities", []) if result.get("multiple_modalities") else []
    if modalities:
        print(f"⚠️ Detected {len(modalities)} distinct modalities: {[m.get('label') for m in modalities]}")
    else:
        print("✅ Only one pricing category detected.")
    return modalities


def detect_tour_variants(raw_text: str, model: str = "claude-sonnet-5") -> list:
    """
    Checks whether the source text describes ONE tour or MULTIPLE distinct
    variants (e.g. a 3-night and 4-night version of the same cruise).
    Returns an empty list if there's just one tour, or a list of
    {"label": ..., "nights": ...} dicts if genuinely multiple are found.
    """
    print("🔎 Checking for multiple tour variants in this content...")
    result = _call_claude(VARIANT_DETECTION_PROMPT, raw_text, model, max_tokens=1024)
    variants = result.get("variants", []) if result.get("multiple_variants") else []
    if variants:
        print(f"⚠️ Detected {len(variants)} distinct tour variants: {[v.get('label') for v in variants]}")
    else:
        print("✅ Only one tour detected.")
    return variants


def apply_clarification(raw_text: str, current_data: dict, instruction: str, model: str = "claude-sonnet-5") -> dict:
    """
    Understands a human's free-text instruction (a question OR a change
    request) about the source document/current extraction, and applies any
    concrete changes directly. Returns:
      {"summary": "plain-text explanation of what was understood/changed",
       "changes": {only the fields that actually changed, in the SAME shape
                   as the main extraction output - empty dict if nothing
                   needed to change, e.g. for a pure question}}
    The caller merges "changes" into their data dict and shows "summary" to
    the human so they always see exactly what happened.
    """
    system_prompt = (
        "You are helping a human review and refine data extracted from a travel document. "
        "They may ask a QUESTION (answer it, make no changes) or give a CHANGE REQUEST "
        "(e.g. 'fix the end date of season 1 to Sept 30', 'the price for triple should be 449 not 459') "
        "- in that case, actually apply the fix. Use ONLY the source document and current "
        "extracted data as context/facts - never invent information not present in the source. "
        "Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:\n"
        '{"summary": "plain-text explanation of what you understood and changed (or answered)", '
        '"changes": {"<field_name>": <new_value>, ...}}\n'
        "Only include fields in 'changes' that actually need to change - if it's just a question "
        "with no requested change, 'changes' must be an empty object {}. Field names and value "
        "shapes must exactly match the current extracted data's own structure (e.g. price_list is "
        "the same array-of-objects shape, operational_days is the same list of weekday names)."
    )
    user_content = (
        f"--- Source document text ---\n{raw_text[:15000]}\n\n"
        f"--- Currently extracted data ---\n{json.dumps(current_data, indent=2)[:6000]}\n\n"
        f"--- Human's message ---\n{instruction}"
    )
    try:
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return {"summary": "ANTHROPIC_API_KEY is not set - can't process this right now.", "changes": {}}
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=2048, system=system_prompt,
            messages=[{"role": "user", "content": user_content}]
        )
        raw_response = "".join(block.text for block in response.content if block.type == "text")
        cleaned = _strip_code_fences(raw_response)
        result = json.loads(cleaned)
        if "summary" not in result:
            result["summary"] = "(No summary returned.)"
        if "changes" not in result or not isinstance(result["changes"], dict):
            result["changes"] = {}
        return result
    except Exception as e:
        return {"summary": f"Couldn't process this: {e}", "changes": {}}


def answer_clarification_question(raw_text: str, current_data: dict, question: str, model: str = "claude-sonnet-5") -> str:
    """
    Answers a human's free-text question about the source document/current
    extraction, using both as context. Returns plain text - does NOT modify
    any extracted data automatically. The human reviews the answer and
    applies any changes themselves via the editable fields.
    """
    system_prompt = (
        "You are helping a human review data extracted from a travel document. "
        "Answer their question clearly and concisely using ONLY the source document "
        "and the current extracted data provided below as context. If the answer isn't "
        "in the source, say so plainly rather than guessing. Do not output JSON - just "
        "a direct, helpful answer in plain text/prose."
    )
    user_content = (
        f"--- Source document text ---\n{raw_text[:15000]}\n\n"
        f"--- Currently extracted data ---\n{json.dumps(current_data, indent=2)[:5000]}\n\n"
        f"--- Human's question ---\n{question}"
    )
    result_text_parts = []
    try:
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "ANTHROPIC_API_KEY is not set - can't answer questions right now."
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=1024, system=system_prompt,
            messages=[{"role": "user", "content": user_content}]
        )
        for block in response.content:
            if block.type == "text":
                result_text_parts.append(block.text)
        return "".join(result_text_parts).strip() or "(No answer returned.)"
    except Exception as e:
        return f"Couldn't get an answer: {e}"


OPTION_ONLY_SYSTEM_PROMPT = """You are extracting ONLY pricing/schedule data for a Travel Compositor
Modality/Option (ContractClosedTourOptionVO). This is NOT a full tour extraction - do NOT extract
tour name, description, itinerary, hotels, included/excluded, meeting point, policy remarks, or
supplements. The source is often just a pricing table.

Extract ONLY:
- price_list: the pricing table(s). Use this EXACT shape per entry (confirmed against the real API schema):
  {
    "name": "optional label, e.g. the season/date range description",
    "startDate": "YYYY-MM-DD",
    "endDate": "YYYY-MM-DD",
    "price": {
      "singlePrice": {"amount": 0, "currency": "EUR"},
      "doublePrice": {"amount": 0, "currency": "EUR"},
      "triplePrice": {"amount": 0, "currency": "EUR"},
      "quadruplePrice": {"amount": 0, "currency": "EUR"}
    }
  }
  If the document only gives a single arrival date per row (not a range), use that same date for both startDate and endDate. If pricing is a group-size-tiered table (columns like "1","2","3-5","6-8" showing per-person price by TOTAL group size), map the "2" tier -> doublePrice, the tier containing "3" -> triplePrice, "4"-or-higher -> quadruplePrice, "1" -> singlePrice (omit if N/A) - this schema only has 4 slots, so describe anything that had to be dropped/approximated in pricing_notes.
  CRITICAL: singlePrice/doublePrice/triplePrice/quadruplePrice for the SAME date range MUST all go into ONE price_list entry - never create multiple entries with the same/overlapping dates (Travel Compositor ADDS prices together for overlapping-date entries within one option).
- pricing_notes: leave empty UNLESS you had to approximate/drop something fitting a group-size table into the 4-slot schema - explain exactly what, with real numbers.
- schedule_notes: plain-English description of departure timing/pattern if mentioned (e.g. "departs every Monday", "runs only on specific dates in the schedule table") - informational only.
- operational_days: your best guess at which weekdays this departs on, as a list of uppercase weekday names, based on schedule_notes. If genuinely unclear, return all 7 days and let the human confirm.
- stop_sales: array of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} for any explicitly mentioned blackout/non-operating date ranges (e.g. dry-dock periods). Empty list if none mentioned.

Never invent numbers or dates not actually present in the source. If pricing is vague or absent, return an empty price_list rather than guessing.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "price_list": [],
  "pricing_notes": "",
  "schedule_notes": "",
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "stop_sales": []
}"""


def extract_option_only_data(raw_text: str, model: str = "claude-sonnet-5", human_hint: str = None) -> dict:
    """
    Lightweight extraction for adding/updating a Modality to an EXISTING
    ClosedTour - only pulls pricing/schedule fields (what
    ContractClosedTourOptionVO actually needs), skipping tour-level fields
    entirely (name, description, itinerary, hotels, supplements, etc).
    Much smaller prompt/output than extract_structured_data - faster,
    cheaper, and avoids re-deriving things that don't change per-option.
    """
    user_content = raw_text
    if human_hint:
        user_content = f"IMPORTANT - human guidance for this extraction: {human_hint}\n\n--- Source content ---\n{raw_text}"

    data = _call_claude(OPTION_ONLY_SYSTEM_PROMPT, user_content, model, max_tokens=4096)

    defaults = {
        "price_list": [], "pricing_notes": "", "schedule_notes": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "stop_sales": [],
        # Defensive: fields builder.py's main_tour_payload construction still
        # reads, even though it's unused/not sent for option-only actions.
        "tour_name": "", "description": "", "hotels_text": "", "hotels_count": 1,
        "supplements": [], "included": "", "excluded": "", "meeting_point": "",
        "policy_remarks": "", "itinerary_destinations": [], "nights": 1,
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default
    return data


def extract_structured_data(raw_text: str, model: str = "claude-sonnet-5", variant_hint: str = None, human_hint: str = None) -> dict:
    """
    Sends raw document text to Claude and returns structured, English,
    JSON-parsed tour data. Raises RuntimeError with a clear message if the
    API call fails or the response isn't valid JSON (rather than silently
    returning garbage).

    variant_hint: if the source describes multiple tour variants and the
    human has picked one (via detect_tour_variants), pass its label here so
    the AI extracts ONLY that variant and ignores the others.

    human_hint: free-text guidance from the human to steer extraction, e.g.
    "use the German-language pricing table, not the English one" or
    "focus on the Superior room category". Passed through as-is - keep it
    short and specific for best results.
    """
    user_content = raw_text
    prefix_parts = []
    if variant_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE tour variants. "
            f"Extract ONLY the following variant, and completely ignore any other "
            f"variant/itinerary mentioned elsewhere in the text: {variant_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    print(f"🤖 Sending document to Claude ({model}) for extraction..."
          + (f" [variant: {variant_hint}]" if variant_hint else ""))
    data = _call_claude(EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=16384)

    # Defensive defaults in case the model omits a key
    defaults = {
        "tour_name": "", "description": "", "hotels_text": "", "hotels_count": 1, "supplements": [], "included": "",
        "excluded": "", "meeting_point": "", "policy_remarks": "",
        "itinerary_destinations": [], "nights": 0, "start_time": "", "end_time": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "pricing_notes": "", "stop_sales": [], "price_list": []
    }
    defaults.update(data)

    print(f"✅ Extraction complete: '{defaults['tour_name']}' "
          f"({len(defaults['itinerary_destinations'])} destinations, {defaults['nights']} nights)")
    return defaults


# ============================================================================
# TICKET EXTRACTION (excursions - single destination, no overnight)
# ============================================================================

TICKET_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data for a Travel Compositor TICKET
(an excursion/activity - single destination, no overnight stay) from a DMC supplier document.
This is DIFFERENT from a multi-day tour: no itinerary, no day-by-day description, no room-occupancy
pricing. Translate ALL content to English regardless of source language.

Extract:
- ticket_name: the excursion/activity name
- description: a SINGLE HTML block (not day-by-day) describing what the experience involves,
  written as natural, engaging prose. Use ONLY facts present in the source - never invent details.
  Format: <p>paragraph(s)</p> - keep it to 2-4 short paragraphs maximum.
- city: the single city/location where this takes place (a plain place name, e.g. "Tokyo") - this
  will be resolved to real coordinates separately, so use the exact place name as commonly known.
- includes: a LIST of plain strings (not HTML) - each a short inclusion, e.g. ["Official Voucher", "Handling Fee"]
  GUIDE LANGUAGE RULE (same principle as tours): if a base/standard guide language is mentioned (usually
  English), make sure it's explicitly listed here (e.g. "English-speaking guide"). If OTHER languages are
  available (e.g. "German/French on request"), do NOT list them here - add each as its own supplement
  instead (see below) so guests clearly see the option, e.g. {"name": "German-speaking guide (upon
  request)", "adult_price": 0 unless a price is stated, "children_price": 0, "infant_price": 0}.
- excludes: a LIST of plain strings (not HTML) - each a short exclusion. Empty list if none mentioned.
- meeting_points: list of {"description": "plain place/location name"}. If the source mentions a SPECIFIC
  fixed meeting point (a named train station, monument, landmark, hotel by name, etc.), use that exact
  place name so it can be properly located. If the source only says pickup is from the guest's own
  hotel/accommodation (varies per guest, no fixed place - e.g. "Pick up from Accommodation") OR gives
  no meeting point at all, use exactly {"description": "Hotel Lobby", "variable_location": true} as the
  default - this matches the standard default used across all products, so it doesn't get incorrectly
  geocoded as if it were one specific fixed location.
- meeting_point_summary: one short plain-text sentence describing the meeting point(s) for the datasheet.
- duration: a number, and duration_type: one of "HOURS"/"DAYS" - how long the experience/activity itself lasts
  (NOT how many days a pass is valid for - that's start_date/end_date on the modality). Use 0/"HOURS" if unclear.
- activity_type: a short category label if the source suggests one (e.g. "Tickets", "Tours"), else omit.
- base_adult_price, base_children_price, base_infant_price: the core prices found in the source, as numbers.
  If only one price is given (no child/infant distinction), put it in base_adult_price and leave others 0.
  If pricing is genuinely absent/blank in the source (e.g. a rate table with no values filled in yet),
  leave these as 0 - do NOT invent numbers - and mention this clearly in pricing_notes.
- child_age_min, child_age_max: the age range that counts as "child" pricing, if mentioned (else 6/12 as a common default).
- disallow_adult, disallow_children, disallow_infant: true only if the source explicitly says a passenger
  type isn't allowed (rare) - otherwise all false.
- operational_days: list of uppercase weekday names this is available, or all 7 if unclear/daily.
- schedule_notes: if the source says operational days are NOT YET DETERMINED (e.g. "TBD by Operations",
  "to be confirmed"), say so plainly here so the human knows operational_days is a placeholder default,
  not a real confirmed schedule. Empty string otherwise.
- time_tables: list of specific departure/start times as strings (e.g. ["09:00", "14:00"]) if the source
  gives specific time slots - empty list if not applicable.
- start_date, end_date: the validity date range for this specific modality/price (YYYY-MM-DD). If the
  source gives no clear range, use a wide default like today's year to 3 years out.
- adult_taxes_amount, child_taxes_amount, infant_taxes_amount: any separately-stated taxes/fees, else 0.
- supplements: TRUE OPTIONAL add-ons the customer chooses to pay for, OR mandatory per-night/per-component
  seasonal surcharges tied to a SPECIFIC part of the experience (pre-calculate the total: rate x actual
  nights/units, same rule as the full ClosedTour extraction). CRITICAL - Ticket Modalities only support
  ONE base price + ONE date range (no seasonal array like ClosedTours have), so if the source describes
  a MANDATORY surcharge that changes the WHOLE base price uniformly for a period (not per-night/
  per-component), it CANNOT be represented as a dated supplement OR a pricing-table row here - flag this
  clearly in pricing_notes instead, explaining the human likely needs a SEPARATE Modality (e.g. "Standard
  - Peak Season") for that period, with its own base price and date range.
  Each supplement: {"name": "label", "adult_price": number, "children_price": number,
  "infant_price": number, "travel_start_date": "YYYY-MM-DD", "travel_end_date": "YYYY-MM-DD"}. Empty list if none.
- pricing_notes: leave empty UNLESS something had to be approximated (e.g. a group-size-tiered price
  table forced onto adult/child/infant categories, pricing was genuinely absent from the source, or a
  mandatory whole-trip seasonal price difference couldn't be represented - see supplements rule above) -
  explain what, with real numbers where available, so a human can review.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
  "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
  "activity_type": "", "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "child_age_min": 6, "child_age_max": 12, "disallow_adult": false, "disallow_children": false,
  "disallow_infant": false, "operational_days": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
  "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
  "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0, "supplements": [], "pricing_notes": ""
}"""


TICKET_VARIANT_DETECTION_PROMPT = """You are checking whether a DMC supplier document/page describes ONE
excursion/activity/ticket, or MULTIPLE distinct excursions bundled together in the same document (e.g.
a "City Tour", a "Desert Safari", and a "Snorkeling Trip" all described in one PDF).

Only count it as multiple if they are genuinely DIFFERENT excursions a customer would choose between -
not just different pricing tiers, room/seat categories, or optional add-ons for the SAME single excursion.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_excursions": true or false,
  "excursions": [
    {"label": "short human-readable label, e.g. 'City Tour in El Gouna'"}
  ]
}
If there is only one excursion, set "multiple_excursions": false and "excursions": [] ."""


def detect_ticket_variants(raw_text: str, model: str = "claude-sonnet-5") -> list:
    """
    Checks whether the source describes MULTIPLE distinct excursions/
    activities bundled in one document, as opposed to just one ticket.
    Returns an empty list if only one is found, or a list of
    {"label": ...} dicts if genuinely multiple are found.
    """
    print("🔎 Checking for multiple excursions/tickets in this content...")
    result = _call_claude(TICKET_VARIANT_DETECTION_PROMPT, raw_text, model, max_tokens=1024)
    excursions = result.get("excursions", []) if result.get("multiple_excursions") else []
    if excursions:
        print(f"⚠️ Detected {len(excursions)} distinct excursions: {[e.get('label') for e in excursions]}")
    else:
        print("✅ Only one excursion detected.")
    return excursions


def extract_ticket_data(raw_text: str, model: str = "claude-sonnet-5", variant_hint: str = None, human_hint: str = None) -> dict:
    """Full extraction for a new Ticket + first Modality."""
    user_content = raw_text
    prefix_parts = []
    if variant_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct excursions/tickets. "
            f"Extract ONLY the following one, and completely ignore any other excursion "
            f"mentioned elsewhere in the text: {variant_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    data = _call_claude(TICKET_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=8192)

    defaults = {
        "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
        "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
        "activity_type": None, "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
        "child_age_min": 6, "child_age_max": 12, "disallow_adult": False, "disallow_children": False,
        "disallow_infant": False,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
        "adult_taxes_amount": 0, "child_taxes_amount": 0,
        "infant_taxes_amount": 0, "supplements": [], "pricing_notes": "", "stop_sales": [], "image_urls": [],
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default
    return data


TICKET_OPTION_ONLY_SYSTEM_PROMPT = """You are extracting ONLY pricing/schedule data for a Travel
Compositor Ticket Modality (ContractTicketModalityVO). This is NOT a full ticket extraction - do NOT
extract ticket name, description, city, meeting points, includes/excludes, or cancellation policy
(cancellation and release timing belong to the TICKET itself, not the modality, and aren't touched
when just adding/updating a modality). The source is often just a pricing table for an ALREADY-EXISTING ticket.

Extract ONLY: base_adult_price, base_children_price, base_infant_price, child_age_min, child_age_max,
start_date, end_date (this modality's validity window), operational_days, time_tables,
supplements (for seasonal/holiday price differences - see full prompt's rules on this), pricing_notes.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "child_age_min": 6, "child_age_max": 12, "start_date": "", "end_date": "",
  "operational_days": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
  "time_tables": [], "supplements": [], "pricing_notes": ""
}"""


def extract_ticket_option_only_data(raw_text: str, model: str = "claude-sonnet-5", human_hint: str = None) -> dict:
    """Lightweight extraction for adding/updating a Modality on an EXISTING Ticket."""
    user_content = raw_text
    if human_hint:
        user_content = f"IMPORTANT - human guidance for this extraction: {human_hint}\n\n--- Source content ---\n{raw_text}"

    data = _call_claude(TICKET_OPTION_ONLY_SYSTEM_PROMPT, user_content, model, max_tokens=4096)

    defaults = {
        "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
        "child_age_min": 6, "child_age_max": 12, "start_date": "", "end_date": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "time_tables": [],
        "supplements": [], "pricing_notes": "", "stop_sales": [],
        # Defensive - fields main ticket payload construction still reads even if unused for this action
        "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
        "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
        "activity_type": None, "disallow_adult": False, "disallow_children": False, "disallow_infant": False,
        "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0, "image_urls": [],
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default
    return data
