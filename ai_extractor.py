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

CRITICAL - CONFIRMED MISTAKE TO AVOID: a document that gives ONE single day-by-day itinerary (same
duration, same route, same days) followed by SEPARATE pricing/accommodation blocks labeled by a room or
service TIER - e.g. "Standard | English" and "Superior | English", each with its own price grid and its
own named hotel - is NOT multiple tour variants. This is exactly ONE tour sold at multiple Modalities
(pricing options/room categories), which is a completely different Travel Compositor concept from a tour
variant. The giveaway: these tiered blocks always share the identical nights/duration and the identical
itinerary described once above them - a genuine tour variant, by contrast, almost always has ITS OWN
different duration or route, because that's the actual point of it being a different product. If you see
two or more pricing blocks that share the same nights value and sit under one single itinerary narrative,
set "multiple_variants": false regardless of how many differently-labeled pricing tables follow - tag
each with the same duration and let modality detection (a separate, later step) handle the room/tier
categories instead.

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
- CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
  directly (e.g. "Please contact the operator 48 hours before your tour date to confirm your pick-up
  time, and note that the starting time and duration may vary according to traffic, weather and
  operational conditions", "Contact us to confirm timing", "Call the supplier to reconfirm your booking").
  Momira Travel is the tour operator the client actually deals with - the client must NEVER be told to
  contact the DMC/supplier directly, since that DMC is Momira's backend supplier, not the client-facing
  operator. This applies EVERYWHERE such an instruction could appear - description, included/excluded,
  meeting point, policy/remarks, schedule/pricing notes, anywhere - silently drop/omit it entirely rather
  than including, paraphrasing, or softening it. This is a deliberate exclusion, not an oversight.
- description MUST be formatted as day-by-day HTML using this EXACT pattern (confirmed against a real published tour) - one block per day, each day title in bold, separated by an empty paragraph:
  <p><strong>Day 1: Short title for the day</strong></p><p>Description of what happens on this day.</p><p><br></p><p><strong>Day 2: Short title for the day</strong></p><p>Description of what happens on this day.</p><p><br></p>...
  Keep going for every day in the itinerary. Regardless of how the source presents each day - a time-by-time schedule (e.g. "12:00pm Embarkation, 2:00pm Visit temple"), a bare bullet list, or already flowing prose - always REWRITE it into natural, engaging, SEO-strong flowing sentences for that day's paragraph, not a copy of the raw format. Use ONLY facts, places, and activities that are actually present in the source - never invent or add details, opening hours, prices, or claims that aren't there. The goal is better PROSE, not more information.
  LENGTH LIMIT: keep each day's paragraph to 3-4 sentences MAXIMUM (roughly 60-90 words) - pick the most compelling highlights rather than listing everything mentioned. This is a firm limit, not a suggestion - shorter, punchier prose reads better anyway and keeps the response fast to generate.
  MEAL CODES: if the source indicates which meals are included each day (e.g. "[B, L, D]", "[-, L, D]", "Breakfast and lunch included"), add the SAME meal codes in parentheses right after that day's title, e.g. "Day 1: Short title (B, L)". Only include codes for meals actually mentioned for that day - if a day has no meals mentioned, add nothing in parentheses. Use these codes: B=Breakfast, L=Lunch, D=Dinner, P=Picnic (add other single-letter codes only if the source uses a different one you can map clearly). At the very END of the full description (after the last day's closing </p><p><br></p>), add ONE final legend paragraph explaining only the codes actually used anywhere in the description, e.g.: <p><em>B = Breakfast | L = Lunch | D = Dinner</em></p>. Omit any code not actually used. If the source gives no meal information at all, skip both the parenthetical codes and the legend entirely.
  PACKAGE-WIDE PRE-ARRIVAL ADVISORY: if the source mentions an advisory, instruction, or strong
  recommendation that affects the WHOLE package/booking rather than a single day - most commonly
  something the traveler must do BEFORE the tour even starts (e.g. "In line with the program, customers
  are strongly advised to spend the night prior to the start of this package [at a hotel near the
  departure point]", a required pre-tour overnight stay, an early check-in requirement, or similar
  advice that governs the entire trip) - this message must not get lost or buried inside a day's
  paragraph. CONFIRMED RULE: add it as its own standalone paragraph at the VERY END of the description
  field - after the last day's content, and after the meal legend paragraph too if one was added, so it
  is always the LAST paragraph in the whole description. Write it as PLAIN text only: NO icon/emoji (e.g.
  no "⚠️") and NO "Important:" label or other bold prefix - those have caused downstream coding/encoding
  issues, so the paragraph must contain nothing but the advisory sentence itself, e.g.:
  <p>In line with the program, customers are strongly advised to spend the night prior to the start of
  this package.</p>
  Only add this paragraph if the source genuinely contains such a package-wide advisory - never invent
  one. If the source has no such advisory, skip this entirely.
- hotels_text MUST always follow this EXACT template (confirmed against a real published tour) - a fixed intro paragraph (always exactly this wording), then a bulleted list:
  <p><strong>Planned hotels for this tour (subject to availability; equivalent alternatives may be used and the tour price may be adjusted if necessary)</strong></p><ul><li>City1 – Hotel Name 1</li><li>City2 – Hotel Name 2 (or Alternative Hotel Name)</li></ul>
  IMPORTANT: only add a new bullet when the accommodation actually CHANGES. If the tour is a cruise/riverboat and the client stays in the SAME vessel/cabin the whole time (even while visiting different destinations along the way), that is ONE hotel/accommodation, not one per destination - write a single bullet like "RV [Ship Name] – Deluxe Cabin (entire cruise)" rather than repeating the ship name per city. Only include cities/stops and hotel names actually found in the source - never invent one. If the source gives no hotel names at all, still use the intro paragraph but list each destination with "Hotel to be confirmed" instead of fabricating a name.
- hotels_count: the number of DIFFERENT accommodations/hotels the client actually stays in (count the bullets you just wrote in hotels_text - e.g. a cruise with one ship the whole way is 1, a land tour through 3 different-hotel cities is 3).
- supplements: TRUE OPTIONAL add-ons the customer only pays for if they choose them - upgrades (better hotel/room/meal category) or optional excursions (e.g. "Optional: Dinner at X Restaurant - 55 EUR", "Optional half-day excursion to Y - 40 USD"). Do NOT include anything that's already covered in included/excluded - only things explicitly marked optional/extra with their own separate price.
  CRITICAL - IGNORE voluntary carbon offset/carbon emission compensation charges entirely (e.g. "Optional
  CO2 offset contribution - 5 EUR", "Carbon footprint compensation", "voluntary climate contribution") -
  never add these as a supplement or anywhere else in the extracted data, even though they're technically
  optional and priced. This is a deliberate exclusion, not an oversight.
  CRITICAL - CONFIRMED RULE: this rule ONLY applies if the source document ITSELF explicitly mentions a
  peak season/holiday/seasonal surcharge somewhere. NEVER invent or add a peak-season supplement that
  isn't actually mentioned in the source - if the source says nothing about a seasonal surcharge, do not
  create one "just in case"; leave supplements as-is for this case, same as any other never-invent rule
  in this prompt. When (and ONLY when) the source DOES mention one - whole-trip uniform (e.g. "20% higher
  during Christmas") OR per-night/per-component (e.g. "USD 11 per person per night surcharge at the
  Deluxe Cabin during peak season") - ALWAYS model it as its own supplement with "mandatory": true and a
  real travel_start_date/travel_end_date. NEVER represent it as a separate row in price_list, and never
  omit the date range - it must always be present for a peak-season supplement. This supplement OVERLAYS
  the normal price: it's an ADDITIONAL charge on top of whatever the base price already is for bookings
  that fall inside that date range, not a replacement or alternative price. If the source only names a
  season/holiday without exact dates (e.g. "Christmas/New Year", "Peak Season"), use your best real-world
  date range for that period and say so in pricing_notes - don't leave the date range empty just because
  exact dates weren't spelled out.
  CONFIRMED BASIS RULE - how the surcharge is phrased in the source decides BOTH the "price" number AND
  the "per_pax" flag below; getting the combination wrong over- or under-charges the customer, so follow
  this exactly:
  - "per stay" / a flat one-time amount (source says neither "per person" nor "per night"): charged ONCE
    regardless of group size or how long the surcharge period runs. price = the stated flat amount,
    per_pax: false. Never multiply this by anything.
  - "per person" (and NOT also "per night"): price = the stated per-person amount, exactly as given - do
    NOT multiply it by a pax count yourself. Set per_pax: true instead, so Travel Compositor's own booking
    engine multiplies this amount by however many travelers actually book. This is the only correct way to
    handle it, since the real pax count isn't knowable at extraction time - a tour's pax is a min/max
    RANGE, never one fixed number.
  - "per night" (and NOT also "per person") - e.g. "USD 11 per night surcharge during peak season":
    Travel Compositor's supplement schema has NO native "per night" concept, so this multiplication
    genuinely must be done by you. price = the per-night rate x the actual number of TOUR nights that
    fall inside the surcharge's date range, capped at the tour's own total length - e.g. if peak season
    covers the whole of August but the tour itself is only 5 nights long, the multiplier is 5 (the tour's
    own length), NEVER the length of the peak season period itself. Work out the actual affected nights
    from the day-by-day itinerary/dates where possible (e.g. rate $11 x 2 affected nights = $22 total).
    per_pax: false.
    CRITICAL SELF-CHECK - this exact multiplication has been missed before: before finalizing the
    supplement price, explicitly verify you multiplied rate x nights and did NOT just copy the per-night
    rate as if it were the total. If the actual number of affected nights genuinely can't be determined,
    say so plainly in pricing_notes rather than guessing.
  - "per person per night" - e.g. "USD 11 per person per night surcharge at the Deluxe Cabin during peak
    season": combine the two rules above. price = the per-night rate x the actual affected TOUR nights
    ONLY (same calculation and same tour-length cap as the "per night" case just above - this part must
    be pre-calculated by you, since Travel Compositor can't do it). Then set per_pax: true so Travel
    Compositor further multiplies that per-night total by the actual booked pax count - together giving
    the correct rate x nights x pax total without you ever needing to guess a pax number.
  - "per room" / "per room per night" - e.g. "USD 71.00 per room per night" (a flat charge for the WHOLE
    room's occupants together, not per traveler): this needs different handling from every case above,
    because the correct per-PERSON amount now depends on how many people actually share that room.
    Travel Compositor's real Supplement schema mirrors price_list's own occupancy split (single/double/
    triple/quadruple), so use that instead of "price"/per_pax: first compute the TOTAL charge for the
    whole room for the whole stay - the per-room rate x the actual affected TOUR nights (same nights
    calculation and same tour-length cap as the "per night" rule above; if the source says "per room"
    with NO "per night" attached, treat it as already a flat one-time per-room total - don't multiply by
    nights). Then divide that SAME total by 1, 2, 3, and 4 to get the per-person amount for each occupancy
    tier - e.g. rate $71 x 3 affected nights = $213 total for the room, so single_price = 213/1 = 213,
    double_price = 213/2 = 106.50, triple_price = 213/3 = 71, quadruple_price = 213/4 = 53.25. Set
    per_pax: false and put the double_price value in "price" too (as the general-purpose per-person
    figure) - Travel Compositor must NOT multiply any of these occupancy amounts again, each is already
    the final per-person charge for that room configuration.
    CRITICAL SELF-CHECK: verify all four occupancy amounts were computed by dividing the exact SAME
    total-per-room figure by 1, 2, 3, and 4 respectively - never compute them independently, and never
    copy the per-night/per-room rate into more than one slot unchanged.
  - Whole-trip/percentage surcharges (e.g. "20% higher during Christmas", not tied to a per-night rate):
    pre-calculate an actual currency amount where you can (e.g. 20% of the base per-person price) and put
    that resulting number in "price", with per_pax: true (a percentage of a per-person price is itself
    per-person, so let Travel Compositor scale it by actual pax the same way). If a percentage genuinely
    can't be converted to a safe real amount, still create the mandatory supplement with your best
    estimate and flag it clearly in pricing_notes for review.
  For every OTHER basis above (not "per room"/"per room per night"), the per-person amount is the SAME
  regardless of occupancy, so set single_price = double_price = triple_price = quadruple_price = "price".
  CRITICAL - CONFIRMED RULE: for a peak-season/holiday surcharge specifically, the "name" must stay a
  clean, customer-facing label ONLY - e.g. "Christmas/New Year Surcharge" or "Peak Season Surcharge -
  Hotel X" - and must NEVER include the price, percentage, or the calculation (no "(20% of base price)",
  no "(2 nights x $11 = $22)", no dollar amounts of any kind in the name). The customer sees this name
  directly and must not be shown that price breakdown. The actual number still goes in the "price" field
  as always (that's what makes the surcharge real) - only put the calculation itself (the math you did to
  arrive at it, so a human can double-check it before publishing) in pricing_notes, never in the name.
  This does NOT apply to normal optional supplements (non-peak-season add-ons/upgrades) - only to
  peak-season/holiday surcharges.
  MODALITY SCOPING - CONFIRMED RULE: if (and only if) this document describes MULTIPLE distinct room/
  cabin/pricing categories (Modalities) for this same tour - e.g. separate "Standard", "Superior", and
  "Deluxe" pricing/supplement tables - a supplement can belong to just ONE of those categories, or to all
  of them. Tag every supplement with "applies_to" so this never gets mixed up: use the EXACT category
  label as it appears in the source (e.g. "Standard", "Superior Class", "Deluxe") if the supplement is
  explicitly listed under, or clearly named/tied to, only that one category (e.g. a surcharge appearing
  only in a "Deluxe Class Hotel" supplements section, or named "Deluxe Room Upgrade"); use "ALL" if the
  supplement clearly applies to every category (or if the document only describes ONE category/modality
  to begin with - "ALL" is always correct in that single-modality case); use "UNCLEAR" ONLY if the
  document genuinely has multiple categories AND you truly cannot tell which one(s) this supplement
  belongs to - a human will resolve those cases, so it's always safe to say "UNCLEAR" rather than guess.
  For each TRUE supplement (optional add-on, or a peak-season surcharge per the rule above), output:
  {
    "name": "clear, specific short label - always required, never leave blank",
    "price": per-person amount as a number (see occupancy fields below for the "per room" basis),
    "single_price": per-person amount for SINGLE occupancy (1 traveler in the room) - equal to "price" unless this is a "per room"/"per room per night" surcharge, see the BASIS RULE above,
    "double_price": per-person amount for DOUBLE occupancy (2 travelers sharing) - equal to "price" unless a "per room" surcharge, see above,
    "triple_price": per-person amount for TRIPLE occupancy (3 travelers sharing) - equal to "price" unless a "per room" surcharge, see above,
    "quadruple_price": per-person amount for QUADRUPLE occupancy (4 travelers sharing) - equal to "price" unless a "per room" surcharge, see above,
    "per_pax": true if this charge applies per traveler (the normal case), false if the source says it's a flat/one-time charge regardless of group size, OR if this is a "per room"/"per room per night" surcharge (see BASIS RULE above),
    "mandatory": true if the source says this is required despite being listed separately, OR if this is a peak-season/holiday surcharge (see rule above - those are ALWAYS mandatory: true); false for a normal optional add-on,
    "on_request": true if the source says this needs advance request/confirmation rather than being instantly bookable,
    "applies_to": "ALL", or the exact category label, or "UNCLEAR" - see MODALITY SCOPING above,
    "travel_start_date": "YYYY-MM-DD" if this supplement is restricted to a specific date range (e.g. a seasonal excursion) - ALWAYS required (never empty) for a peak-season/holiday surcharge, otherwise omit/empty string,
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
  DUAL-LANGUAGE GUIDE RULE: this is a DIFFERENT case from the one above - if the source lists TWO (or
  more) languages joined by "/" or "or" as EQUAL standard options for the guiding/transfer service (e.g.
  "licenced English/German-speaking guiding service", "English or German speaking guide"), that is NOT a
  base-language-plus-on-request-extra - both languages are included as standard, so keep the included
  bullet as the source states it (e.g. "Licenced English/German-speaking guiding service") rather than
  splitting one off into supplements. In this case the description ALSO MUST clearly state, in plain
  words, that the tour/transfer can run in EITHER English OR German - work one clear sentence to that
  effect into whichever day's paragraph naturally covers the guide/transfer (usually Day 1), e.g. "This
  tour is guided in either English or German." Never leave it ambiguous or worded so it could be read as
  both languages being provided at once - the guest must clearly understand they get ONE of the two.
- policy_remarks: include genuinely relevant NON-MONETARY policy info such as age restrictions/supervision
  requirements, payment/deposit schedule, or other booking terms. CRITICAL - CONFIRMED RULE: NEVER include
  any of the following from the source document, since Momira (as tour operator) applies its own fee
  structure by law rather than the supplier's commercial terms - including the supplier's numbers here
  would be legally incorrect and contradict the real configured settings:
  (1) the source's own cancellation policy/terms (e.g. "25% charged for cancellations 60-31 days before",
      "no-show = 100% fee", any tiered cancellation percentages/timelines) - cancellation is ALWAYS fixed
      at 30 days / 100% regardless of what the source says.
  (2) any child discount PERCENTAGE or fee (e.g. "children under 12 pay 50%", "child rate is 70% of
      adult price") - it's fine to keep non-monetary child age policy (e.g. "must be accompanied by an
      adult", "minimum age 12"), just never the supplier's stated discount percentage/fee itself.
  If the source's policy section is ONLY about cancellation or child pricing percentages, leave
  policy_remarks empty entirely rather than including any of it.
- min_child_age, max_child_age: the age range that counts as "child" (for pricing purposes), AND/OR any
  stated age eligibility restriction (e.g. "children must be at least 12", "not suitable for children
  under 12 years old", "minimum age: 12"). Both kinds of language should populate these fields - use
  whichever number range is most specific to what's actually stated. Default to 2/12 only if genuinely
  nothing about child age is mentioned anywhere in the source (standard convention: infant = 0-2, child =
  2-12) - this has been missed before, so actively look for it even in a "Good to know"/notes section,
  not just a pricing table.
- start_time, end_time: if the source states a specific departure/start time and/or end/return time for the tour (e.g. "Starting Time: 8:00 a.m.", "returns around 6pm"), extract as "HH:MM:SS" (24-hour, e.g. "08:00:00" - CONFIRMED via a real API error that seconds are required, not just HH:MM).
  CONFIRMED RULE - start_time: if the source gives an actual pick-up/collection time for Day 1 (e.g. "Pick-up
  at 07:30", "collection between 6:00-6:30am" - use the earlier/first time given for a range), always use
  that exact time as start_time, in preference to anything below. Otherwise, if the PACKAGE-WIDE
  PRE-ARRIVAL ADVISORY above applies (the source recommends/requires spending the night before the tour
  starts) and no specific pick-up time is stated anywhere, default start_time to "08:00:00" - a
  pre-night stay implies an early, standard-morning departure, so this is a safe, useful default rather
  than leaving the field blank. Do not apply this "08:00:00" default in any other situation - only when
  the pre-arrival advisory genuinely applies AND no real pick-up time was given.
  CONFIRMED RULE - end_time: if the source gives guidance on the LATEST safe departure/return time - most
  commonly a flight-booking recommendation (e.g. "departure flights are recommended not earlier than
  16:00", "please don't book flights before 4pm on the final day", "avoid flights before 18:00 on your
  return day") - use that stated time as end_time, since it reflects the actual earliest moment the
  traveler is free to leave. This applies whether it's phrased as the tour's own return time or as
  flight-booking advice for the final day - either way, treat it as when the tour itself effectively ends.
  Leave start_time/end_time as empty strings only if NEITHER a real time NOR (for start_time) the
  pre-arrival-advisory default above applies.
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
- release_days_mentions: a list of integers - ANY explicit booking/reservation deadline or "release period"
  mentioned anywhere in the source (e.g. "must be booked at least 45 days before departure", "release
  period: 60 days", "reservations required 30 days in advance", "book by X days prior to arrival"). This is
  DIFFERENT from a cancellation policy (e.g. "non-refundable inside 21 days") - do NOT include cancellation
  deadlines here, only booking/reservation/release deadlines. Convert weeks/months to days (e.g. "6 weeks"
  -> 42, "2 months" -> 60). If the source mentions MORE THAN ONE such deadline (e.g. different components
  have different notice periods), include ALL of them as separate integers - a human will apply the
  safest (longest) one rather than you picking. Empty list if nothing like this is mentioned anywhere.

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
  "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "schedule_notes": "",
  "pricing_notes": "",
  "price_list": [],
  "release_days_mentions": []
}"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _sanitize_supplement_price_fields(supplement: dict) -> None:
    """
    CONFIRMED FIX (real production crash, SUB-1): supplements' price fields
    ("price"/"single_price"/"double_price"/"triple_price"/"quadruple_price")
    must be flat numbers, but the model has occasionally returned a nested
    {"amount": ..., "currency": ...} object instead - the shape price_list
    rows use, sitting right next to the supplement schema in the same
    prompt, so the two are an easy mix-up. That dict then survives silently
    all the way through the review UI (a Streamlit numeric cell just shows
    it oddly, it doesn't error) until it hits builder.py's float() call at
    publish time and crashes with "float() argument must be a string or a
    real number, not 'dict'". Catch and unwrap it immediately at extraction
    time instead, so a malformed value never has the chance to look normal
    on the review screen. Mutates `supplement` in place.
    """
    for key in ("price", "single_price", "double_price", "triple_price", "quadruple_price"):
        val = supplement.get(key)
        if isinstance(val, dict):
            supplement[key] = val.get("amount", 0) or 0


_client_singleton = None


def _get_anthropic_client():
    """
    Reuses ONE Anthropic client for the whole process instead of constructing
    a fresh one on every single API call (previously done in 3 separate
    places) - avoids redundant object/HTTP-pool setup on every extraction,
    clarification, or detection call.
    """
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton

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

    _client_singleton = Anthropic(api_key=api_key)
    return _client_singleton


def friendly_error_message(e: Exception) -> str:
    """
    Translates raw Python/API exceptions into a short, plain-language message
    a non-technical human can act on, instead of showing them a stack trace
    or a bare Python exception string. Used anywhere an AI call can fail
    (extraction, clarification, detection) and the failure is shown in the UI.
    """
    text = str(e)
    lower = text.lower()

    if "ANTHROPIC_API_KEY" in text or "api key" in lower:
        return "The AI service isn't set up correctly (missing or invalid API key). Please contact whoever manages this tool."
    if "anthropic' package" in lower or "not installed" in lower:
        return "A required piece of software isn't installed on the server. Please contact whoever manages this tool."
    if "rate_limit" in lower or "rate limit" in lower or "429" in text:
        return "The AI service is temporarily busy (too many requests). Please wait a minute and try again."
    if "overloaded" in lower or "529" in text:
        return "The AI service is temporarily overloaded. Please wait a moment and try again."
    if "timeout" in lower or "timed out" in lower:
        return "The request took too long and timed out. Please try again - if it keeps happening, try with a shorter document."
    if "connection" in lower or "network" in lower:
        return "Couldn't connect to the AI service. Please check your internet connection and try again."
    if "authenticationerror" in lower or "401" in text or "403" in text:
        return "The AI service rejected the request (authentication problem). Please contact whoever manages this tool."
    if "cut off" in lower or "token limit" in lower or "max_tokens" in lower:
        return ("The AI's answer was too long and got cut off before it finished (this document/tour "
                "produced more text than the AI is allowed to send back in one go). Try again - if it keeps "
                "happening on this same document, splitting it into smaller sections usually fixes it.")
    if "wasn't valid json" in lower or ("json" in lower and ("decode" in lower or "parse" in lower)):
        return "The AI's response couldn't be understood. Please try again - if it keeps happening, try rephrasing your request."

    # Fallback: still human-readable, just without technical jargon exposure -
    # checked BEFORE truncating so a helpful hint earlier in a long message
    # (e.g. "cut off"/"token limit", checked above) never gets sliced away by
    # the [:150] cut, only the leftover raw-response dump gets shortened.
    return f"Something went wrong while talking to the AI service ({text[:150]})"


# Cheap/fast model for simple yes-no classification calls (detecting whether a
# document describes multiple tours/tickets/modalities) - these are much
# simpler tasks than full structured extraction, so they don't need the same
# top-tier model. Extraction and clarification calls (where getting the real
# data right matters most) keep using the higher-accuracy model passed in by
# the caller.
HAIKU_MODEL = "claude-haiku-4-5"


def _stream_claude_message(client, model: str, max_tokens: int, system_prompt: str, user_content) -> tuple:
    """
    Sends one message via the Anthropic SDK's STREAMING API rather than a
    plain (non-streaming) create() call. The SDK itself requires streaming
    for any request whose max_tokens is high enough that it MIGHT take
    longer than 10 minutes to generate - confirmed via a real failure
    ("Streaming is required for operations that may take longer than 10
    minutes") once max_tokens was raised for longer tour extractions.
    Streaming works identically for short/simple calls too, so every call
    site uses this shared helper now rather than only the ones that
    technically need it - one code path, no risk of the same error
    resurfacing later if another call's max_tokens grows.
    Returns (full_text, stop_reason).
    """
    full_text_parts = []
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for text in stream.text_stream:
            full_text_parts.append(text)
        final_message = stream.get_final_message()
    return "".join(full_text_parts), getattr(final_message, "stop_reason", None)


EXTRACTION_TOOL_NAME = "provide_extracted_data"

# Deliberately PERMISSIVE (any object, any properties) rather than a
# hand-written schema mirroring every possible output shape across all the
# different extraction prompts - the detailed system prompts already fully
# control what fields/shape to produce; this schema exists only so the
# Anthropic API treats the response as a tool call.
_PERMISSIVE_TOOL_SCHEMA = {"type": "object", "additionalProperties": True}

# Strict schema for apply_clarification's small, fixed-shape output. Unlike the
# big extraction prompts, this MUST force "summary" to always be present -
# confirmed that the permissive schema let Claude drop it on every call.
CLARIFY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Plain-text explanation of what was understood and changed (or answered). "
                            "ALWAYS required, even for a pure question with no changes - never omit this.",
        },
        "changes": {
            "type": "object",
            "additionalProperties": True,
            "description": "Only the fields that actually need to change, in the same shape as the "
                            "extracted data structure. Empty object {} if nothing needs to change.",
        },
    },
    "required": ["summary", "changes"],
}


def _stream_claude_tool_call(client, model: str, max_tokens: int, system_prompt: str, user_content,
                              tool_name: str = EXTRACTION_TOOL_NAME, input_schema: dict = None) -> tuple:
    """
    Forces Claude to respond via a TOOL CALL instead of free-form JSON text
    inside a text block. This closes off an entire class of failure that
    free-form "output JSON as text" prompting is prone to: on a real,
    repeatedly-reproduced failure (same document, three separate tries),
    Claude's free-text response was genuinely malformed JSON - most likely
    an unescaped quote inside a long generated HTML description - which
    broke both the direct json.loads() parse AND the brace-matching
    fallback, surfacing as "The AI's response couldn't be understood" every
    single time for that document.
    Tool-call inputs are parsed and validated as JSON by the Anthropic API
    itself before they ever reach us (we get an already-parsed dict, not
    raw text to parse ourselves) - this makes malformed JSON structurally
    impossible rather than something to keep patching around after the
    fact.
    `input_schema` defaults to a fully permissive object (any keys) for the
    big free-form extraction prompts, where the detailed system prompt
    already fully describes the shape. Callers with a SMALL, fixed shape
    (e.g. apply_clarification's {"summary", "changes"}) should pass a real
    schema with "required" fields instead - a permissive schema gives the
    model no structural signal about which keys are actually expected, and
    was confirmed to let it drift (omitting "summary" entirely on every
    single call) once nothing but prose was enforcing the shape.
    Returns (parsed_input_dict, stop_reason). Raises RuntimeError if Claude
    didn't call the tool for some reason (very rare with tool_choice
    forcing it, but handled rather than silently returning nothing).
    """
    tool_def = {
        "name": tool_name,
        "description": "Provide the requested extracted/structured data as a JSON object, following the shape and rules described in the system prompt.",
        "input_schema": input_schema or _PERMISSIVE_TOOL_SCHEMA,
    }
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
        tools=[tool_def],
        tool_choice={"type": "tool", "name": tool_name},
    ) as stream:
        final_message = stream.get_final_message()

    stop_reason = getattr(final_message, "stop_reason", None)
    for block in final_message.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            return block.input, stop_reason

    raise RuntimeError(
        "Claude didn't return the expected structured data. This is unusual - please try again, and if "
        "it keeps happening, try a shorter document."
    )


def _call_claude(system_prompt: str, user_content: str, model: str, max_tokens: int = 4096) -> dict:
    """
    Shared helper: calls Claude via a forced tool call (see
    _stream_claude_tool_call - guarantees well-formed JSON back, no manual
    parsing/fallback-recovery needed) and returns the resulting dict. The
    system prompt is sent as a cacheable block - Anthropic's prompt caching
    means repeat calls that reuse the SAME system prompt (e.g. one call per
    item in a multi-tour/multi-ticket batch) are billed at a fraction of
    the normal input price for that cached portion, instead of paying full
    price for the same multi-thousand-token prompt every single time.
    """
    client = _get_anthropic_client()
    data, stop_reason = _stream_claude_tool_call(client, model, max_tokens, system_prompt, user_content)
    if stop_reason == "max_tokens":
        raise RuntimeError(
            "Claude's response was cut off (hit the token limit) before finishing - try a shorter "
            "document/URL, or this may need a higher max_tokens setting."
        )
    return data


MODALITY_DETECTION_PROMPT = """You are checking whether a DMC supplier document/page describes pricing for
MULTIPLE distinct room/cabin/ticket categories (e.g. "Standard Cabin", "Deluxe Cabin", "Suite" each with
their own price table) for what is otherwise the SAME single tour/ticket product.

This is DIFFERENT from checking for tour variants (different itineraries/durations) - here we're looking
for multiple PRICING CATEGORIES within the same product that would each need to become a separate
Modality/Option in Travel Compositor.

CRITICAL - CONFIRMED REAL FAILURE TO AVOID: "suggested_code" gets sent DIRECTLY to Travel Compositor's
API as the Modality's identifier - it must be SHORT and CLEAN, just the category name itself, nothing
else. A real production error was caused by a suggested_code of "Standard English min. 2 people" (the
API rejected it outright: "Modality code ... not found in contract modalities") - that happened because
extra descriptive text (the language, a minimum-pax note, pricing-table wording) got folded into the code
instead of being left out. NEVER include: parenthetical/explanatory text, numbers describing
pax/occupancy/min-max requirements, the word "people"/"pax"/"person", periods, or the language name
(e.g. "English") unless the language IS the only thing distinguishing this category from another one at
the same tier. Correct: "Standard", "Superior", "Deluxe", "Suite". Wrong: "Standard English min. 2
people", "Superior Cabin (2-4 pax)", "Deluxe - Ocean View, min 2 guests". If the source labels a category
with extra words beyond the core tier name, extract ONLY the tier name itself for suggested_code, and put
any of that other descriptive detail (language, occupancy notes) in "label" instead, where it's just
informational and never sent to the API as-is.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_modalities": true or false,
  "modalities": [
    {"label": "short human-readable label, e.g. 'Standard Cabin (English)'", "suggested_code": "e.g. 'Standard' - ONLY the core tier name, no / + - characters, no extra words"}
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


MODALITY_MATCH_PROMPT = """You are matching NEWLY-DETECTED pricing categories from an updated DMC document
against the modality/option codes that ALREADY EXIST live in Travel Compositor for this same tour/ticket.

You'll be given:
- EXISTING CODES: the modality/option codes already live in Travel Compositor for this tour/ticket.
- NEW CANDIDATES: pricing categories just detected in the updated source document, each with a label.

For each NEW CANDIDATE, decide whether it's an UPDATE to one of the EXISTING CODES (the same room/cabin/
ticket category, just refreshed pricing) or a genuinely NEW modality that doesn't exist yet. Match on
MEANING, not exact string equality - e.g. a candidate labeled "Standard Cabin" should match an existing
code like "Standard" or "STD_CABIN" if that's clearly the same category, even though the text differs.
Only propose a match when you're reasonably confident it's the SAME category - if in doubt, mark it as
new rather than guessing wrong (a human always confirms or corrects every suggestion before anything is
applied, so it is safe - and preferred - to be cautious and mark something "new" when unsure).

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "matches": [
    {"candidate_label": "<the candidate's label, verbatim>", "matched_existing_code": "<one of the EXISTING CODES, or null if this is a new modality>", "confidence": "high" or "medium" or "low", "reasoning": "one short sentence"}
  ]
}
Every NEW CANDIDATE must appear exactly once in "matches"."""


def match_modalities_to_existing(existing_codes: list, candidates: list, model: str = HAIKU_MODEL) -> list:
    """
    AI-ASSISTED, NEVER AUTO-APPLIED matching of newly-detected modality
    candidates (from detect_multiple_modalities) against the modality codes
    already live for this tour/ticket. Both ContractClosedTourVO and
    ContractTicketVO expose a plain `modalityCodes: List[str]` on their GET
    response (confirmed via schemas.py) with no separate human-readable
    name field, so the codes themselves ARE the identifier to match against.

    Per the confirmed "AI matches, human confirms" design (this is a
    deliberate product decision, not a shortcut): this function only ever
    PROPOSES a match - it never decides anything on its own. The caller
    must always surface every suggestion to a human for explicit
    confirm/override before treating any candidate as an update-to-existing
    (PUT) vs. a genuinely new modality (POST). Never wire this function's
    output straight into a publish call without a human confirmation step
    in between.

    Returns a list of dicts, one per candidate, in the same order as
    `candidates`: {"candidate_label", "matched_existing_code" (a string
    from `existing_codes`, or None), "confidence" ("high"/"medium"/"low"),
    "reasoning"}. Falls back to "no match" (every candidate treated as new)
    if there are no existing codes to match against, or if the AI call
    fails outright - a safe default, since "new" just means the human sees
    an extra modality to confirm, never a silently-skipped update.
    """
    if not candidates:
        return []
    if not existing_codes:
        return [
            {"candidate_label": c.get("label", ""), "matched_existing_code": None,
             "confidence": "high", "reasoning": "No existing modalities to match against - this tour/ticket has none yet."}
            for c in candidates
        ]
    print(f"🔎 Matching {len(candidates)} newly-detected modalit{'y' if len(candidates) == 1 else 'ies'} "
          f"against {len(existing_codes)} existing code(s)...")
    user_content = (
        "EXISTING CODES:\n" + "\n".join(f"- {c}" for c in existing_codes) +
        "\n\nNEW CANDIDATES:\n" + "\n".join(f"- {c.get('label', '')}" for c in candidates)
    )
    try:
        result = _call_claude(MODALITY_MATCH_PROMPT, user_content, model, max_tokens=1024)
    except Exception as e:
        print(f"⚠️ Modality matching call failed ({e}) - treating all candidates as new; a human can still "
              "manually pick an existing code to update instead.")
        return [
            {"candidate_label": c.get("label", ""), "matched_existing_code": None,
             "confidence": "low", "reasoning": f"AI matching failed ({e}) - defaulted to 'new', please check manually."}
            for c in candidates
        ]
    matches = result.get("matches", [])
    # Guard against the AI dropping/renaming a candidate, or hallucinating a
    # code that isn't actually in existing_codes - never trust blindly, and
    # always return exactly one entry per input candidate in order.
    by_label = {m.get("candidate_label"): m for m in matches if isinstance(m, dict)}
    safe_matches = []
    for c in candidates:
        label = c.get("label", "")
        m = by_label.get(label)
        matched_code = m.get("matched_existing_code") if m else None
        if matched_code not in existing_codes:
            matched_code = None
        safe_matches.append({
            "candidate_label": label,
            "matched_existing_code": matched_code,
            "confidence": (m.get("confidence") if m else None) or "low",
            "reasoning": (m.get("reasoning") if m else None) or "No AI suggestion returned for this candidate - please check manually.",
        })
    if safe_matches:
        matched_n = len([m for m in safe_matches if m["matched_existing_code"]])
        print(f"✅ {matched_n} of {len(safe_matches)} candidate(s) suggested as updates to an existing code "
              f"(pending human confirmation); {len(safe_matches) - matched_n} suggested as new.")
    return safe_matches


def detect_tour_variants(raw_text: str, model: str = "claude-sonnet-5") -> list:
    """
    Checks whether the source text describes ONE tour or MULTIPLE distinct
    variants (e.g. a 3-night and 4-night version of the same cruise).
    Returns an empty list if there's just one tour, or a list of
    {"label": ..., "nights": ...} dicts if genuinely multiple are found.

    CONFIRMED: upgraded from HAIKU_MODEL to the main model after a real
    misdetection - a document with ONE itinerary but 2 Modality-tiered
    pricing blocks ("Standard | English" / "Superior | English", same
    duration) got wrongly split into "2 tour variants". Getting this call
    wrong is more consequential than most other detection calls in this
    file (it decides whether the app tries to create ONE ClosedTour or
    SEVERAL), so it's worth the extra cost/latency of the stronger model.
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
        "You MUST call the apply_changes tool exactly once to respond - never respond with plain text. "
        "The tool call has TWO required arguments, and you must always provide BOTH, with no exceptions:\n"
        "1. summary (string, REQUIRED, never omit): a plain-text explanation of what you understood and "
        "changed, or your answer if it was just a question. This is shown directly to the human, so it "
        "must never be empty - even for a pure question with zero data changes, still explain your answer "
        "here.\n"
        "2. changes (object, REQUIRED, may be empty {} for a pure question): ONLY the fields that actually "
        "need to change, with their NEW full value. If the human asked for a concrete change (a date, "
        "price, name, day, etc.), 'changes' must contain that field with its corrected value - an empty "
        "'changes' object when a concrete edit was requested means you failed the task. Field names and "
        "value shapes must exactly match the current extracted data's own structure (e.g. price_list is "
        "the same array-of-objects shape, operational_days is the same list of weekday names)."
    )
    # The source document text is put in its OWN cacheable content block,
    # separate from the (frequently-changing) current-data/instruction block.
    # It stays IDENTICAL across every "Tell AI what to fix" call on the same
    # item during a review session, so Anthropic's prompt caching means only
    # the first call in that session pays full price for it - every
    # follow-up question/fix on the same item reuses the cached copy instead
    # of resending it at full cost.
    user_content = [
        {"type": "text", "text": f"--- Source document text ---\n{raw_text[:15000]}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": (
            f"--- Currently extracted data ---\n{json.dumps(current_data, indent=2)[:20000]}\n\n"
            f"--- Human's message ---\n{instruction}"
        )},
    ]
    try:
        client = _get_anthropic_client()
        # Tool-call forcing (see _stream_claude_tool_call) rather than free-text
        # JSON - same reasoning as _call_claude: a "changes" payload can contain
        # arbitrary long text (e.g. a corrected description) that's exactly the
        # kind of content prone to breaking free-text JSON parsing.
        result, _ = _stream_claude_tool_call(client, model, 4096, system_prompt, user_content, tool_name="apply_changes", input_schema=CLARIFY_TOOL_SCHEMA)

        # SAFETY NET: a strict input_schema with "required" is a strong hint to
        # the model, but the Anthropic API does NOT hard-enforce "required" on
        # tool_use output - the model can still technically omit a key. If that
        # happens, retry ONCE with an explicit corrective instruction rather
        # than silently showing an unhelpful placeholder - confirmed this was
        # happening even with the strict schema in place.
        if not (result.get("summary") or "").strip():
            corrective_user_content = user_content + [{
                "type": "text",
                "text": ("Your previous tool call omitted the required 'summary' field (or left it blank) - "
                         "this is not allowed. Call apply_changes again, this time including a real, "
                         "non-empty 'summary' explaining what you understood/changed or answered, "
                         "alongside 'changes'."),
            }]
            result, _ = _stream_claude_tool_call(client, model, 4096, system_prompt, corrective_user_content,
                                                 tool_name="apply_changes", input_schema=CLARIFY_TOOL_SCHEMA)

        if not (result.get("summary") or "").strip():
            # Both attempts failed to include a summary - build a factual one
            # from whatever we DO have rather than a dead-end placeholder.
            if result.get("changes"):
                result["summary"] = f"Applied changes to: {', '.join(result['changes'].keys())}."
            else:
                result["summary"] = ("I processed your message but didn't return a clear summary - please "
                                     "check above whether anything changed, or try rephrasing your request.")
        if "changes" not in result or not isinstance(result["changes"], dict):
            result["changes"] = {}
        return result
    except Exception as e:
        return {"summary": f"Couldn't process that request - {friendly_error_message(e)}", "changes": {}}


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
    # Same cacheable-source-text pattern as apply_clarification() above -
    # repeat questions about the same item during one review session reuse
    # the cached source text instead of paying full price for it each time.
    user_content = [
        {"type": "text", "text": f"--- Source document text ---\n{raw_text[:15000]}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": (
            f"--- Currently extracted data ---\n{json.dumps(current_data, indent=2)[:5000]}\n\n"
            f"--- Human's question ---\n{question}"
        )},
    ]
    try:
        client = _get_anthropic_client()
        raw_response, _ = _stream_claude_message(client, model, 1024, system_prompt, user_content)
        return raw_response.strip() or "(No answer returned.)"
    except Exception as e:
        return f"Couldn't get an answer - {friendly_error_message(e)}"


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
- schedule_notes: plain-English description of departure timing/pattern if mentioned (e.g. "departs every Monday", "runs only on specific dates in the schedule table") - informational only. NEVER include an instruction telling the customer to contact the operator/supplier directly (e.g. "contact the operator 48h before to confirm pick-up time") - Momira is the client-facing operator, not this DMC supplier, so silently drop that kind of text if present.
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


MODALITY_EXTRACTION_SYSTEM_PROMPT = """You are extracting PRICING/SCHEDULE data for ONE SPECIFIC Modality
(room/cabin/pricing category - e.g. "Standard", "Superior", "Deluxe") of a Travel Compositor ClosedTour,
from a DMC supplier document that may describe multiple such Modalities for the same tour. Focus ONLY on
the pricing table(s), supplements, and schedule information for the Modality named in the human guidance
you're given - IGNORE pricing/supplements that are clearly labeled as belonging to a DIFFERENT named
Modality/room category in the same document (e.g. if you're asked for "Superior", ignore anything
explicitly tied to "Standard" or "Deluxe" instead).

This is NOT a full tour extraction - do NOT extract tour name, description, itinerary, hotels,
included/excluded, or meeting point/policy remarks - those are handled separately, ONCE, for the whole
tour (not per Modality).

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly (e.g. "Please contact the operator 48 hours before your tour date to confirm your pick-up time").
Momira Travel is the tour operator the client actually deals with - the client must NEVER be told to
contact the DMC/supplier directly. Silently drop/omit this kind of text if present, anywhere it appears.

Extract:
- price_list: the pricing table(s) for THIS Modality only. Use this EXACT shape per entry (confirmed against the real API schema):
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
- supplements: TRUE OPTIONAL add-ons the customer only pays for if they choose them (upgrades, optional excursions), OR a peak-season/holiday surcharge, that apply SPECIFICALLY to bookings of THIS Modality. Do NOT include anything already covered in included/excluded, and do NOT include a supplement that the source clearly ties to a DIFFERENT Modality.
  CRITICAL - IGNORE voluntary carbon offset/carbon emission compensation charges entirely (e.g. "Optional CO2 offset contribution") - never add these as a supplement. This is a deliberate exclusion, not an oversight.
  CRITICAL - CONFIRMED RULE: only add a peak-season/holiday surcharge if the source genuinely mentions one for THIS Modality - never invent one "just in case". When it does, ALWAYS model it as its own supplement with "mandatory": true and a real travel_start_date/travel_end_date (never a separate price_list row, never an empty date range). This supplement OVERLAYS the normal price as an ADDITIONAL charge for bookings inside that date range. If the source only names a season/holiday without exact dates, use your best real-world date range and say so in pricing_notes.
  CONFIRMED BASIS RULE - how the surcharge is phrased in the source decides BOTH the "price" number AND the "per_pax" flag; getting the combination wrong over- or under-charges the customer:
  - "per stay" / a flat one-time amount (neither "per person" nor "per night"): price = the stated flat amount, per_pax: false. Never multiply.
  - "per person" (and NOT also "per night"): price = the stated per-person amount as-is - do NOT multiply by a pax count. Set per_pax: true so Travel Compositor's own booking engine multiplies it by however many travelers actually book (pax is a min/max range at extraction time, never one fixed number).
  - "per night" (and NOT also "per person") - e.g. "USD 11 per night surcharge during peak season": Travel Compositor's schema has no native "per night" concept, so YOU must pre-multiply. price = the per-night rate x the actual number of affected nights within THIS Modality's own stay{tour_nights_clause}, capped at that length. per_pax: false. CRITICAL SELF-CHECK: verify you multiplied rate x nights and didn't just copy the per-night rate as the total.
  - "per person per night": combine the two rules above - price = per-night rate x actual affected nights ONLY (pre-calculated by you), then per_pax: true so Travel Compositor further multiplies by the actual booked pax count.
  - "per room" / "per room per night" - e.g. "USD 71.00 per room per night" (a flat charge for the WHOLE room, not per traveler): compute the TOTAL charge for the whole room for the whole stay - the per-room rate x the actual affected nights (same nights rule as above; if "per room" with no "per night" attached, treat as already a flat one-time per-room total). Then divide that SAME total by 1, 2, 3, and 4 to get single_price/double_price/triple_price/quadruple_price - e.g. rate $71 x 3 nights = $213 total, so single_price=213, double_price=106.50, triple_price=71, quadruple_price=53.25. Set per_pax: false and put double_price in "price" too. CRITICAL SELF-CHECK: verify all four occupancy amounts come from dividing the SAME total by 1/2/3/4 - never compute them independently.
  - Whole-trip/percentage surcharges (e.g. "20% higher during Christmas"): pre-calculate an actual currency amount (e.g. 20% of the base per-person price) into "price", with per_pax: true.
  - For every basis OTHER than "per room"/"per room per night", set single_price = double_price = triple_price = quadruple_price = "price" (the per-person amount is the same regardless of occupancy).
  CRITICAL - CONFIRMED RULE: for a peak-season/holiday surcharge, "name" must stay a clean customer-facing label ONLY (e.g. "Peak Season Surcharge") - NEVER include the price/percentage/calculation in the name. Put the calculation itself in pricing_notes instead, never in the name.
  For each TRUE supplement, output:
  {
    "name": "clear, specific short label - always required, never leave blank",
    "price": per-person amount as a number,
    "single_price": per-person amount for SINGLE occupancy - equal to "price" unless a "per room" surcharge, see BASIS RULE,
    "double_price": per-person amount for DOUBLE occupancy - equal to "price" unless a "per room" surcharge,
    "triple_price": per-person amount for TRIPLE occupancy - equal to "price" unless a "per room" surcharge,
    "quadruple_price": per-person amount for QUADRUPLE occupancy - equal to "price" unless a "per room" surcharge,
    "per_pax": true if per-traveler (normal case), false if flat/one-time or a "per room" surcharge,
    "mandatory": true if required or a peak-season surcharge, false for a normal optional add-on,
    "on_request": true if the source says this needs advance request/confirmation,
    "travel_start_date": "YYYY-MM-DD" if restricted to a date range - ALWAYS required for a peak-season surcharge, otherwise empty,
    "travel_end_date": "YYYY-MM-DD" - same condition as above
  }
  If nothing optional with its own price is mentioned for this Modality, leave this as an empty list.
- pricing_notes: leave empty UNLESS you had to approximate/drop something fitting a table into the 4-slot schema, or note a peak-season surcharge calculation - explain exactly what, with real numbers.
- schedule_notes: plain-English description of departure timing/pattern if mentioned - informational only.
- operational_days: your best guess at which weekdays this departs on, as a list of uppercase weekday names. If genuinely unclear, return all 7 days.
- stop_sales: array of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} for any explicitly mentioned blackout/non-operating date ranges. Empty list if none.

Never invent numbers or dates not actually present in the source. If pricing is vague or absent, return an empty price_list rather than guessing.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "price_list": [], "supplements": [], "pricing_notes": "", "schedule_notes": "",
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "stop_sales": []
}"""


def extract_modality_data(raw_text: str, model: str = "claude-sonnet-5", human_hint: str = None, tour_nights=None) -> dict:
    """
    Focused per-Modality extraction for the NEW single-tour ClosedTour create
    flow (each Modality reviewed individually - see app.py's render_multi_tour_flow):
    pulls price_list, supplements, operational_days and stop_sales for ONE
    named Modality only. Unlike extract_option_only_data (used by the
    separate add-a-modality-to-an-existing-tour flow), this DOES extract
    supplements - each Modality is reviewed on its own screen now, so its
    supplements are naturally scoped to it by construction, with no need for
    the AI to guess/tag which Modality a supplement belongs to.

    tour_nights: the ALREADY-CONFIRMED tour length (from the one main
    extraction done once for the whole tour) - passed through as known
    context so "per night" surcharge math is anchored to the real tour
    length instead of the AI having to (potentially wrongly) re-derive it
    from a pricing-only source snippet.
    """
    tour_nights_clause = f" (this tour is confirmed to be {tour_nights} nights long)" if tour_nights else ""
    system_prompt = MODALITY_EXTRACTION_SYSTEM_PROMPT.replace("{tour_nights_clause}", tour_nights_clause)

    user_content = raw_text
    if human_hint:
        user_content = (
            f"IMPORTANT: focus ONLY on the Modality/pricing category matching this guidance, ignore all "
            f"others in the document: {human_hint}\n\n--- Source content ---\n{raw_text}"
        )

    data = _call_claude(system_prompt, user_content, model, max_tokens=4096)

    defaults = {
        "price_list": [], "supplements": [], "pricing_notes": "", "schedule_notes": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "stop_sales": [],
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    # Defensive per-supplement defaults, same convention as extract_structured_data.
    for _s in data.get("supplements") or []:
        if not isinstance(_s, dict):
            continue
        _sanitize_supplement_price_fields(_s)
        _flat_price = _s.get("price", 0) or 0
        for _occ_key in ("single_price", "double_price", "triple_price", "quadruple_price"):
            if _s.get(_occ_key) is None:
                _s[_occ_key] = _flat_price

    return data


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
    # 32768 (up from a previous 16384) - confirmed against a real failure where
    # a longer multi-day tour's response got cut off mid-JSON at the old limit.
    # Sonnet 5 supports up to 128k output tokens on the synchronous API, so
    # this has plenty of headroom without needing any beta header.
    data = _call_claude(EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=32768)

    # Defensive defaults in case the model omits a key
    defaults = {
        "tour_name": "", "description": "", "hotels_text": "", "hotels_count": 1, "supplements": [], "included": "",
        "excluded": "", "meeting_point": "", "policy_remarks": "",
        "itinerary_destinations": [], "nights": 0, "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "pricing_notes": "", "stop_sales": [], "price_list": [], "release_days_mentions": []
    }
    defaults.update(data)

    # Defensive per-supplement defaults (in case the model omits a field despite
    # the prompt's instructions above) - "applies_to" defaults to "ALL" (today's
    # prior all-modalities behavior) rather than blank/missing, and the occupancy
    # price fields default to the flat "price" so older-shaped responses still
    # behave exactly as before this per-occupancy pricing was added.
    for _s in defaults.get("supplements") or []:
        if not isinstance(_s, dict):
            continue
        _sanitize_supplement_price_fields(_s)
        _flat_price = _s.get("price", 0) or 0
        _s.setdefault("applies_to", "ALL")
        for _occ_key in ("single_price", "double_price", "triple_price", "quadruple_price"):
            if _s.get(_occ_key) is None:
                _s[_occ_key] = _flat_price

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

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly (e.g. "Please contact the operator 48 hours before your tour date to confirm your pick-up time,
and note that the starting time and duration may vary according to traffic, weather and operational
conditions", "Contact us to confirm timing", "Call the supplier to reconfirm your booking"). Momira Travel
is the tour operator the client actually deals with - the client must NEVER be told to contact the
DMC/supplier directly, since that DMC is Momira's backend supplier, not the client-facing operator. This
applies EVERYWHERE such an instruction could appear - description, includes/excludes, meeting points,
pricing_notes, schedule_notes, anywhere - silently drop/omit it entirely rather than including,
paraphrasing, or softening it. This is a deliberate exclusion, not an oversight.

Extract:
- ticket_name: the excursion/activity name - keep close to the source, don't invent a fancier title.
- description: a SINGLE HTML block (not day-by-day) describing what the experience involves, written as
  natural, engaging, SEO-strong prose - the goal is compelling copy that reads well and ranks well, NOT
  a bare fact list. However, it must never lie or exaggerate: use ONLY facts, places, and activities
  actually present in the source - never invent details, ratings, superlatives, or claims that aren't
  there. Good SEO writing and factual accuracy are not in tension - rewrite HOW it's said, never WHAT is
  true. Format: <p>paragraph(s)</p> - keep it to 2-4 short paragraphs maximum.
  PRE-ARRIVAL ADVISORY: if the source mentions an important advisory affecting the whole booking that the
  traveler must know/do BEFORE or independent of the activity itself (e.g. a required overnight stay
  beforehand, an early arrival/check-in requirement, a strong advisory about timing), put this as its own
  standalone paragraph at the VERY END of the description - after the normal descriptive paragraphs, so
  it is always the LAST paragraph. Write it as PLAIN text only: NO icon/emoji (e.g. no "⚠️") and NO
  "Important:" label or other bold prefix - those have caused downstream coding/encoding issues, so the
  paragraph must contain nothing but the advisory sentence itself, e.g. <p>...</p>. Only add it if the
  source genuinely contains such an advisory - never invent one.
- city: the single city/location where this takes place (a plain place name, e.g. "Tokyo") - this
  will be resolved to real coordinates separately, so use the exact place name as commonly known.
- includes: a LIST of plain strings (not HTML) - each a short inclusion, e.g. ["Official Voucher", "Handling Fee"]
  GUIDE LANGUAGE RULE (same principle as tours): if a base/standard guide language is mentioned (usually
  English), make sure it's explicitly listed here (e.g. "English-speaking guide"). If OTHER languages are
  available (e.g. "German/French on request"), do NOT list them here - add each as its own supplement
  instead (see below) so guests clearly see the option, e.g. {"name": "German-speaking guide (upon
  request)", "adult_price": 0 unless a price is stated, "children_price": 0, "infant_price": 0}.
  DUAL-LANGUAGE GUIDE RULE: this is a DIFFERENT case from the one above - if the source lists TWO (or
  more) languages joined by "/" or "or" as EQUAL standard options for the guiding/transfer service (e.g.
  "licenced English/German-speaking guiding service", "English or German speaking guide"), that is NOT a
  base-language-plus-on-request-extra - both languages are included as standard, so keep the includes
  entry as the source states it (e.g. "Licenced English/German-speaking guiding service") rather than
  splitting one off into supplements. In this case the description ALSO MUST clearly state, in plain
  words, that the tour/transfer can run in EITHER English OR German - work one clear sentence to that
  effect into the description, e.g. "This transfer is guided in either English or German." Never leave it
  ambiguous or worded so it could be read as both languages being provided at once - the guest must
  clearly understand they get ONE of the two.
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
- is_private: true if the source describes this as a PRIVATE experience (e.g. "private tour", "private
  transfers", "private guide", "exclusively for your group") as opposed to a joint/shared/group/public
  one (e.g. "joint public tour", "shared transfer", "group tour"). This is a genuine selling point worth
  flagging clearly - a human will use it to label the Modality Code. If the source doesn't specify either
  way, default to false rather than guessing.
- base_adult_price, base_children_price, base_infant_price: the core prices found in the source, as numbers.
  CRITICAL RULE for base_children_price specifically: if children are allowed (not disallow_children) but
  the source gives only ONE price with no distinct child rate, set base_children_price EQUAL to
  base_adult_price - NOT 0. A price of 0 specifically means "this passenger type travels free", so only
  use 0 if the source EXPLICITLY says children are free/complimentary, or if disallow_children is true.
  Simply not mentioning a separate child price is NOT the same as saying children are free - it means
  they pay the standard (adult) rate, which is the far more common real-world case.
  base_infant_price commonly IS genuinely 0 by convention (infants often travel free) - only set it
  non-zero if the source states an actual infant price.
  If pricing is genuinely absent/blank in the source (e.g. a rate table with no values filled in yet),
  leave base_adult_price (and therefore base_children_price too, per the rule above) as 0 - do NOT invent
  numbers - and mention this clearly in pricing_notes.
- child_age_min, child_age_max: the age range that counts as "child" pricing, AND/OR any stated age
  eligibility restriction (e.g. "children must be at least 12", "not applicable for children under 12
  years old", "minimum age: 12", "age limit: 12"). Both kinds of language should populate these fields -
  use whichever is most specific to what's actually stated. This has been missed before, so actively
  look for it anywhere in the source (a "Good to know" section counts, not just a pricing table), else
  2/12 as the standard default (infant = 0-2, child = 2-12) - same convention used for ClosedTours.
- disallow_adult, disallow_children, disallow_infant: true only if the source explicitly says a passenger
  type isn't allowed (rare) - otherwise all false.
- operational_days: list of uppercase weekday names this is available. CRITICAL - THIS IS OFTEN MISSED:
  actively search the ENTIRE source for weekday schedule information (not just an obvious "Schedule"
  section) - phrases like "operated ... as following schedules:", "departs on", "available every", or a
  bare list of weekday names near the tour description all count. Only fall back to all 7 days if the
  source is GENUINELY silent about which days it runs (e.g. truly says "daily", or says nothing about
  scheduling at all) - never default to all 7 just because the schedule is stated in an unusual place or
  a slightly indirect format.
  MULTI-LANGUAGE SCHEDULE RULE: if the source gives a SEPARATE weekday schedule per guide language (e.g.
  "English-speaking guide (am.): Mondays, Wednesdays, Fridays, Saturdays and Sundays" / "German-speaking
  guide (am.): Mondays, Wednesdays, Fridays and Sundays"), this ticket's base operational_days MUST be set
  to the schedule for the STANDARD/base language only (see the GUIDE LANGUAGE RULE above - normally
  English, or whichever language has no separate on-request supplement). Do NOT merge/union the different
  languages' day-sets together, and do NOT default to all 7 - pick the exact days listed for the base
  language's schedule. Mention the other language(s)' different schedule explicitly in schedule_notes so
  the human sees the discrepancy (e.g. "German-speaking guide runs Mon/Wed/Fri/Sun only - narrower than
  the English schedule used for operational_days above").
- schedule_notes: if the source says operational days are NOT YET DETERMINED (e.g. "TBD by Operations",
  "to be confirmed"), say so plainly here so the human knows operational_days is a placeholder default,
  not a real confirmed schedule. Also use this field for the multi-language schedule discrepancy note
  described above. Empty string otherwise.
- time_tables: list of specific departure/start times as strings (e.g. ["09:00", "14:00"]) if the source
  gives specific time slots - empty list if not applicable.
- start_date, end_date: the validity date range for this specific modality/price (YYYY-MM-DD). If the
  source gives no clear range, use a wide default like today's year to 3 years out.
- adult_taxes_amount, child_taxes_amount, infant_taxes_amount: any separately-stated taxes/fees, else 0.
- supplements: ONLY genuinely simple, ALWAYS-AVAILABLE, independently-stackable optional add-ons (e.g.
  "Add English audio guide - $5", "Extra photo package - $15").
  CRITICAL - IGNORE voluntary carbon offset/carbon emission compensation charges entirely (e.g. "Optional
  CO2 offset contribution - 5 EUR", "Carbon footprint compensation", "voluntary climate contribution") -
  never add these as a supplement or anywhere else in the extracted data, even though they're technically
  optional and priced. This is a deliberate exclusion, not an oversight.
  CONFIRMED REAL CONSTRAINTS - Ticket
  Supplements are structurally different from ClosedTour ones:
  (1) There is NO "on request" concept for Ticket supplements at all - never describe one as needing
      special confirmation/availability check, since the schema has no field for that.
  (2) Supplements are independently stackable - the customer can tick ANY combination of them, and
      their prices simply ADD UP. NEVER create two or more supplements that are meant to be mutually
      EXCLUSIVE alternatives (e.g. two different "peak season surcharge" options, or "Option A" vs
      "Option B" pricing) - a customer could select both by mistake and get double-charged. If the
      source describes alternative/exclusive options, or anything needing on-request handling, this
      must become a SEPARATE MODALITY instead - flag this clearly in pricing_notes, explaining what
      the separate modality should be (e.g. "Create a second Modality 'Deluxe with private guide' for
      this on-request option - it cannot be a supplement").
  (3) Peak season/holiday surcharges - CONFIRMED RULE: this ONLY applies if the source ITSELF explicitly
      mentions a peak season/holiday/seasonal surcharge somewhere - NEVER invent or add one that isn't
      actually mentioned; if the source says nothing about a seasonal surcharge, don't create one "just in
      case". When (and ONLY when) the source DOES mention one, ALWAYS model it as a supplement with a real
      travel_start_date/travel_end_date, whether the surcharge is whole-trip/percentage-based (e.g. "20%
      higher during Christmas") or per-night/per-component (e.g. "USD 11 per person per night surcharge
      during peak season"). Never leave the date range empty for one of these - if the source only names a
      season/holiday without exact dates, use your best real-world date range for that period and note the
      assumption in pricing_notes. This supplement OVERLAYS the normal modality price - it's an ADDITIONAL
      charge for bookings that fall inside that date range, not a replacement/alternative price.
      CONFIRMED BASIS RULE (same principle as ClosedTour, adapted to Tickets' schema):
      - "per person" (the default/normal case for Tickets - adult_price/children_price/infant_price are
        ALREADY inherently per-person-of-that-type by definition; Travel Compositor charges each booked
        adult/child/infant that amount automatically): just use the stated per-person amounts as-is - no
        multiplication needed, and there's no separate per_pax-style flag to set for Tickets.
      - "per night" - e.g. "USD 11 per person per night surcharge during peak season": Ticket supplements
        have no native "per night" concept either, so pre-calculate the TOTAL by multiplying the per-night
        rate by the actual number of nights/units the surcharge period overlaps with THIS ticket's own
        duration (capped at the ticket's own length, exactly like the ClosedTour tour-length cap above -
        never just the surcharge period's full length). Same CRITICAL SELF-CHECK as ClosedTour above:
        verify you multiplied rate x nights/units and did not just copy the per-night rate as the total.
        The per-person part is already handled by the adult/children/infant price fields themselves - only
        the nights dimension needs your manual multiplication.
      - "per stay" / a flat one-time amount not tied to passenger type: Ticket supplements have NO flat/
        one-time option in this schema (every field is inherently per-passenger-type) - if the source
        genuinely describes a flat per-booking surcharge, put your best per-person estimate in each of
        adult_price/children_price/infant_price and clearly flag in pricing_notes that the source
        described a flat per-booking charge that had to be approximated as per-person, so a human can
        review and adjust if needed.
      - Whole-trip/percentage surcharges: pre-calculate an actual currency amount where possible (e.g. 20%
        of the base adult/child/infant prices) and put that resulting number in the price fields as
        normal. If a percentage genuinely can't be converted to a safe real amount, still create the
        supplement with your best estimate and flag it in pricing_notes.
      These always apply uniformly and don't compete with anything else, so they're safe as supplements
      even though (2) above forbids mutually-exclusive alternative supplements.
      CRITICAL - CONFIRMED RULE: the "name" must stay a clean, customer-facing label ONLY - e.g.
      "Christmas/New Year Surcharge" or "Peak Season Surcharge" - and must NEVER include the price,
      percentage, or the calculation (no "(20% of base price)", no "(2 nights x $11 = $22)", no dollar
      amounts of any kind in the name). The customer sees this name directly and must not be shown that
      price breakdown. The actual numbers still go in the adult_price/children_price/infant_price fields
      as always (that's what makes the surcharge real) - only put the calculation itself (the math you
      did, so a human can double-check it before publishing) in pricing_notes, never in the name. This
      does NOT apply to normal optional supplements - only to peak-season/holiday surcharges.
  (4) CRITICAL - a common real case: if the source has SEPARATE FULL PRICE TABLES per guide language
      (e.g. "English Speaking Guide" table with its own prices, then a separate "German Speaking Guide"
      table with DIFFERENT prices), each language is its OWN distinct product with its own real price -
      NEVER model different guide languages as a supplement (that would just add a small fee on top of
      one base price, which is wrong - each language has its own genuinely different full price). Extract
      the PRIMARY/first language's table as the main base_adult_price/base_children_price/base_infant_price,
      then list every OTHER language found with its own prices clearly in pricing_notes (e.g. "Also
      available: German Speaking Guide - Adult 91, Superior 100; French Speaking Guide - Adult 92..."),
      so a human can create each additional language as its own separate Modality.
  Each supplement: {"name": "label", "adult_price": number, "children_price": number,
  "infant_price": number, "travel_start_date": "YYYY-MM-DD", "travel_end_date": "YYYY-MM-DD"}. Empty list if none.
- occupancy_prices: ONLY populate if the human indicates Occupancy pricing mode is being used (this is
  separate from the default Distribution mode). If the source has a group-size-tiered price table
  (columns like "1", "2", "3-5", "6-8"), extract it here instead of forcing it into base_adult_price.
  CONFIRMED REAL SHAPE: each entry is {"occupancy": exact integer headcount, "amount": price for that
  exact headcount} - occupancy is an EXACT number, NOT a range. If the source shows a range at one
  price (e.g. "3-5" = $87), EXPAND it into one entry per exact number: {"occupancy":3,"amount":87},
  {"occupancy":4,"amount":87}, {"occupancy":5,"amount":87}. Infants are always free and excluded from
  occupancy counts - don't create entries for them. Leave empty for standard Distribution-mode pricing.
  NOTE: real documents commonly show "N/A" for the solo/1-pax column (only 2+ pax pricing is offered).
  If the source has no 1-pax price, do NOT invent one - just extract what's given, and mention in
  pricing_notes that a solo/1-pax price is missing and needs to be manually confirmed, since it can't be
  safely assumed from the other rows.
- pricing_notes: leave empty UNLESS something had to be approximated (e.g. a group-size-tiered price
  table forced onto adult/child/infant categories, pricing was genuinely absent from the source, a
  peak-season surcharge amount/date range had to be estimated, or an alternative/on-request option needs
  to become a separate Modality - see supplements rule above) - explain what, with real numbers where
  available, so a human can review.
- release_days_mentions: a list of integers - ANY explicit booking/reservation deadline or "release period"
  mentioned anywhere in the source (e.g. "must be booked at least 45 days before", "release period: 60
  days", "reservations required 30 days in advance"). DIFFERENT from a cancellation policy (e.g.
  "non-refundable inside 21 days") - do NOT include cancellation deadlines here, only booking/reservation/
  release deadlines. Convert weeks/months to days (e.g. "6 weeks" -> 42, "2 months" -> 60). If the source
  mentions MORE THAN ONE such deadline, include ALL of them as separate integers - a human will apply the
  safest (longest) one rather than you picking. Empty list if nothing like this is mentioned anywhere.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
  "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
  "activity_type": "", "is_private": false, "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "occupancy_prices": [],
  "child_age_min": 2, "child_age_max": 12, "disallow_adult": false, "disallow_children": false,
  "disallow_infant": false, "operational_days": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
  "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
  "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0, "supplements": [], "pricing_notes": "",
  "release_days_mentions": []
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
    {"label": "short human-readable label, e.g. 'City Tour in El Gouna'",
     "is_private": true if this specific excursion is described as private (private tour/transfer/guide),
     false if described as joint/shared/group/public, false if not specified either way}
  ]
}
If there is only one excursion, set "multiple_excursions": false and "excursions": [] ."""


def detect_ticket_variants(raw_text: str, model: str = HAIKU_MODEL) -> list:
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

    # 16384 (up from a previous 8192) for the same headroom reason as the
    # tour extraction above - Tickets are usually shorter, but a long
    # description + many occupancy/supplement rows could still hit the old cap.
    data = _call_claude(TICKET_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=16384)

    defaults = {
        "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
        "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
        "activity_type": None, "is_private": False, "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
        "child_age_min": 2, "child_age_max": 12, "disallow_adult": False, "disallow_children": False,
        "disallow_infant": False,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
        "adult_taxes_amount": 0, "child_taxes_amount": 0,
        "infant_taxes_amount": 0, "supplements": [], "pricing_notes": "", "stop_sales": [], "image_urls": [],
        "price_type": "OCCUPANCY", "base_service_price": 0, "occupancy_prices": [], "release_days_mentions": [],
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
supplements (simple, always-available, stackable add-ons only - never exclusive alternatives, different guide languages, or on-request items, see full prompt's rules on this - and NEVER voluntary carbon offset/emission compensation charges, ignore those entirely), pricing_notes.

CRITICAL - NEVER include an instruction telling the customer to contact the operator/supplier directly
(e.g. "contact the operator 48h before to confirm pick-up time") anywhere, including pricing_notes -
Momira is the client-facing operator, not this DMC supplier, so silently drop that kind of text if present.

operational_days: actively search the ENTIRE source for weekday schedule info, even if it's stated in an
unusual place or format - only default to all 7 days if the source is genuinely silent about which days
it runs. If the source gives a SEPARATE schedule per guide language and a human hint above names a
specific language/focus for THIS modality, use that language's exact days (not a union, not all 7). If no
hint narrows it down, use the base/standard language's schedule and note any other languages' differing
days in pricing_notes.

CRITICAL RULE for base_children_price: if the source gives only ONE price with no distinct child rate,
set base_children_price EQUAL to base_adult_price - NOT 0. A price of 0 specifically means "this
passenger type travels free" - only use 0 if the source EXPLICITLY says children are free/complimentary.
Not mentioning a separate child price is NOT the same as children being free - it means they pay the
standard rate. base_infant_price commonly IS genuinely 0 by convention - only set it non-zero if the
source states an actual infant price.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "child_age_min": 2, "child_age_max": 12, "start_date": "", "end_date": "",
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
        "child_age_min": 2, "child_age_max": 12, "start_date": "", "end_date": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "time_tables": [],
        "supplements": [], "pricing_notes": "", "stop_sales": [],
        "price_type": "OCCUPANCY", "base_service_price": 0, "occupancy_prices": [],
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
