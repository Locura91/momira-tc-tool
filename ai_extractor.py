"""
Uses the Claude API to turn raw, messy DMC document text (any language)
into structured English tour data, matching the shape builder.py expects.

Requires ANTHROPIC_API_KEY in .env (get one at console.anthropic.com).
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-16-outreach-stop-search-button"

import os
import re
import json
import math
import datetime
from typing import List, Tuple

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

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.

Rules:
- Translate ALL content into English, regardless of the source document's original language.
- Output ONLY valid JSON. No markdown code fences, no explanation, no preamble.
- Never fabricate information that isn't present in the source document. Use empty string "" or empty list [] for anything you can't determine.
- nights: the total number of overnight stays in the itinerary - count them directly from the day-by-day
  schedule (the number of distinct nights spent somewhere), not from a day-count the source's own title
  might use.
- CONFIRMED NAMING RULE - Nights vs Days: an itinerary of N nights always spans N+1 days (e.g. spending 4
  nights somewhere means the trip runs across 5 calendar days - day 1 you arrive, day 5 you depart). Apply
  this EVERY time a "Days" count is used to describe the trip's overall length - in tour_name (e.g. a
  4-night itinerary must be named "5 Days ..." not "4 Days ...") and anywhere the description refers to
  the whole trip's length in days (e.g. "this 5-day journey..."). This applies regardless of what the
  source document's own title/wording says - if the source itself is inconsistent (e.g. it calls a
  4-night itinerary a "4 Days" tour), correct it to the right day count (nights + 1) rather than copying
  the source's own possibly-wrong number. Concretely: a 4-night tour is a 5 Days tour; a 2-night tour is a
  3 Days tour; a 6-night tour is a 7 Days tour. This does NOT apply to single-day excursions with no
  overnight stay (0 nights) - those are never renamed to "1 Day" by this rule, since they're a different
  product type (Tickets) that doesn't use this convention.
- tour_name: keep the product name close to what the source calls it, but apply the Nights-vs-Days naming
  rule above if the source's title states a day count - fix it to nights + 1 if it doesn't already match.
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
  CRITICAL - CONFIRMED RULE (product owner, 2026-08-16): IGNORE Pre-night / Post-night (a.k.a. pre-tour or
  post-tour extra night, "Pre Night Option", "Post Night Option") mentions entirely - never add one as a
  ClosedTour supplement, even though the source prices it and it looks exactly like an optional add-on.
  Travel Compositor cannot calculate an extra night into a ClosedTour's own day count, so a Pre/Post night
  option has nowhere correct to live on this record - adding it as a supplement would let a client buy an
  extra night that never actually gets scheduled. This is a deliberate exclusion, not an oversight: skip
  it in supplements, and skip it everywhere else in the extracted data too (it is not part of included,
  excluded, or pricing_notes either) - simply leave the mention out, exactly like the carbon-offset rule
  above. Do NOT confuse this with a hotel EXTENSION that is itself sold as its own separate product/tour -
  this exclusion is specifically about a Pre/Post night OFFERED AS PART OF this ClosedTour's own document.
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
  ONE SUPPLEMENT LIST FOR THE WHOLE TOUR - CONFIRMED HOUSE RULE (product owner): "Supplement within
  ClosedTour is set only once and applies to ALL Modalities of the ClosedTour." So do NOT try to work
  out which room/cabin category a supplement belongs to, and do NOT repeat a supplement once per
  category. List each distinct supplement ONCE. An optional Abu Simbel excursion is offered to every
  guest on the tour whichever cabin they booked, and that is how supplements work here in general.
  If a document genuinely prices the SAME extra differently per category (e.g. a room upgrade that
  costs more from Deluxe than from Standard), that is not a supplement - say so in pricing_notes so a
  human can decide, rather than inventing one entry per category.
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
  any child discount PERCENTAGE or fee here (e.g. "children under 12 pay 50%", "child rate is 70% of
  adult price") - it's fine to keep non-monetary child age policy (e.g. "must be accompanied by an
  adult", "minimum age 12"), just never the supplier's stated discount percentage/fee itself. Also never
  put the supplier's cancellation policy here - see cancellation_policy_tiers/cancellation_policy_text
  below, cancellation gets its own dedicated fields instead of being mixed into policy_remarks.
  If the source's policy section is ONLY about cancellation or child pricing percentages, leave
  policy_remarks empty entirely rather than including any of it.
- cancellation_policy_tiers: CORRECTED RULE - this used to be wrongly treated as always a flat 30-days/
  100% default regardless of what the source said; that was wrong. Whenever the source states its OWN
  specific cancellation-fee policy - usually a tiered schedule like "From 91 days or more before arrival,
  25% ... From 90 to 61 days before arrival, 50% ... From 60 to 46 days, 75% ... From 45 days to the day
  of check-in, 100%" - extract EVERY tier as {"days": <the LOWER bound of days-before-arrival for this
  tier>, "fee_percentage": <the cancellation FEE percentage for this tier, exactly as the source states
  it - this is a fee/charge percentage, NOT a refund percentage>}. Use the LOWER bound of each day-range
  as "days" (e.g. "90 to 61 days before arrival" -> days=61, "60 to 46 days" -> days=46, "45 days to the
  day of check-in" -> days=0, "91 days or more" -> days=91). A separately-mentioned "no-show" fee does NOT
  need its own entry if it matches the final/most-expensive tier's percentage (it's already covered by
  the days=0 tier) - only give it a separate entry if it's numerically different from the final tier.
  If the source states NO specific cancellation policy anywhere, return an EMPTY list - do not invent
  one and do not assume any particular default; a blank list signals the system to keep its own existing
  default policy untouched.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy out as
  a short, clear, human-readable plain-text summary for staff/customer-facing display - one line per
  tier, e.g. "Cancellation Policy:\n- 91+ days before arrival: 25% fee\n- 90-61 days before arrival: 50%
  fee\n- 60-46 days before arrival: 75% fee\n- 45 days to check-in / no-show: 100% fee". If the source
  also describes a genuinely different rule for last-minute/close-in bookings that doesn't fit the
  day/percentage tier structure (e.g. "bookings made within 30 days of departure follow a different fee
  schedule based on the booking date"), add it as one extra trailing sentence. Leave this field empty
  whenever cancellation_policy_tiers is empty.
- min_child_age, max_child_age: the age range that counts as "child" for pricing.
  CONFIRMED HOUSE RULE (product owner): A STATED MINIMUM CHILD AGE REPLACES THE FLOOR, AND THE CEILING
  STAYS 12. "Children from 7 years" / "minimum age 7" / "children must be at least 7" / a rate column
  headed "Child 7-11" all mean min_child_age: 7, max_child_age: 12 - NOT 7/7, NOT 7/11, and never a
  band left at 2/12. Only lower or raise the CEILING if the source itself names a different upper age
  (e.g. "child rate applies up to 15" -> max_child_age: 15).
  READ IT FROM ANYWHERE IT APPEARS: a pricing table's column heading ("Child (7-11.99 yrs)"), a
  "Good to know"/notes section, a age-policy paragraph, a footnote. It has been missed before because
  it was only looked for in one of those places.
  WHAT THE MINIMUM ACTUALLY DOES: everyone below min_child_age is an INFANT, not a forbidden guest.
  So "children from 7" makes 0-7 the infant band. If the source instead says under-7s CANNOT join at
  all (e.g. "not suitable for children under 7", "no children under 7 accepted"), that is a booking
  restriction, not an age band: still set min_child_age to 7, AND state the restriction plainly in the
  description so a human can see it, since these two numbers cannot express "not accepted".
  Default to 2/12 ONLY if genuinely nothing about child age appears anywhere in the source (standard
  convention: infant = 0-2, child = 2-12).
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
  CONFIRMED RULE (product owner, 2026-08-14) - vague time-of-day wording, no exact clock time anywhere:
  WHY THIS MATTERS - Travel Compositor uses a ClosedTour's own start_time/end_time to check whether a
  customer combining this tour with a Transport can actually catch it: it compares the two and expects
  roughly a 2-hour gap, so a blank time here breaks that check entirely and can silently let a customer
  book a transport they cannot make. A vague-but-real time-of-day mention is still useful information and
  must not be thrown away just because it isn't an exact clock time.
    * start_time - Day 1's description says the tour leaves in the "early morning" (or equivalent: "morning
      departure", "departs early", "before sunrise") with NO exact clock time given anywhere else in the
      source: default start_time to "07:00:00". Only use this default when truly nothing more specific is
      stated - a real pick-up time anywhere in the source (the rule above) always wins, and so does the
      pre-arrival-advisory default above when that specific situation applies instead.
    * end_time - the final day's description gives an APPROXIMATE arrival/return time (e.g. "arriving
      approximately at 6pm", "you'll arrive back around 21:00", "return to your hotel by approximately
      9:00 PM") - this counts as a stated time just as much as an exact one. Extract the hour given (round
      to the nearest half hour if the source itself is only that precise) as end_time - do not leave it
      blank just because the source hedges it with "approximately"/"around"/"about".
  Leave start_time/end_time as empty strings only if NEITHER a real time, an approximate time, NOR (for
  start_time) one of the two defaults above applies.
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

  ============================================================
  PER-NIGHT PRICING - CONFIRMED HOUSE RULE (product owner), and the single most repeated
  correction on this platform. It applies to Nile cruises above all, and to any ClosedTour whose
  rates are quoted BY THE NIGHT rather than as a total for the trip.
  When the document gives a per-night rate (e.g. "USD 250 per night", "250 / night / cabin"):
      singlePrice = (number of nights on THIS tour) x (per-night rate)
      doublePrice = (number of nights x per-night rate) / 2
  The division is not a discount - the quoted rate buys the CABIN, and two people sharing it each
  pay half. So a 4-night cruise at USD 250 per night is singlePrice 1000, doublePrice 500.
  READ THE ROW LABEL BEFORE DIVIDING - this is where the rule is most often misapplied. Halve
  only a rate quoted for the WHOLE unit ("per suite", "per cabin", "per room", or unqualified).
  A row already labelled PER PERSON is per person already:
      "Single Luxury Cabin - $565"                 -> singlePrice = nights x 565
      "Per person in Double Luxury Cabin - $353"   -> doublePrice = nights x 353, NOT halved
      "Per Junior Suite 333 - $1,059" (per suite)  -> singlePrice = nights x 1059,
                                                      doublePrice = (nights x 1059) / 2
  Halving a figure that was already per person under-charges by half, on every booking, and
  nothing downstream can detect it. When the label is ambiguous, say which reading you used in
  pricing_notes rather than choosing in silence.
  DO NOT put the per-night rate itself into the price fields. That is the mistake this rule exists
  to stop: it under-prices the tour by the whole length of the trip, and the payload validates
  perfectly while doing it. Multiply first, every time.
  Use the SAME nights figure you put in the "nights" field. If the document gives per-night rates
  but no clear night count, say so in pricing_notes rather than guessing a total.
  Do NOT invent triplePrice or quadruplePrice by dividing by 3 or 4 - only fill those if the
  document actually prices triple or quadruple occupancy.
  ============================================================
  OCCUPANCIES MUST AGREE BETWEEN PRICES AND SUPPLEMENTS - CONFIRMED HOUSE RULE (product owner):
  "If no price in Triple or in Quadruple, there can't be any price for triple or quadruple in the
  supplement neither - it goes hand in hand." An occupancy that is not sold cannot carry a
  supplement, so if triplePrice is absent here, every supplement's triple amount must be absent
  too, and the same for quadruple. This is checked automatically before publishing, but extract
  it correctly in the first place rather than relying on that.
  ============================================================
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
  "cancellation_policy_tiers": [],
  "cancellation_policy_text": "",
  "itinerary_destinations": [],
  "nights": 0,
  "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "schedule_notes": "",
  "pricing_notes": "",
  "price_list": [],
  "release_days_mentions": []
}"""


def _required_keys_schema(defaults: dict) -> dict:
    """Builds a tool-call input_schema that REQUIRES every key in `defaults` to be present,
    typed by the shape of its own default value (bool->boolean, int/float->number, list->array,
    dict->object, None->no type constraint, else->string). Values can still be ANY value of that
    type, including the empty/zero one - only the KEY'S PRESENCE is enforced - so this can never
    force the model to invent content, only stop it from silently DROPPING a whole field once
    nothing but the prompt's prose was making it include one. Building the schema FROM the same
    `defaults` dict each extraction function already uses for its post-call fallback-filling
    means the two can never drift apart - see EXTRACTION_TOOL_SCHEMA's docstring below for the
    confirmed real bug this whole mechanism exists to close."""
    def _type_for(value):
        if value is None:
            return {}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, (int, float)):
            return {"type": "number"}
        if isinstance(value, list):
            return {"type": "array"}
        if isinstance(value, dict):
            return {"type": "object", "additionalProperties": True}
        return {"type": "string"}
    return {
        "type": "object",
        "properties": {k: _type_for(v) for k, v in defaults.items()},
        "required": list(defaults.keys()),
    }


# CONFIRMED REAL BUG (product owner, 2026-08-14, real document: "4 Days Sapa Cultural Treasures
# & Bac Ha Market"): "the App could still not read any name, description or any other data from
# this document" - a clean, well-formed ClosedTour document (checked directly: name/description/
# itinerary/pricing all present and readable in the extracted text) came back with every field
# empty, three times in a row. Root cause is almost certainly the same pattern confirmed twice
# already this session (apply_clarification dropping "summary"; price_refresh's lookup dropping
# "found"/"brackets") - extract_structured_data's call to _call_claude passed no input_schema,
# so it silently used the fully permissive default (_PERMISSIVE_TOOL_SCHEMA), which gives the
# model no structural signal that any key is actually expected. The defensive `defaults` dict
# right below this call then quietly fills in "" / [] / 0 for every dropped key, so a total
# field-drop produces exactly this symptom: no exception, no error on screen, just an empty
# tour. A real schema that REQUIRES every top-level key (values can still be "" / [] / 0 - only
# the KEY'S PRESENCE is enforced, never a non-empty value, so this can never force the model to
# invent content) closes this off structurally instead of hoping the prompt's prose is enough.
EXTRACTION_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "tour_name": {"type": "string"},
        "description": {"type": "string"},
        "hotels_text": {"type": "string"},
        "hotels_count": {"type": "integer"},
        "supplements": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "included": {"type": "string"},
        "excluded": {"type": "string"},
        "meeting_point": {"type": "string"},
        "policy_remarks": {"type": "string"},
        "cancellation_policy_tiers": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "cancellation_policy_text": {"type": "string"},
        "itinerary_destinations": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "nights": {"type": "integer"},
        "start_time": {"type": "string"},
        "end_time": {"type": "string"},
        "min_child_age": {"type": "integer"},
        "max_child_age": {"type": "integer"},
        "operational_days": {"type": "array", "items": {"type": "string"}},
        "schedule_notes": {"type": "string"},
        "pricing_notes": {"type": "string"},
        "stop_sales": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "price_list": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "release_days_mentions": {"type": "array"},
    },
    "required": [
        "tour_name", "description", "hotels_text", "hotels_count", "supplements", "included",
        "excluded", "meeting_point", "policy_remarks", "cancellation_policy_tiers",
        "cancellation_policy_text", "itinerary_destinations", "nights", "start_time", "end_time",
        "min_child_age", "max_child_age", "operational_days", "schedule_notes", "pricing_notes",
        "stop_sales", "price_list", "release_days_mentions",
    ],
}


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


def _fix_days_count_in_tour_name(tour_name: str, nights) -> str:
    """
    CONFIRMED NAMING RULE: an N-night itinerary is always described as an
    (N+1)-Day tour (e.g. a 4-night tour is a 5 Days tour, a 2-night tour is
    a 3 Days tour) - standard travel-industry convention (day 1 = arrival,
    the last day = departure, so N nights always span N+1 calendar days).
    The AI is instructed to apply this itself (see EXTRACTION_SYSTEM_PROMPT),
    but this is a fully deterministic correction, so double-check/fix it
    here too rather than relying on the AI alone - catches the real-world
    case where the SOURCE document's own title already uses a wrong day
    count (e.g. calling a 4-night itinerary a "4 Days" tour) and the AI
    copies it verbatim instead of correcting it.

    Only touches an existing "<number> Day(s)" token in the name (the first
    one found) - leaves a tour_name with no day-count mention completely
    untouched, and does nothing for a 0-night (single-day, no overnight)
    product, since that's a different product type (Ticket) this
    convention doesn't apply to.
    """
    if not tour_name or not isinstance(nights, (int, float)) or nights <= 0:
        return tour_name
    correct_days = int(nights) + 1
    return re.sub(r"\b\d+\s*(Days?)\b", lambda m: f"{correct_days} {m.group(1)}", tour_name, count=1, flags=re.I)


def _sanitize_cancellation_tiers(tiers) -> list:
    """
    Defensive cleanup of the AI-extracted cancellation_policy_tiers list
    (see EXTRACTION_SYSTEM_PROMPT/TICKET_EXTRACTION_SYSTEM_PROMPT's
    cancellation_policy_tiers rule) - drops any entry that isn't a real
    {"days": int, "fee_percentage": number} dict, guards against the same
    NaN/Infinity/non-numeric risk documented elsewhere in this project
    (blank-cell-shaped values slipping through), clamps fee_percentage into
    a sane 0-100 range, and de-duplicates/sorts descending by days so the
    builder can rely on a clean, consistently-ordered list. Used at
    extraction time so a malformed entry never has the chance to reach the
    review UI or the publish payload looking normal.
    """
    if not isinstance(tiers, list):
        return []
    seen_days = set()
    cleaned = []
    for t in tiers:
        if not isinstance(t, dict):
            continue
        try:
            days = int(t.get("days"))
            fee_pct = float(t.get("fee_percentage"))
        except (TypeError, ValueError):
            continue
        if math.isnan(fee_pct) or math.isinf(fee_pct) or days < 0:
            continue
        fee_pct = max(0.0, min(100.0, fee_pct))
        if days in seen_days:
            continue
        seen_days.add(days)
        cleaned.append({"days": days, "fee_percentage": fee_pct})
    cleaned.sort(key=lambda t: t["days"], reverse=True)
    return cleaned


_WEEKDAY_NAME_TO_INDEX = {
    "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
    "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
}


def compute_non_guaranteed_stop_sales(rule: dict) -> list:
    """
    CONFIRMED REAL CASE (RV River Kwai Cruise contract): some DMC contracts
    describe a "guaranteed departure" pattern - the tour runs weekly on one
    fixed weekday, but only SPECIFIC ordinal occurrences of that weekday
    within each month are guaranteed to operate without a minimum-passenger
    requirement (e.g. "the 1st and 3rd Monday of every month"); every OTHER
    occurrence of that same weekday still nominally exists on the schedule
    but requires the stated minimum passenger count to actually run.

    Travel Compositor's ContractClosedTourOptionVO schema has no native
    "guaranteed departure" concept - only operationalDays (a plain weekday
    set) and stopSales (blocked date ranges). Asking the AI to directly
    enumerate every individual non-guaranteed calendar date itself is an
    unreliable task (off-by-one and month-boundary risk over a full year),
    so instead the AI extracts the STATED RULE as structured data, and this
    function does the exact calendar math in plain deterministic Python:
    operational_days gets narrowed to just the guaranteed weekday, and every
    OTHER occurrence of that weekday in the stated range becomes its own
    single-day stop_sales entry (so it's visibly blocked instead of quietly
    bookable without enough passengers).

    rule: {"weekday": "MONDAY", "ordinals": [1, 3], "range_start": "YYYY-MM-DD",
           "range_end": "YYYY-MM-DD", "min_pax_otherwise": 4}
    (ordinals are 1-based "1st/2nd/3rd/... occurrence of that weekday within
    its calendar month" - the standard everyday meaning of "1st and 3rd Monday
    of every month").

    Returns a list of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} entries
    (single-day ranges), one per NON-guaranteed occurrence. Returns an empty
    list if `rule` is missing/malformed/incomplete rather than raising, so a
    bad or partial AI extraction never crashes the pipeline - it just means
    no automatic stop_sales get added and the human reviews it manually.
    """
    if not isinstance(rule, dict):
        return []
    try:
        weekday_name = str(rule.get("weekday", "")).strip().upper()
        weekday_index = _WEEKDAY_NAME_TO_INDEX[weekday_name]
        ordinals = {int(o) for o in (rule.get("ordinals") or [])}
        range_start = datetime.date.fromisoformat(str(rule.get("range_start", "")).strip())
        range_end = datetime.date.fromisoformat(str(rule.get("range_end", "")).strip())
    except (KeyError, ValueError, TypeError):
        return []
    if not ordinals or range_end < range_start:
        return []

    # Find the first occurrence of the target weekday on/after range_start.
    days_until_weekday = (weekday_index - range_start.weekday()) % 7
    current = range_start + datetime.timedelta(days=days_until_weekday)

    occurrence_count = {}  # (year, month) -> how many of this weekday seen so far
    non_guaranteed = []
    while current <= range_end:
        month_key = (current.year, current.month)
        occurrence_count[month_key] = occurrence_count.get(month_key, 0) + 1
        ordinal = occurrence_count[month_key]
        if ordinal not in ordinals:
            date_str = current.isoformat()
            non_guaranteed.append({"start": date_str, "end": date_str})
        current += datetime.timedelta(days=7)

    return non_guaranteed


def _ordinal_label(n) -> str:
    n = int(n)
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _apply_guaranteed_departure_rule(data: dict) -> None:
    """
    Applies data["guaranteed_departure_rule"] (if the AI extracted one - see
    MODALITY_EXTRACTION_SYSTEM_PROMPT / OPTION_ONLY_SYSTEM_PROMPT) to the rest
    of `data` in place: narrows operational_days to just the guaranteed
    weekday, and merges the computed non-guaranteed occurrences into
    stop_sales (skipping any day already covered by an AI-extracted range,
    e.g. a dry-dock closure, so the two don't produce redundant/overlapping
    entries). Also appends a plain-English note to schedule_notes so the
    human reviewing the Modality can see exactly what was inferred and why,
    before publishing. No-op if guaranteed_departure_rule is missing/null or
    the rule doesn't compute to anything (malformed/incomplete rule).
    """
    rule = data.get("guaranteed_departure_rule")
    if not isinstance(rule, dict):
        return

    computed = compute_non_guaranteed_stop_sales(rule)
    if not computed:
        return

    existing = [r for r in (data.get("stop_sales") or []) if isinstance(r, dict)]

    def _already_covered(day_str: str) -> bool:
        try:
            day = datetime.date.fromisoformat(day_str)
        except ValueError:
            return False
        for r in existing:
            try:
                start = datetime.date.fromisoformat(str(r.get("start", "")))
                end = datetime.date.fromisoformat(str(r.get("end", "")))
            except ValueError:
                continue
            if start <= day <= end:
                return True
        return False

    new_entries = [c for c in computed if not _already_covered(c["start"])]
    data["stop_sales"] = existing + new_entries

    weekday_name = str(rule.get("weekday", "")).strip().upper()
    if weekday_name in _WEEKDAY_NAME_TO_INDEX:
        data["operational_days"] = [weekday_name]

    ordinals = sorted({int(o) for o in (rule.get("ordinals") or [])})
    ordinals_label = " and ".join(_ordinal_label(o) for o in ordinals) if ordinals else "guaranteed"
    note = (
        f"Guaranteed departure rule detected: only the {ordinals_label} {weekday_name.title()} of each month "
        f"(between {rule.get('range_start', '?')} and {rule.get('range_end', '?')}) is guaranteed to operate "
        f"without a minimum passenger count. The other {len(new_entries)} {weekday_name.title()} date(s) in that "
        f"range require {rule.get('min_pax_otherwise', '?')} passengers minimum, and have been added to Stop "
        f"Sales below - please review before publishing."
    )
    existing_notes = str(data.get("schedule_notes") or "").strip()
    data["schedule_notes"] = f"{existing_notes} {note}".strip() if existing_notes else note


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
    if ("as far as the tool will go" in lower or "no paragraph breaks" in lower
            or "one change at a time" in lower):
        # Already written for a human, and more specific than the generic truncation advice
        # below - replacing it with "try again" would throw away the useful part.
        # Detection already retried in sections and still couldn't fit - saying "try again"
        # here would be false advice, because the automatic retry has already been spent.
        return text
    if "cut off" in lower or "token limit" in lower or "max_tokens" in lower:
        return ("The AI's answer was too long and got cut off before it finished (this document/tour "
                "produced more text than the AI is allowed to send back in one go). Detection retries "
                "automatically by reading the document in sections, so this usually means the EXTRACTION "
                "step (not detection) overflowed on one very large product. Try again - if it keeps "
                "happening on this same document, splitting it into smaller sections fixes it.")
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


def _call_claude(system_prompt: str, user_content: str, model: str, max_tokens: int = 4096,
                 input_schema: dict = None) -> dict:
    """
    Shared helper: calls Claude via a forced tool call (see
    _stream_claude_tool_call - guarantees well-formed JSON back, no manual
    parsing/fallback-recovery needed) and returns the resulting dict. The
    system prompt is sent as a cacheable block - Anthropic's prompt caching
    means repeat calls that reuse the SAME system prompt (e.g. one call per
    item in a multi-tour/multi-ticket batch) are billed at a fraction of
    the normal input price for that cached portion, instead of paying full
    price for the same multi-thousand-token prompt every single time.

    `input_schema` is optional and defaults to the fully permissive schema
    (see _stream_claude_tool_call) - pass a real schema with "required"
    fields for a caller whose output has a small, fixed shape where a
    dropped field would silently break everything downstream (e.g. a
    per-item "found"/"brackets" pair that a missing-field default quietly
    turns into "not found" for every single item at once).
    """
    client = _get_anthropic_client()
    data, stop_reason = _stream_claude_tool_call(client, model, max_tokens, system_prompt, user_content,
                                                 input_schema=input_schema)
    if stop_reason == "max_tokens":
        raise RuntimeError(
            "Claude's response was cut off (hit the token limit) before finishing - try a shorter "
            "document/URL, or this may need a higher max_tokens setting."
        )
    return data


# ----------------------------------------------------------------------
# Detection with automatic chunking
#
# CONFIRMED REAL FAILURE (product owner, on a live transfer rate sheet): "Detection failed:
# The AI's answer was too long and got cut off before it finished." Detection listed every
# distinct route x class in a document, capped at 1024-2048 output tokens, and a real
# supplier tariff covering dozens of routes simply produces more candidates than that fits.
# The document was fine; the ceiling was too low. The app's advice was to split the document
# by hand, which is work a person should never have to do on the tool's behalf.
#
# Two fixes, in order:
#   1. A far higher ceiling, because a detection candidate is only a few dozen tokens and
#      the old limits were arbitrary rather than considered.
#   2. If it STILL truncates, split the document and detect per section automatically, then
#      merge and de-duplicate. Each section carries the document's opening lines with it,
#      because currency, class names and validity dates usually sit in a header that a naive
#      split would strip from every section but the first.
_DETECTION_MAX_TOKENS = 8192
_DETECTION_MAX_DEPTH = 5          # full doc -> halves -> ... -> thirty-seconds
_DETECTION_HEADER_CHARS = 700


def _call_claude_with_stop(system_prompt: str, user_content: str, model: str,
                           max_tokens: int, input_schema: dict = None) -> tuple:
    """Like _call_claude, but hands the stop_reason back instead of raising on truncation,
    so a caller can react to it (chunk and retry) rather than surfacing a dead end."""
    client = _get_anthropic_client()
    return _stream_claude_tool_call(client, model, max_tokens, system_prompt, user_content,
                                    input_schema=input_schema)


_CONTINUATION_MARKER = "[Continued from earlier in the same document."


def _document_header(text: str) -> str:
    """The document's opening block only - its title, currency line, validity dates.

    CONFIRMED REAL BUG (caught by test, 2026-08-09): this used to be "the first 700
    characters", which on a dense rate sheet is not a header at all but eighteen actual
    route rows. Every section then carried those rows along, so each split ADDED content
    instead of removing it and the recursion never converged - a document that was merely
    large came back as "still cut off after splitting as far as the tool will go". Taking
    the first paragraph and nothing else keeps the context without dragging the body with
    it."""
    if text.startswith(_CONTINUATION_MARKER):
        # Already a section: its header block sits after the marker line.
        text = text.split("[Section:]", 1)[0]
        text = text.split("\n", 1)[-1]
    first = text.split("\n\n", 1)[0]
    return first[:_DETECTION_HEADER_CHARS]


def _split_for_detection(text: str, parts: int = 2, header: str = "") -> list:
    """Split on paragraph boundaries into roughly equal sections, each after the first
    prefixed with the document's header so per-section context (currency, class names,
    validity dates) survives a split."""
    # Split the BODY, not the body plus the context prefix. CONFIRMED REAL BUG (caught by
    # test): the prefix sits at the start, so measuring the halfway point across it made the
    # first section absorb the prefix plus only a sliver of content and the second keep the
    # rest. Each level then shed a little instead of halving, and a document that needed
    # four splits ran out of depth and reported itself as un-splittable.
    body = text
    if text.startswith(_CONTINUATION_MARKER) and "[Section:]\n" in text:
        body = text.split("[Section:]\n", 1)[1]
    blocks = body.split("\n\n")
    if len(blocks) < parts:
        blocks = body.split("\n")
    if len(blocks) < parts:
        return [text]
    target = max(1, len(body) // parts)
    chunks, current, size = [], [], 0
    for block in blocks:
        current.append(block)
        size += len(block) + 2
        if size >= target and len(chunks) < parts - 1:
            chunks.append("\n\n".join(current))
            current, size = [], 0
    if current:
        chunks.append("\n\n".join(current))
    chunks = [c for c in chunks if c.strip()]
    if len(chunks) < 2:
        return chunks or [text]
    out = []
    for chunk in chunks:
        prefix = ""
        # No identity check on the first chunk: for a whole document it already CONTAINS
        # the header block, so the "not in chunk" test correctly skips it - while for a
        # section being split again the prefix was stripped above, so its first chunk
        # genuinely needs the header back.
        if header.strip() and header.strip() not in chunk:
            prefix = (f"{_CONTINUATION_MARKER} Opening lines repeated for context:]\n"
                      f"{header}\n\n[Section:]\n")
        out.append(prefix + chunk)
    return out


# What the last detection actually returned, so an empty result can be explained on screen
# rather than leaving an operator guessing whether the document, the prompt or the tool failed.
LAST_DETECTION = {}


def _record_detection(list_key, found, flag, raw_text, depth):
    LAST_DETECTION.update({
        "list_key": list_key,
        "count": LAST_DETECTION.get("count", 0) + len(found),
        "flag": flag,
        "document_chars": max(LAST_DETECTION.get("document_chars", 0), len(raw_text or "")),
        "sections_read": LAST_DETECTION.get("sections_read", 0) + 1,
    })


def _dedupe_detected(items, key_fn):
    """Collapse candidates that are the same product, preserving the order they came in.

    Used on EVERY answer, not only on merged sections. De-duplication used to live solely in
    the chunked path, so a model that listed the same route twice inside one answer - easy on
    a rate sheet that repeats a route per guide language - put it in the review queue twice,
    and a human reviewed and published the same product two times."""
    seen, out = set(), []
    for item in items:
        try:
            identity = key_fn(item)
        except Exception:
            identity = repr(item)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(item)
    return out


def _detect_items(system_prompt: str, raw_text: str, model: str, flag_key: str, list_key: str,
                  key_fn, max_tokens: int = _DETECTION_MAX_TOKENS, _depth: int = 0,
                  _header: str = None) -> list:
    """Run a detection prompt, and if the answer is cut off, split the document and merge.

    key_fn(item) -> a hashable identity used to de-duplicate across sections. Sections
    deliberately overlap in context, and a route table can straddle a split, so the same
    candidate genuinely can come back twice - de-duplicating is not optional."""
    if _depth == 0:
        # Reset HERE, not on the success path: a top-level call that truncates never reaches
        # the success path, so the previous run's counts survived and the diagnosis reported
        # the wrong document. A diagnostic that lies is worse than none.
        LAST_DETECTION.clear()
    try:
        data, stop_reason = _call_claude_with_stop(system_prompt, raw_text, model, max_tokens)
    except Exception:
        if _depth >= _DETECTION_MAX_DEPTH:
            raise
        stop_reason, data = "max_tokens", None      # treat a hard failure like truncation
    if data is not None and stop_reason != "max_tokens":
        # TRUST THE LIST, NOT THE FLAG. CONFIRMED REAL FAILURE (product owner, on a real
        # transfer rate sheet uploaded as Transport): the model returned the routes it had
        # found AND set the boolean to false - reasoning, not unreasonably, that a document
        # headed "Transfer Fees" does not describe "multiple transports" - and this line then
        # threw the whole list away. On screen that is indistinguishable from the AI finding
        # nothing at all, so no instruction the operator typed could ever fix it.
        #
        # The boolean was always redundant: an empty list already means "not multiple". Reading
        # only the list removes a way for the two to disagree.
        found = _dedupe_detected([i for i in (data.get(list_key) or []) if isinstance(i, dict)],
                                 key_fn)
        _record_detection(list_key, found, data.get(flag_key), raw_text, _depth)
        return found

    if _depth >= _DETECTION_MAX_DEPTH:
        raise RuntimeError(
            "Claude's answer was still cut off after splitting this document as far as the tool "
            "will go. This document is unusually large - try uploading it in sections."
        )
    header = _document_header(raw_text) if _header is None else _header
    parts = _split_for_detection(raw_text, parts=2, header=header)
    if len(parts) < 2:
        raise RuntimeError(
            "Claude's answer was cut off and this document has no paragraph breaks to split on - "
            "try uploading it in smaller sections."
        )
    print(f"↔️ Answer was cut off - re-reading this document in {len(parts)} sections and merging.")
    merged = []
    for part in parts:
        merged.extend(_detect_items(system_prompt, part, model, flag_key, list_key, key_fn,
                                    max_tokens=max_tokens, _depth=_depth + 1, _header=header))
    return _dedupe_detected(merged, key_fn)


def _with_hint(raw_text: str, human_hint: str = None) -> str:
    """Put the operator's instruction in front of the document, where detection will see it.

    CONFIRMED REAL GAP (product owner): the "Extraction hint" box was only ever passed to the
    EXTRACTION step, never to DETECTION - so typing "only the Hurghada routes" had no effect
    on which products were found, only on how each one was subsequently read. Since detection
    is what decides the list a human then picks from, the instruction was arriving one step
    too late to do the thing it looks like it does.

    It goes ABOVE the document, and the prompts are told it outrides their own heuristics: the
    operator can see the document and knows how these products are actually sold."""
    hint = (human_hint or "").strip()
    if not hint:
        return raw_text
    return (f"INSTRUCTION FROM THE OPERATOR (follow this over your own judgement):\n{hint}\n\n"
            f"--- DOCUMENT ---\n{raw_text}")


def _directional_route_identity(item: dict) -> tuple:
    """Identity for TRANSPORT, where direction is part of the product.

    A Travel Compositor transport record is departure -> arrival, so Marsa Alam -> Hurghada
    and Hurghada -> Marsa Alam are two separate records that must both exist to sell the
    route both ways. _route_identity sorts the pair and would collapse them into one, quietly
    deleting every return leg."""
    def norm(v):
        return " ".join(str(v or "").split()).lower()
    return (norm(item.get("service_name")), norm(item.get("departure_hint")),
            norm(item.get("arrival_hint")))


def _route_identity(item: dict) -> tuple:
    """Two routes are the same product if they are the same class between the same two
    places - in EITHER direction. The detection prompts already say A->B and B->A are one
    product; sorting the pair is what makes that hold when the two directions were found in
    different sections of the document and never seen side by side."""
    def norm(v):
        return " ".join(str(v or "").split()).lower()
    ends = tuple(sorted((norm(item.get("departure_hint")), norm(item.get("arrival_hint")))))
    return (norm(item.get("service_name")), ends)


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
    modalities = _detect_items(
        MODALITY_DETECTION_PROMPT, raw_text, model, "multiple_modalities", "modalities",
        lambda m: " ".join(str(m.get("suggested_code") or m.get("label") or "").split()).lower())
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
    variants = _detect_items(
        VARIANT_DETECTION_PROMPT, raw_text, model, "multiple_variants", "variants",
        lambda v: " ".join(str(v.get("label") or "").split()).lower())
    if variants:
        print(f"⚠️ Detected {len(variants)} distinct tour variants: {[v.get('label') for v in variants]}")
    else:
        print("✅ Only one tour detected.")
    return variants


# CONFIRMED PRODUCT-OWNER DECISION: "we can also give more tokens, just if the AI misses out on
# reading crucial information." These budgets exist only to stay inside the model's context
# window, not to save money, so they are set as high as that window realistically allows. A
# rate sheet the model was never shown is the single most expensive kind of mistake here - it
# answers anyway, confidently, and the wrong answer looks exactly like a right one.
#
# Roughly 4 characters per token: 400k chars of document is ~100k tokens, 200k chars of
# extracted data ~50k tokens, which together still leave comfortable room for the reply inside
# a 200k window. Anything beyond this is genuinely too big for one call, and the notes appended
# below say so out loud rather than letting the model guess at what it cannot see.
_CLARIFY_DOC_CHARS = 400000
_CLARIFY_DATA_CHARS = 200000

# The reply itself. A corrected price_list covering eight seasons with per-occupancy rates is a
# large object, and a tool call cut off mid-way is refused outright (see the max_tokens check
# below) - so the ceiling is set well above what any single correction should need.
_CLARIFY_MAX_OUTPUT_TOKENS = 32768


# Past-tense claims of work done. Used only to decide whether a summary that came back with an
# EMPTY changes object deserves a second look - a false positive costs one cheap retry, and the
# retry explicitly allows "nothing needed changing" as an answer, so it can never force an edit.
_CLAIMED_CHANGE_RE = re.compile(
    r"(?i)\b(updated|changed|corrected|fixed|added|removed|replaced|adjusted|rewrote|"
    r"recalculated|re-?verified|now includes?|now contains?|are now|is now|have been|has been)\b")
# ... unless the sentence is explicitly saying it did NOT change anything.
_NO_CHANGE_RE = re.compile(
    r"(?i)(no changes?\s+(were\s+)?(needed|made|required)|nothing\s+(was\s+)?changed|"
    r"did\s+not\s+change|didn'?t\s+change|left\s+(it\s+)?unchanged|no\s+edits?\s+(were\s+)?)")


def _summary_claims_a_change(summary: str) -> bool:
    """True when the wording reports work done, and does not also say nothing was changed."""
    text = (summary or "").strip()
    if not text or not _CLAIMED_CHANGE_RE.search(text):
        return False
    return not _NO_CHANGE_RE.search(text)


def _json_within_budget(data: dict, budget: int) -> Tuple[str, List[str]]:
    """Serialise `data` to JSON that FITS, by dropping whole fields rather than cutting text.

    CONFIRMED REAL BUG (product owner: "Tell AI what to fix - the results are actually very
    bad"): this used to be json.dumps(data)[:20000]. A ClosedTour with a full itinerary and a
    price list goes well past that, so the model was handed a JSON object sliced off mid-string
    - not a large object, an INVALID one. It could not reliably tell what the current values
    even were, which is exactly the input it needs to change one of them.

    Dropping whole fields keeps the object parseable, and the caller is told which fields went
    so it can say so in the prompt. Biggest fields go first, because one enormous itinerary is
    usually what blows the budget while thirty small fields are what the instruction is about."""
    try:
        full = json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(data)[:budget], []
    if len(full) <= budget:
        return full, []

    kept = dict(data)
    dropped = []
    by_size = sorted(
        kept.keys(),
        key=lambda k: len(json.dumps(kept[k], ensure_ascii=False, default=str)),
        reverse=True)
    for key in by_size:
        if len(json.dumps(kept, indent=2, ensure_ascii=False, default=str)) <= budget:
            break
        if len(kept) <= 1:
            break
        dropped.append(key)
        kept.pop(key, None)
    return json.dumps(kept, indent=2, ensure_ascii=False, default=str), dropped


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
        "You are helping a human review and refine data extracted from a travel document.\n\n"
        "TREAT EVERY MESSAGE AS AN INSTRUCTION TO CHANGE SOMETHING unless it is unmistakably a "
        "question (it ends in a question mark, or opens with what/why/where/how/is/does/can). "
        "CONFIRMED, FROM THE PERSON WHO USES THIS BOX: \"I never ask anything in this tool, I only "
        "order what AI did misread.\" So a flat statement like 'the triple price is 449' or 'the "
        "season runs to 30 November' is an ORDER TO CORRECT THE DATA, not an observation to agree "
        "with, and 'no changes needed' is the wrong answer to it.\n\n"
        "IF YOU BELIEVE THE DATA IS ALREADY RIGHT, LOOK AGAIN BEFORE SAYING SO. The person is "
        "reading the real document and you are reading an extract of it; when they contradict what "
        "you see, they are usually right. Only answer 'nothing needed changing' when you have "
        "checked the specific field they named and it genuinely already holds the value they want - "
        "and then say which field you checked and what it holds, so they can see where you looked.\n\n"
        "Use ONLY the source document and current "
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
        "the same array-of-objects shape, operational_days is the same list of weekday names).\n\n"
        "CONFIRMED REAL BUG THIS AVOIDS - 'generated_modalities' IS NEVER SHOWN AND MUST NEVER BE EDITED: "
        "a Ticket's list of Modalities-to-be-created is COMPUTED FRESH from base_adult_price/"
        "base_children_price/base_infant_price plus extra_cost_options every time the screen renders - it "
        "is a derived preview, not a stored field. A change written to it directly is silently thrown away "
        "on the very next render, so the human sees a success message and the table looking completely "
        "unchanged - a real reported failure. When the human asks to remove/add/change a Modality variant "
        "(e.g. 'delete the French-speaking guide option', 'only one Modality needs to be created'), the "
        "fix ALWAYS belongs in extra_cost_options instead: remove/add/edit the matching entry there (match "
        "by its 'name', e.g. an entry named 'French-speaking guide') - removing it from extra_cost_options "
        "is what makes that Modality stop being generated. If the human names the BASE modality itself "
        "(no extra), the fix is base_adult_price/base_children_price/base_infant_price. Never put "
        "'generated_modalities' in the changes object under any circumstances.\n\n"
        "TICKET MODALITY PRICING - 'price_type' / 'occupancy_prices' / 'base_service_price' (Tickets "
        "only - a ClosedTour/Hotel/Transfer/Transport modality has no 'price_type' field at all, so "
        "none of this applies to those): a Ticket Modality's OWN price (as opposed to an extra_cost_options "
        "surcharge - see above) is priced ONE of two ways, and the current extracted data always tells "
        "you which via its 'price_type' field - check it, never guess:\n"
        "  - price_type 'OCCUPANCY': 'occupancy_prices' is a list of {\"occupancy\": <exact integer pax "
        "count>, \"amount\": <price for exactly that many people>} and MUST cover EVERY headcount from 1 "
        "up through this Modality's own Max Passengers (never a partial list - the screen shows exactly "
        "that many rows, not a variable-length table). If the human corrects the price for ONE specific "
        "headcount (e.g. 'the 4-pax price should be 120', 'fix the price for 6 people'), return the FULL "
        "occupancy_prices array as it should read afterward - every row from the current data, with only "
        "that one row's amount changed - never a 1-row array containing just the corrected entry, since "
        "that would delete every other row's price. If the human says the price is the SAME regardless of "
        "group size (e.g. 'it's actually 45 per person no matter how many', 'flat rate of 45'), return "
        "occupancy_prices with EVERY row set to that same amount - do NOT touch base_adult_price/"
        "base_children_price/base_infant_price for this, those three fields are not used by the Ticket "
        "pricing screen at all anymore.\n"
        "  - CHILD PRICE COLUMN (2026-08-13, applies only when this Ticket allows children - "
        "'disallow_children' is false): each occupancy_prices row may ALSO carry a \"child_amount\" - the "
        "per-row price for a child in that same age band (child_age_min/child_age_max, default 2-12), "
        "shown next to the adult 'amount' as its own column. Default is child_amount EQUAL to that row's "
        "amount (no discount) unless the human or the source document states a distinct child rate/"
        "discount (e.g. 'children are half price', 'child rate 50% off', 'kids pay 20 instead of 40') - "
        "then compute child_amount as that ratio applied to each row's own amount, not a single flat "
        "number copied to every row. If the human corrects only the child price (e.g. 'the child price "
        "should be 15 for 2 people', 'children are actually free', 'no child discount, same as adult'), "
        "return the FULL occupancy_prices array with every row unchanged except child_amount - the same "
        "never-a-partial-array rule as the adult amount above. If the human's correction is about the "
        "ADULT price only, still include the existing child_amount on every row unchanged, don't drop it.\n"
        "  - price_type 'SERVICE': the ticket has one flat total price regardless of headcount, held in "
        "base_service_price - a price correction here is simply base_service_price with its new value, "
        "never occupancy_prices.\n"
        "  - Never write price_type itself unless the human explicitly asks to switch pricing mode (e.g. "
        "'change this to a flat Service price instead of per-person Occupancy pricing') - changing it "
        "silently would rebuild the whole pricing table from a shape the human never asked to change."
    )
    # The source document text is put in its OWN cacheable content block,
    # separate from the (frequently-changing) current-data/instruction block.
    # It stays IDENTICAL across every "Tell AI what to fix" call on the same
    # item during a review session, so Anthropic's prompt caching means only
    # the first call in that session pays full price for it - every
    # follow-up question/fix on the same item reuses the cached copy instead
    # of resending it at full cost.
    # CONFIRMED REAL COMPLAINT (product owner): "the results are actually very bad." The two
    # causes were both here, in what the model was being GIVEN rather than in how it was asked.
    #
    # The document was cut to 15,000 characters. A ClosedTour itinerary runs far past that, so
    # an instruction about day 6 was routinely answered by a model that had never been shown
    # day 6 - and it has no way to know that, so it answers anyway.
    #
    # The extracted data was cut to 20,000 characters MID-JSON, handing over an object that
    # does not parse. See _json_within_budget.
    doc = raw_text or ""
    doc_note = ""
    if len(doc) > _CLARIFY_DOC_CHARS:
        doc = doc[:_CLARIFY_DOC_CHARS]
        doc_note = ("\n\n[This document was too long to include in full and has been cut here. "
                    "If the answer depends on a part you cannot see, SAY SO in your summary "
                    "rather than guessing.]")
    # generated_modalities is a DERIVED preview (recomputed from base_adult_price/
    # base_children_price/base_infant_price + extra_cost_options on every render, see
    # render_ticket_extra_costs in app.py) - never a real stored field. Showing it invites the
    # model to "edit" it directly, which is a no-op that gets silently overwritten on the next
    # render - a real reported failure ("the AI says it did something, but still all Modalities
    # are seen"). Excluded here so the model is never tempted; the system prompt above redirects
    # any such request to extra_cost_options instead.
    _clarify_source_data = {k: v for k, v in (current_data or {}).items() if k != "generated_modalities"}
    data_json, dropped = _json_within_budget(_clarify_source_data, _CLARIFY_DATA_CHARS)
    data_note = ""
    if dropped:
        data_note = ("\n\n[These fields were too large to include and are NOT shown above: "
                     + ", ".join(dropped) +
                     ". Do not change them, and say so if the request concerns one of them.]")

    user_content = [
        {"type": "text", "text": f"--- Source document text ---\n{doc}{doc_note}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": (
            f"--- Currently extracted data ---\n{data_json}{data_note}\n\n"
            f"--- Human's message ---\n{instruction}"
        )},
    ]
    try:
        client = _get_anthropic_client()
        # Tool-call forcing (see _stream_claude_tool_call) rather than free-text
        # JSON - same reasoning as _call_claude: a "changes" payload can contain
        # arbitrary long text (e.g. a corrected description) that's exactly the
        # kind of content prone to breaking free-text JSON parsing.
        result, stop_reason = _stream_claude_tool_call(
            client, model, _CLARIFY_MAX_OUTPUT_TOKENS, system_prompt, user_content,
            tool_name="apply_changes", input_schema=CLARIFY_TOOL_SCHEMA)
        if stop_reason == "max_tokens":
            # A truncated tool call still returns a partial object, which would be merged
            # into the product as if it were complete. Refusing is the only safe answer.
            raise RuntimeError(
                "The answer was cut off before it finished, so it can't be applied safely. "
                "Ask for one change at a time - e.g. just the day-6 description, then the "
                "price - rather than several at once.")

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
            result, _ = _stream_claude_tool_call(client, model, _CLARIFY_MAX_OUTPUT_TOKENS,
                                                 system_prompt, corrective_user_content,
                                                 tool_name="apply_changes",
                                                 input_schema=CLARIFY_TOOL_SCHEMA)

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

        def _strip_generated_modalities(result):
            """THIRD SAFETY NET: "generated_modalities" is derived (see the exclusion above) and
            gets silently recomputed away on the next render however it arrives here, so a model
            that writes it anyway (ignoring the system prompt) must not be allowed to report
            success on it - drop it and steer the summary, rather than let the caller show a
            green checkmark for an edit that is about to vanish.

            CONFIRMED REAL BUG THIS FIXES: this used to run only once, before the retry below -
            if stripping generated_modalities was what EMPTIED changes in the first place, the
            retry fires and its result is assigned straight into result["changes"] with no
            re-check, so a generated_modalities key the retry reintroduces slipped through
            unfiltered. Called after every place changes is set, including the retry."""
            if "generated_modalities" in result["changes"]:
                del result["changes"]["generated_modalities"]
                if "extra_cost_options" in result["changes"]:
                    note = ("(Note: the Modality list shown is computed automatically from the base "
                            "price and Extra Costs - I edited Extra Costs to make this change take "
                            "effect, rather than the list itself.)")
                else:
                    note = ("⚠️ This was NOT applied: the Modality list shown is computed automatically "
                            "from the base price and Extra Costs, and cannot be edited directly. Please "
                            "retry, naming the specific Extra Cost option to add/remove/change (e.g. "
                            "\"delete the French-speaking guide option\").")
                result["summary"] = ((result.get("summary") or "").strip() + "\n\n" + note).strip()

        _strip_generated_modalities(result)

        # SECOND SAFETY NET, and the more damaging of the two.
        #
        # CONFIRMED REAL INCIDENT (product owner, ClosedTour "Luxury Cabin"): the model returned
        # a detailed past-tense report - "Updated the price list to include all seasonality
        # periods... all 8 seasonal periods are now included with correct start/end dates" - and
        # an EMPTY changes object. The price list was still empty afterwards. Nothing on screen
        # contradicted that paragraph, so the only way to catch it was to disbelieve what you
        # had just been told and re-read the table.
        #
        # A missing summary is obvious. A summary that CLAIMS work it did not return is not, and
        # it is worse, because it is confidently wrong and the human moves on. So the claim is
        # checked against what actually came back and the model gets one chance to make good.
        if not result["changes"] and _summary_claims_a_change(result.get("summary", "")):
            insistent = user_content + [{
                "type": "text",
                "text": ("Your previous tool call described changes you had made, but returned an EMPTY "
                         "'changes' object - so nothing was applied and the human's data is unchanged. "
                         "Call apply_changes again. If you did determine concrete edits, put every one "
                         "of them in 'changes' now, each with its full new value, matching the current "
                         "data's own shape. If on reflection nothing needs to change - the data is "
                         "already correct, or this was only a question - call it with an empty 'changes' "
                         "and rewrite the summary so it plainly says that NOTHING was changed, and why. "
                         "Never describe work you are not returning."),
            }]
            retry, _ = _stream_claude_tool_call(client, model, _CLARIFY_MAX_OUTPUT_TOKENS,
                                                system_prompt, insistent,
                                                tool_name="apply_changes",
                                                input_schema=CLARIFY_TOOL_SCHEMA)
            retry_changes = retry.get("changes") if isinstance(retry, dict) else None
            if isinstance(retry_changes, dict) and retry_changes:
                result["changes"] = retry_changes
                result["summary"] = ((retry.get("summary") or "").strip()
                                     or result.get("summary", ""))
                result["recovered_after_empty_claim"] = True
                _strip_generated_modalities(result)
            else:
                # Still nothing to apply. Keep the CORRECTED wording where there is one, so the
                # human reads "nothing was changed" rather than the original false report.
                corrected = (retry.get("summary") or "").strip() if isinstance(retry, dict) else ""
                if corrected:
                    result["summary"] = corrected
                result["claimed_but_changed_nothing"] = True
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

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
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
- stop_sales: array of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} - dates when this Modality genuinely CANNOT be booked/does not operate, even though they'd otherwise fall inside the normal schedule. This is COMMON in real contracts but easy to under-recognize because DMC documents rarely use the literal words "stop sale" - watch for ANY of these real-world phrasings instead: "not available on/between", "not operating", "no departures", "closed for maintenance/dry-dock/renovation", "excluded dates", "blackout dates", "suspended between", "unavailable", "closed on [a named holiday]", a sold-out period, or a table of "operating dates" that has GAPS between the listed ranges. CRITICAL: this can be MULTIPLE separate, non-contiguous date ranges (e.g. two different maintenance closures plus a holiday closure) - include EVERY one you find as its own entry in the array, don't stop after the first match. Do NOT invent one if the source is simply silent about closures - only include a range the source actually states or clearly implies (e.g. an explicit gap in an otherwise fully-dated operating calendar). Empty list if genuinely none.
- guaranteed_departure_rule: CONFIRMED REAL PATTERN - some contracts state that a weekly departure normally
  needs a minimum passenger count to run, EXCEPT specific ordinal occurrences of that weekday each month
  which are "guaranteed" to operate regardless of passenger count (e.g. "need a minimum of 4 clients to
  guarantee operation, except for departures which can be operated without minimum of passengers on the 1st
  and 3rd Monday of every month during November 2026 - October 2027"). If the source states a rule like
  this, extract it as: {"weekday": "MONDAY", "ordinals": [1, 3], "range_start": "YYYY-MM-DD",
  "range_end": "YYYY-MM-DD", "min_pax_otherwise": 4} - weekday is the single uppercase weekday name that
  departs weekly, ordinals is the list of 1-based occurrence-within-month numbers that are guaranteed (1st
  = 1, 2nd = 2, 3rd = 3, etc.), range_start/range_end is the date range the stated rule covers (use the
  full stated validity window - if unstated, use a wide default), min_pax_otherwise is the minimum
  passenger count required for the non-guaranteed occurrences. Set this to null if the source does not
  describe this specific pattern (a plain weekly schedule with no guaranteed-vs-not distinction is NOT
  this - leave null in that ordinary case). Never invent a rule that isn't actually stated.

Never invent numbers or dates not actually present in the source. If pricing is vague or absent, return an empty price_list rather than guessing.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "price_list": [],
  "pricing_notes": "",
  "schedule_notes": "",
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "stop_sales": [],
  "guaranteed_departure_rule": null
}"""


MODALITY_EXTRACTION_SYSTEM_PROMPT = """You are extracting PRICING/SCHEDULE data for ONE SPECIFIC Modality

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
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
- stop_sales: array of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} - dates when THIS Modality genuinely CANNOT be booked/does not operate, even though they'd otherwise fall inside its normal schedule. This is COMMON in real contracts but easy to under-recognize because DMC documents rarely use the literal words "stop sale" - watch for ANY of these real-world phrasings instead: "not available on/between", "not operating", "no departures", "closed for maintenance/dry-dock/renovation", "excluded dates", "blackout dates", "suspended between", "unavailable", "closed on [a named holiday]", a sold-out period, or a table of "operating dates" that has GAPS between the listed ranges. CRITICAL: this can be MULTIPLE separate, non-contiguous date ranges (e.g. two different maintenance closures plus a holiday closure) - include EVERY one you find as its own entry in the array, don't stop after the first match. Do NOT invent one if the source is simply silent about closures - only include a range the source actually states or clearly implies. Empty list if genuinely none.
- guaranteed_departure_rule: CONFIRMED REAL PATTERN (e.g. a river cruise contract: "need a minimum of 4
  clients to guarantee operation of any cruises, except for Upstream departures which can be operated
  without minimum of passengers on the 1st and 3rd Monday of every month during November 2026 - October
  2027") - some contracts state that a weekly departure normally needs a minimum passenger count to run,
  EXCEPT specific ordinal occurrences of that weekday each month which are "guaranteed" to operate
  regardless of passenger count. If THIS Modality's source describes a rule like this, extract it as:
  {"weekday": "MONDAY", "ordinals": [1, 3], "range_start": "YYYY-MM-DD", "range_end": "YYYY-MM-DD",
  "min_pax_otherwise": 4} - weekday is the single uppercase weekday name that departs weekly, ordinals is
  the list of 1-based occurrence-within-month numbers that are guaranteed (1st = 1, 2nd = 2, 3rd = 3,
  etc.), range_start/range_end is the date range the stated rule covers (use the full stated validity
  window - if unstated, use a wide default), min_pax_otherwise is the minimum passenger count required for
  the non-guaranteed occurrences. Set this to null if the source does not describe this specific pattern (a
  plain weekly schedule with no guaranteed-vs-not distinction is NOT this - leave null in that ordinary
  case, and null if this rule clearly belongs to a DIFFERENT Modality than the one you're extracting).
  Never invent a rule that isn't actually stated.

Never invent numbers or dates not actually present in the source. If pricing is vague or absent, return an empty price_list rather than guessing.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "price_list": [], "supplements": [], "pricing_notes": "", "schedule_notes": "",
  "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
  "stop_sales": [], "guaranteed_departure_rule": null
}"""


_MODALITY_MAX_OUTPUT_TOKENS = 32768


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
            f"WHICH MODALITY TO EXTRACT - read this first, and re-read it before you answer.\n"
            f"{human_hint}\n\n"
            f"Work ONLY on that Modality. The document almost certainly prices several, and taking a "
            f"number from the wrong one produces a tour that looks complete and is priced wrongly - "
            f"the single most expensive mistake available here.\n"
            f"Before answering, satisfy yourself of three things and say so in pricing_notes if any "
            f"of them is in doubt:\n"
            f"  1. WHICH ROW OR COLUMN of the rate table is this Modality's. Name it.\n"
            f"  2. That you have EVERY season/date range for it, including ones listed further down "
            f"     under the same heading - a missed period is the commonest failure on rate sheets.\n"
            f"  3. That the figures are totals for the whole stay, not per-night rates left "
            f"     unmultiplied.\n\n"
            f"--- Source content ---\n{raw_text}"
        )

    # CONFIRMED PRODUCT-OWNER COMPLAINT: "The AI must spend more time for the modality, as it is
    # too badly made yet and many informations are not read." This call had the SMALLEST output
    # budget in the whole app - 4,096 tokens - while doing the most detailed work in it: every
    # season row, with four occupancy prices each, plus supplements, stop sales and schedule. A
    # cruise with eight seasons overruns that, and an overrun is not a partial answer, it is a
    # refused one (see _call_claude). Raised to match the other extraction calls.
    data = _call_claude(system_prompt, user_content, model, max_tokens=_MODALITY_MAX_OUTPUT_TOKENS)

    defaults = {
        "price_list": [], "supplements": [], "pricing_notes": "", "schedule_notes": "",
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "stop_sales": [], "guaranteed_departure_rule": None,
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

    _apply_guaranteed_departure_rule(data)

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
        "stop_sales": [], "guaranteed_departure_rule": None,
        # Defensive: fields builder.py's main_tour_payload construction still
        # reads, even though it's unused/not sent for option-only actions.
        "tour_name": "", "description": "", "hotels_text": "", "hotels_count": 1,
        "supplements": [], "included": "", "excluded": "", "meeting_point": "",
        "policy_remarks": "", "itinerary_destinations": [], "nights": 1,
    }
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    _apply_guaranteed_departure_rule(data)

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
    data = _call_claude(EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=32768,
                       input_schema=EXTRACTION_TOOL_SCHEMA)

    # Defensive defaults in case the model omits a key
    defaults = {
        "tour_name": "", "description": "", "hotels_text": "", "hotels_count": 1, "supplements": [], "included": "",
        "excluded": "", "meeting_point": "", "policy_remarks": "",
        "cancellation_policy_tiers": [], "cancellation_policy_text": "",
        "itinerary_destinations": [], "nights": 0, "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "pricing_notes": "", "stop_sales": [], "price_list": [], "release_days_mentions": []
    }
    # CONFIRMED REAL BUG: this used to be defaults.update(data), which is NOT None-safe - any
    # field Claude returns as JSON null OVERWRITES the safe default instead of falling back to
    # it (every other extract_* function in this file instead uses the "or is None" guard
    # below). Since the tool schema handed to the API has no type constraints, a stray null for
    # e.g. itinerary_destinations crashes downstream at len(None), or a None silently reaches
    # builder.py's payload math where a real default was expected.
    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default
    defaults = data

    # Deterministic double-check of the Nights-vs-Days naming rule (see
    # _fix_days_count_in_tour_name's docstring) - catches the AI copying a
    # wrong day count straight from the source document's own title.
    defaults["tour_name"] = _fix_days_count_in_tour_name(defaults.get("tour_name", ""), defaults.get("nights"))

    defaults["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(defaults.get("cancellation_policy_tiers"))

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

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
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
  NEVER LEAVE THIS EMPTY. A blank name is never an acceptable answer - a human reviewing this cannot
  publish a ticket with no name, and every real document names what it's selling somewhere (a heading, a
  title, a header/footer, the filename-like text at the top of a rate sheet, or a phrase repeated across
  the price tables like "Full Day X Excursion"). If there is genuinely no single obvious title anywhere,
  build a short, factual, non-invented name from what IS stated - the activity/city plus what's actually
  described (e.g. "Half Day City Tour" from a document about a half-day tour of a named city, "Private
  Airport Transfer" from a transfer document with no other name) - never a generic placeholder like
  "Excursion" or "Ticket" alone, and never leave it as an empty string under any circumstances.
- description: a SINGLE HTML block (not day-by-day) describing what the experience involves, written as
  natural, engaging, SEO-strong prose - the goal is compelling copy that reads well and ranks well, NOT
  a bare fact list. However, it must never lie or exaggerate: use ONLY facts, places, and activities
  actually present in the source - never invent details, ratings, superlatives, or claims that aren't
  there. Good SEO writing and factual accuracy are not in tension - rewrite HOW it's said, never WHAT is
  true. Format: <p>paragraph(s)</p> - keep it to 2-4 short paragraphs maximum.
  NEVER LEAVE THIS EMPTY. A blank description is never an acceptable answer. Even a bare-bones rate sheet
  with no marketing copy still states enough real facts to write from - the activity itself, the city,
  what's included, the duration, the schedule, the meeting point. Build 1-2 honest paragraphs from
  whatever real facts the source DOES give rather than returning an empty string - the rule above against
  inventing details means never adding a fact that isn't there, not leaving the field blank when facts
  exist to write from. Only if the source is so minimal that even a single true sentence cannot be
  written (essentially never in practice) should this be short rather than empty.
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
  FIND THE "INCLUSIONS" HEADING: many source documents have an explicit heading spelled "Inclusions",
  "Includes", "Included", or "What's Included" (any capitalization), usually followed by a bulleted or
  comma-separated list. Whenever such a heading exists ANYWHERE in the source you were given, you MUST
  read every item listed under it and put each one into this field as its own string - never leave this
  heading's content out, and never summarize/merge multiple bullet items into one string. Some documents
  bundling several excursions state ONE such heading as a shared/general section (appearing once, not
  repeated under each excursion) - if you were given a variant_hint for one specific excursion and the
  source also has a document-wide "Inclusions" heading like this, that shared content still applies to
  your excursion and must be included here too.
  GUIDE LANGUAGE RULE (same principle as tours): if a base/standard guide language is mentioned (usually
  English), make sure it's explicitly listed here (e.g. "English-speaking guide"). If OTHER languages are
  available (e.g. "German/French on request"), do NOT list them here - add each to extra_cost_options
  instead (see below), with group "Guide language", so each becomes its own Modality. A guide language (or
  any other extra a customer explicitly CHOOSES between) is never a supplement - it always becomes its own
  Modality via extra_cost_options. Only a DATED price change that is not a choice (a seasonal price table,
  a holiday surcharge) belongs in modality_supplements - see that field below.
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
- supplements: ALWAYS an empty list - this is a LEGACY field, kept only so old drafts don't crash. Use
  modality_supplements below for dated price changes and extra_cost_options below for chosen extras. Never
  populate this one.
- extra_cost_options: every EXTRA COST this same ticket can carry - the things that make the identical
  experience cost more. Each one becomes its own Ticket Modality downstream, priced at the base price PLUS
  this extra, so extract the EXTRA ON TOP, not the total.
  {"name": "clear customer-facing label, e.g. 'German-speaking guide'",
   "group": "the set of MUTUALLY EXCLUSIVE alternatives this belongs to, e.g. 'Guide language' - or \"\" if
     it is independently choosable alongside anything else, e.g. a lunch upgrade",
   "adult_price": <the EXTRA per adult, on top of the base>, "children_price": <the EXTRA per child>,
   "infant_price": <the EXTRA per infant, usually 0>}
  GROUP IS LOAD-BEARING. Options sharing a group can never be booked together (a booking has ONE guide
  language), so give every guide language the same group "Guide language". Give an independent add-on an
  empty group. Getting this wrong produces a sellable "English AND German guide" product, or blocks a
  genuinely valid combination.
  CRITICAL - EXTRA, NOT TOTAL: if the base (English) adult price is 40 and the German-guide price is 50,
  output adult_price: 10. The app adds the base itself. Outputting 50 would sell that modality at 90.
  CRITICAL - SEPARATE FULL PRICE TABLES: a very common real case is a document with a complete price table
  per guide language (an "English Speaking Guide" table, then a "German Speaking Guide" table with its own
  numbers). Take the FIRST/primary language's table as base_adult_price/base_children_price/
  base_infant_price, then for every other language output the DIFFERENCE per passenger type here. If the
  difference is not constant across the table (e.g. it varies by group size), still output your best single
  figure AND explain the variation with real numbers in pricing_notes so a human can correct it.
  CONFIRMED PRODUCT-OWNER RULE - FLAT PER-GROUP LANGUAGE SURCHARGE (not per-person): some documents instead
  give ONE flat per-day surcharge for the whole group for a foreign-language guide (e.g. "French: $9",
  "German: $47" - a flat add-on, not a per-adult price). Converting a flat group charge into a per-person
  extra can never be exactly correct since it depends how many people are in the group, so orient the
  conversion around 2 PAX specifically (Momira's main target group booking size): output
  adult_price = <the flat surcharge> / 2 for that language. Say in pricing_notes that this was approximated
  from a flat per-group surcharge assuming 2 passengers, with the real flat amount, so a human can adjust
  for a different group size if needed.
  CRITICAL - IGNORE voluntary carbon offset/carbon emission compensation charges entirely (e.g. "Optional
  CO2 offset contribution", "Carbon footprint compensation") - never add these here or anywhere else. This
  is a deliberate exclusion, not an oversight.
  PEAK SEASON IS NOT AN EXTRA COST OPTION: a peak-season/holiday surcharge is a date-restricted price
  change, not a product variant a customer chooses. Never put one here - put it in modality_supplements
  below instead.
  Empty list if the document prices no extras at all - never invent one.
- modality_supplements: every DATED price change to THIS Modality that is NOT a customer choice - the same
  experience simply costs more during a specific window. This is the confirmed mechanism for a seasonal
  price table (the same excursion priced higher in a "High"/"Peak" season than in "Normal" season) and for
  a holiday/peak-date surcharge (e.g. "Tet Holiday guide surcharges are subject to a 100% surcharge").
  Genuinely different product variants (a foreign-language guide, a Seat-in-Coach option) are NEVER put
  here - those stay in extra_cost_options above, unchanged.
  {"name": "clear label, e.g. 'High Season' or 'Tet Holiday Surcharge'",
   "adult_price_supplement": <the EXTRA per adult on top of base_adult_price during this window>,
   "children_price_supplement": <the EXTRA per child>, "infant_price_supplement": <usually 0>,
   "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}
  HOW TO FILL THIS FROM A SEASONAL PRICE TABLE: take the LOWEST-priced season (usually "Normal"/"Low") as
  base_adult_price/base_children_price/base_infant_price above. For every OTHER season, output one entry
  here per passenger type with that season's price MINUS the base price (the extra on top, not the total -
  same rule as extra_cost_options). If a season is valid for SEVERAL separate non-contiguous date ranges
  (common on these rate sheets - e.g. "2 Jan - 3 Feb", then "11 Feb - 15 Apr", then "2 May - 1 Sep", all at
  the identical price), output ONE ENTRY PER DATE RANGE, all sharing that same price delta - never merge
  them into one wide range, since the gaps between them belong to a DIFFERENT (cheaper or more expensive)
  season and merging would sell the wrong price during those gaps.
  HOW TO FILL THIS FROM A HOLIDAY/PEAK-DATE SURCHARGE: if the surcharge is stated as a PERCENTAGE of another
  amount (e.g. "100% surcharge" on a guide-language extra_cost_options entry, meaning the language extra
  doubles during the holiday), compute the actual currency delta from that other amount and output it here
  as its own entry with the holiday's own start_date/end_date (e.g. "5-9 February 2027" for Tet) - explain
  the percentage-of-what calculation in pricing_notes so a human can verify it. If the holiday's dates are
  not stated (e.g. "Public Holidays (to be advised at time of booking)"), do NOT invent dates - leave that
  one out of modality_supplements entirely and describe it in pricing_notes instead, since an entry needs
  both start_date and end_date to publish.
  Empty list if the document shows no seasonal/dated price variation at all - never invent one.
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
  CONFIRMED REAL SYSTEM LIMIT: 9 is the maximum bookable headcount - Travel Compositor cannot sell a
  Ticket to more than 9 people. NEVER output an entry with occupancy above 9, even if the source's table
  goes further (e.g. a "9-14" or "10+" column) - stop expanding a range at 9 and drop any exact-headcount
  column beyond it entirely. If the source priced groups above 9, say so in pricing_notes with the real
  numbers so a human can see what was there, but do not put it in occupancy_prices.
  SEAT-IN-COACH (SIC) COLUMN: some occupancy-band price tables carry an extra "SIC"/"Seat in Coach" column
  alongside the private/per-vehicle occupancy bands. CONFIRMED PRODUCT-OWNER RULE: if that column has a
  real price (not "N/A"/blank), it is a DIFFERENT pricing shape - a per-adult Distribution price, not part
  of this occupancy table - and becomes its OWN separate Modality (Distribution mode, adult-priced). Do NOT
  fold an SIC price into occupancy_prices. Instead state it clearly in pricing_notes with the exact SIC
  price found, so a human can create that Distribution-priced Modality. If the SIC column is "N/A"/blank,
  say nothing - that means Seat-in-Coach isn't offered for this excursion.
- pricing_notes: leave empty UNLESS something had to be approximated (e.g. a group-size-tiered price
  table forced onto adult/child/infant categories, pricing was genuinely absent from the source, a
  peak-season surcharge amount/date range had to be estimated or its dates were never stated, a
  Seat-in-Coach price was found (see occupancy_prices above), or an alternative/on-request option needs
  to become a separate Modality - see extra_cost_options rule above) - explain what, with real numbers
  where available, so a human can review.
- release_days_mentions: a list of integers - ANY explicit booking/reservation deadline or "release period"
  mentioned anywhere in the source (e.g. "must be booked at least 45 days before", "release period: 60
  days", "reservations required 30 days in advance"). DIFFERENT from a cancellation policy (e.g.
  "non-refundable inside 21 days") - do NOT include cancellation deadlines here, only booking/reservation/
  release deadlines. Convert weeks/months to days (e.g. "6 weeks" -> 42, "2 months" -> 60). If the source
  mentions MORE THAN ONE such deadline, include ALL of them as separate integers - a human will apply the
  safest (longest) one rather than you picking. Empty list if nothing like this is mentioned anywhere.
- cancellation_policy_tiers: whenever the source states its OWN specific cancellation-fee policy - usually
  a tiered schedule like "From 91 days or more before arrival, 25% ... From 90 to 61 days before arrival,
  50% ... From 60 to 46 days, 75% ... From 45 days to the day of check-in, 100%" - extract EVERY tier as
  {"days": <the LOWER bound of days-before-arrival for this tier>, "fee_percentage": <the cancellation
  FEE percentage for this tier, exactly as the source states it - this is a fee/charge percentage, NOT a
  refund percentage>}. Use the LOWER bound of each day-range as "days" (e.g. "90 to 61 days before
  arrival" -> days=61, "60 to 46 days" -> days=46, "45 days to the day of check-in" -> days=0, "91 days or
  more" -> days=91). A separately-mentioned "no-show" fee does NOT need its own entry if it matches the
  final/most-expensive tier's percentage - only give it a separate entry if it's numerically different.
  If the source states NO specific cancellation policy anywhere, return an EMPTY list - do not invent one.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy out as a
  short, clear, human-readable plain-text summary suitable for both internal notes and a customer-facing
  voucher - one line per tier, e.g. "Cancellation Policy:\n- 91+ days before arrival: 25% fee\n- 90-61
  days before arrival: 50% fee\n- 60-46 days before arrival: 75% fee\n- 45 days to check-in / no-show:
  100% fee". If the source also describes a genuinely different rule for last-minute/close-in bookings
  that doesn't fit the day/percentage tier structure (e.g. "bookings made within 30 days of departure
  follow a different fee schedule based on the booking date"), add it as one extra trailing sentence.
  Leave this field empty whenever cancellation_policy_tiers is empty.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
  "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
  "activity_type": "", "is_private": false, "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "occupancy_prices": [],
  "child_age_min": 2, "child_age_max": 12, "disallow_adult": false, "disallow_children": false,
  "disallow_infant": false, "operational_days": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
  "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
  "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0, "supplements": [],
  "extra_cost_options": [], "modality_supplements": [], "pricing_notes": "",
  "release_days_mentions": [], "cancellation_policy_tiers": [], "cancellation_policy_text": ""
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
    excursions = _detect_items(
        TICKET_VARIANT_DETECTION_PROMPT, raw_text, model, "multiple_excursions", "excursions",
        lambda e: " ".join(str(e.get("label") or "").split()).lower())
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
        # CONFIRMED REAL PRODUCT-OWNER COMPLAINT (real document: an Asian Trails "Essential
        # Trails" PDF bundling 4+ excursions, each under its own heading followed by its own
        # description paragraph, price tables, and a near-IDENTICAL "Inclusions"/"Good to know"
        # boilerplate block): ticket_name came back with the right title, but description came
        # back empty. The likely cause - several excursions in the same document share almost
        # the same wording ("As a former prosperous seaport during the 16th to 18th
        # centuries..." appears near-verbatim under TWO different headings) - a simple "extract
        # only this one" instruction doesn't tell the model HOW to keep several similar-sounding
        # sections apart, so it's plausible it either can't tell which paragraph is really this
        # excursion's, or gives up and returns nothing rather than risk stitching two together.
        # Being explicit about locating the exact heading text and only using what's directly
        # under it - even when nearby sections repeat the same sentences - gives it a concrete
        # procedure instead of just a name to search for.
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct excursions/tickets, each under its "
            f"own heading. Extract ONLY the following one, and completely ignore any other excursion "
            f"mentioned elsewhere in the text: {variant_hint}\n"
            f"HOW TO FIND IT: locate the heading in the source that matches \"{variant_hint}\" (or is "
            f"clearly the same excursion, allowing for minor wording differences), then use ONLY the "
            f"description paragraph, price tables, and details that appear DIRECTLY UNDER THAT HEADING - "
            f"stop at the next heading. Several excursions in a document like this can share nearly "
            f"identical boilerplate wording (the same descriptive sentences, the same \"Inclusions\"/"
            f"\"Good to know\" bullet points) - do not let that similarity make you unsure which text "
            f"belongs to \"{variant_hint}\" specifically. Its own paragraph exists somewhere in the "
            f"source; find it by heading position, not by trying to write a generic summary instead."
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    # Defensive defaults in case the model omits a key - defined BEFORE the call so the same
    # dict also builds the input_schema below (see _required_keys_schema's docstring: this is
    # the fix for the confirmed real bug where a permissive schema let a whole extraction come
    # back with every field silently empty).
    defaults = {
        "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
        "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
        "activity_type": None, "is_private": False, "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
        "child_age_min": 2, "child_age_max": 12, "disallow_adult": False, "disallow_children": False,
        "disallow_infant": False,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
        "adult_taxes_amount": 0, "child_taxes_amount": 0,
        "infant_taxes_amount": 0, "supplements": [], "extra_cost_options": [], "modality_supplements": [],
        "pricing_notes": "", "stop_sales": [], "image_urls": [],
        "price_type": "OCCUPANCY", "base_service_price": 0, "occupancy_prices": [], "release_days_mentions": [],
        "cancellation_policy_tiers": [], "cancellation_policy_text": "", "voucher_remarks": "",
    }

    # 16384 (up from a previous 8192) for the same headroom reason as the
    # tour extraction above - Tickets are usually shorter, but a long
    # description + many occupancy/supplement rows could still hit the old cap.
    data = _call_claude(TICKET_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=16384,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    # CONFIRMED REAL PRODUCT-OWNER COMPLAINT: "if ticket name and description is empty, the AI
    # needs to get more data from the document - it cannot show empty name and description." A
    # human reviewing an extraction cannot publish a ticket with a blank name, and the prompt
    # above now says never to leave either empty - but a prompt instruction is not a guarantee,
    # so this is the same kind of safety net apply_clarification() already uses for an empty
    # summary/changes: retry ONCE with an explicit corrective instruction naming exactly what
    # came back blank, rather than silently handing the human an empty field to notice (or not
    # notice) on their own.
    missing_required = [f for f in ("ticket_name", "description") if not (data.get(f) or "").strip()]
    if missing_required:
        retry_content = (
            user_content
            + f"\n\nIMPORTANT CORRECTION NEEDED: your previous extraction left "
              f"{' and '.join(missing_required)} empty. Neither may ever be blank - look again at "
              f"the ENTIRE source above and build a real value for each from whatever facts it "
              f"actually contains (a title, heading, or the activity/city/duration described), "
              f"per the rules already given for these fields. Do not invent facts that aren't in "
              f"the source, but do not return an empty string either - every real document has "
              f"enough in it to write from."
        )
        retry_data = _call_claude(TICKET_EXTRACTION_SYSTEM_PROMPT, retry_content, model, max_tokens=16384)
        for field in missing_required:
            new_value = (retry_data.get(field) or "").strip()
            if new_value:
                data[field] = new_value

    # CONFIRMED REAL BUG (product owner, real document): the retry above still isn't a
    # guarantee - a real multi-excursion PDF (several near-identical Asian Trails excursions
    # bundled in one document) came back with ticket_name empty on BOTH the first extraction
    # AND the retry, even though the review screen's own header already showed the correct
    # name ("Half Day Walking in Hoi An Ancient Town") - because that header is built from
    # variant_hint, a SEPARATE, already-confirmed-correct AI call (detect_ticket_variants),
    # not from this function's own output. As the product owner put it: "Ticket name cannot
    # be empty, as it is usually the Ticket name, which has been detected already." This is a
    # deterministic, code-level fallback - not a third AI attempt - so it can never fail the
    # same way the first two did: if variant_hint exists (this extraction was already told
    # exactly which excursion to find), a still-blank ticket_name always falls back to it.
    if not (data.get("ticket_name") or "").strip() and variant_hint:
        data["ticket_name"] = variant_hint.strip()

    # description has no equally reliable known-correct source to fall back to, but SOME
    # honest, non-empty text beats a blank field a human might publish without noticing -
    # built only from values this function already has in hand (name/city/duration), never
    # invented facts.
    if not (data.get("description") or "").strip():
        _desc_name = (data.get("ticket_name") or variant_hint or "This excursion").strip()
        _desc_city = (data.get("city") or "").strip()
        _desc_bits = _desc_name
        if _desc_city:
            _desc_bits += f" takes place in {_desc_city}."
        else:
            _desc_bits += "."
        data["description"] = f"<p>{_desc_bits}</p>"
        data["pricing_notes"] = (
            (data.get("pricing_notes") or "").strip()
            + ("\n\n" if data.get("pricing_notes") else "")
            + "AI could not extract a real description from the source even after retrying - the "
              "text above is a placeholder built from the name/city only. Please write a proper "
              "description from the document."
        ).strip()

    data["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(data.get("cancellation_policy_tiers"))

    # CONFIRMED REAL REQUEST (human feedback): the cancellation policy must
    # also show up on the customer-facing Voucher Remarks field, not just
    # internally - default it here from the same extracted text (a human can
    # still edit the two independently afterward in the review UI).
    if data.get("cancellation_policy_text") and not data.get("voucher_remarks"):
        data["voucher_remarks"] = data["cancellation_policy_text"]

    return _finalize_ticket_price_type(data)


def _finalize_ticket_price_type(data: dict) -> dict:
    """
    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): "Can we only add occupancy or per Service for
    the ticket. Always Occupancy first ... and 9 rows and in each row one pax with one price. If
    the price is always same (like Distribution, then it would just 9 times the same price added
    in each row)." The Ticket pricing editor (render_ticket_pricing_editor, ui_components.py) no
    longer offers a Distribution mode at all - only Occupancy (always exactly N rows, 1 through
    this Ticket's own Max Passengers capped at 9) and Service remain, and a "DISTRIBUTION"
    price_type reaching it is auto-converted into a flat Occupancy table (the old flat
    base_adult_price repeated across every row) the moment it's rendered.

    Extraction itself is UNCHANGED - it still returns a flat base_adult_price/base_children_price/
    base_infant_price for a flat-priced source, or a tiered occupancy_prices table for a
    group-size-banded one (occupancy_prices is only ever populated when a genuine per-headcount
    table exists in the source - see the occupancy_prices field rule above). This function just
    decides which shape was actually extracted and labels it correctly for the UI:
    - occupancy_prices non-empty -> "OCCUPANCY" (a genuine group-size-tiered table was found).
    - otherwise -> "DISTRIBUTION" (only a flat per-person price was found) - the UI's own
      conversion then repeats it into the Occupancy table automatically. Without this, a
      flat-priced extraction (the common case) always defaulted to "OCCUPANCY" with an EMPTY
      table, so the human never even saw the extracted price without manually digging for it.
    """
    data["price_type"] = "OCCUPANCY" if data.get("occupancy_prices") else "DISTRIBUTION"
    return data


TICKET_MAIN_INFO_SYSTEM_PROMPT = """You are extracting ONLY the MAIN TICKET information for a Travel
Compositor TICKET (an excursion/activity - single destination, no overnight stay) from a DMC supplier
document. Pricing and Modality data (base prices, occupancy tables, extra costs, seasonal supplements,
operational days, time slots) are extracted SEPARATELY by another step - do NOT try to find or report
pricing information here, and do NOT let a confusing/complex price table distract you from the fields
below, which are about the excursion itself, not what it costs. This is DIFFERENT from a multi-day tour:
no itinerary, no day-by-day description, no room-occupancy pricing. Translate ALL content to English
regardless of source language.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it. ALWAYS OUTPUT YYYY-MM-DD.

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly (e.g. "Please contact the operator 48 hours before your tour date to confirm your pick-up time",
"Contact us to confirm timing", "Call the supplier to reconfirm your booking"). Momira Travel is the tour
operator the client actually deals with - the client must NEVER be told to contact the DMC/supplier
directly, since that DMC is Momira's backend supplier, not the client-facing operator. This applies
EVERYWHERE such an instruction could appear - description, includes/excludes, meeting points, anywhere -
silently drop/omit it entirely rather than including, paraphrasing, or softening it. This is a deliberate
exclusion, not an oversight.

Extract:
- ticket_name: the excursion/activity name - keep close to the source, don't invent a fancier title.
  NEVER LEAVE THIS EMPTY. A blank name is never an acceptable answer - a human reviewing this cannot
  publish a ticket with no name, and every real document names what it's selling somewhere (a heading, a
  title, a header/footer, the filename-like text at the top of a rate sheet, or a phrase repeated across
  the price tables like "Full Day X Excursion"). If there is genuinely no single obvious title anywhere,
  build a short, factual, non-invented name from what IS stated - the activity/city plus what's actually
  described (e.g. "Half Day City Tour" from a document about a half-day tour of a named city, "Private
  Airport Transfer" from a transfer document with no other name) - never a generic placeholder like
  "Excursion" or "Ticket" alone, and never leave it as an empty string under any circumstances.
- description: a SINGLE HTML block (not day-by-day) describing what the experience involves, written as
  natural, engaging, SEO-strong prose - the goal is compelling copy that reads well and ranks well, NOT
  a bare fact list. However, it must never lie or exaggerate: use ONLY facts, places, and activities
  actually present in the source - never invent details, ratings, superlatives, or claims that aren't
  there. Good SEO writing and factual accuracy are not in tension - rewrite HOW it's said, never WHAT is
  true. Format: <p>paragraph(s)</p> - keep it to 2-4 short paragraphs maximum.
  NEVER LEAVE THIS EMPTY. A blank description is never an acceptable answer. Even a bare-bones rate sheet
  with no marketing copy still states enough real facts to write from - the activity itself, the city,
  what's included, the duration, the schedule, the meeting point. Build 1-2 honest paragraphs from
  whatever real facts the source DOES give rather than returning an empty string - the rule above against
  inventing details means never adding a fact that isn't there, not leaving the field blank when facts
  exist to write from. Only if the source is so minimal that even a single true sentence cannot be
  written (essentially never in practice) should this be short rather than empty.
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
  FIND THE "INCLUSIONS" HEADING: many source documents have an explicit heading spelled "Inclusions",
  "Includes", "Included", or "What's Included" (any capitalization), usually followed by a bulleted or
  comma-separated list. Whenever such a heading exists ANYWHERE in the source you were given, you MUST
  read every item listed under it and put each one into this field as its own string - never leave this
  heading's content out, and never summarize/merge multiple bullet items into one string.
  SHARED/GENERAL INCLUSIONS: some documents (especially ones bundling several excursions, like a supplier
  rate sheet with one excursion per heading) state ONE "Inclusions" section that is shared/general and
  applies to EVERY excursion in the document, rather than repeating it under each excursion's own
  heading - e.g. it appears once, on its own page or at the end, not directly under any single excursion's
  heading. If you were given a variant_hint for one specific excursion below, and the source ALSO has a
  document-wide/shared "Inclusions" heading like this that is not specific to any other excursion, that
  shared content STILL applies to your excursion and must be included here too - do not skip it just
  because it isn't physically positioned under your excursion's own heading.
  GUIDE LANGUAGE RULE: if a base/standard guide language is mentioned (usually English), make sure it's
  explicitly listed here (e.g. "English-speaking guide"). If OTHER languages are available on request
  (e.g. "German/French on request"), do NOT list them here - the separate Modality/Pricing step turns
  each into its own Modality. DUAL-LANGUAGE GUIDE RULE: if the source lists TWO (or more) languages joined
  by "/" or "or" as EQUAL standard options (e.g. "licenced English/German-speaking guiding service"), that
  is NOT a base-language-plus-on-request-extra - keep the includes entry as the source states it. In this
  case the description ALSO MUST clearly state, in plain words, that the tour/transfer can run in EITHER
  English OR German - work one clear sentence to that effect into the description.
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
  (NOT how many days a pass is valid for). Use 0/"HOURS" if unclear.
- activity_type: a short category label if the source suggests one (e.g. "Tickets", "Tours"), else omit.
- is_private: true if the source describes this as a PRIVATE experience (e.g. "private tour", "private
  transfers", "private guide", "exclusively for your group") as opposed to a joint/shared/group/public
  one (e.g. "joint public tour", "shared transfer", "group tour"). If the source doesn't specify either
  way, default to false rather than guessing.
- release_days_mentions: a list of integers - ANY explicit booking/reservation deadline or "release period"
  mentioned anywhere in the source (e.g. "must be booked at least 45 days before", "release period: 60
  days", "reservations required 30 days in advance"). DIFFERENT from a cancellation policy - do NOT
  include cancellation deadlines here, only booking/reservation/release deadlines. Convert weeks/months to
  days (e.g. "6 weeks" -> 42, "2 months" -> 60). If the source mentions MORE THAN ONE such deadline,
  include ALL of them as separate integers. Empty list if nothing like this is mentioned anywhere.
- cancellation_policy_tiers: whenever the source states its OWN specific cancellation-fee policy - usually
  a tiered schedule like "From 91 days or more before arrival, 25% ... From 90 to 61 days before arrival,
  50% ... From 60 to 46 days, 75% ... From 45 days to the day of check-in, 100%" - extract EVERY tier as
  {"days": <the LOWER bound of days-before-arrival for this tier>, "fee_percentage": <the cancellation
  FEE percentage for this tier, exactly as the source states it - this is a fee/charge percentage, NOT a
  refund percentage>}. Use the LOWER bound of each day-range as "days" (e.g. "90 to 61 days before
  arrival" -> days=61, "60 to 46 days" -> days=46, "45 days to the day of check-in" -> days=0, "91 days or
  more" -> days=91). If the source states NO specific cancellation policy anywhere, return an EMPTY list.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy out as a
  short, clear, human-readable plain-text summary suitable for both internal notes and a customer-facing
  voucher - one line per tier. Leave this field empty whenever cancellation_policy_tiers is empty.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
  "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
  "activity_type": "", "is_private": false,
  "release_days_mentions": [], "cancellation_policy_tiers": [], "cancellation_policy_text": ""
}"""


def extract_ticket_main_info(raw_text: str, model: str = "claude-sonnet-5", variant_hint: str = None, human_hint: str = None) -> dict:
    """Extraction for a new Ticket's MAIN info only (name/description/city/includes/excludes/meeting
    points/duration/cancellation policy) - deliberately excludes pricing/Modality fields, which are
    extracted separately by extract_ticket_modality_data(). CONFIRMED PRODUCT-OWNER REQUEST: "The App
    must first detect the main Information of the Ticket and only after the first step, the App must
    detect the Modality" - splitting the two extractions keeps a complex pricing table from crowding
    out (or getting confused with) the main info in the SAME AI call, which was the likely cause of a
    real production bug where ticket_name/description came back empty on a multi-excursion document
    with heavy seasonal price tables."""
    user_content = raw_text
    prefix_parts = []
    if variant_hint:
        # Same reasoning as extract_ticket_data's identical block - a multi-excursion document needs
        # to be told exactly which heading/section to read, or near-identical boilerplate wording
        # across excursions can make the model unsure which paragraph belongs to which excursion.
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct excursions/tickets, each under its "
            f"own heading. Extract ONLY the following one, and completely ignore any other excursion "
            f"mentioned elsewhere in the text: {variant_hint}\n"
            f"HOW TO FIND IT: locate the heading in the source that matches \"{variant_hint}\" (or is "
            f"clearly the same excursion, allowing for minor wording differences), then use ONLY the "
            f"description paragraph and details that appear DIRECTLY UNDER THAT HEADING - stop at the "
            f"next heading. Several excursions in a document like this can share nearly identical "
            f"boilerplate wording (the same descriptive sentences, the same \"Inclusions\"/\"Good to "
            f"know\" bullet points) - do not let that similarity make you unsure which text belongs to "
            f"\"{variant_hint}\" specifically. Its own paragraph exists somewhere in the source; find it "
            f"by heading position, not by trying to write a generic summary instead.\n"
            f"EXCEPTION for a shared/general Inclusions section: some of these documents also have ONE "
            f"\"Inclusions\"/\"Includes\" heading that is document-wide (not repeated per excursion - e.g. "
            f"it appears once, on its own page, not directly under \"{variant_hint}\"'s own heading). If "
            f"you find one like that, its content still applies to \"{variant_hint}\" and MUST be read "
            f"into the includes field - do not skip it just because it sits outside this excursion's own "
            f"heading block."
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    defaults = {
        "ticket_name": "", "description": "", "city": "", "includes": [], "excludes": [],
        "meeting_points": [], "meeting_point_summary": "", "duration": 0, "duration_type": "HOURS",
        "activity_type": None, "is_private": False,
        "release_days_mentions": [], "cancellation_policy_tiers": [], "cancellation_policy_text": "",
    }
    data = _call_claude(TICKET_MAIN_INFO_SYSTEM_PROMPT, user_content, model, max_tokens=8192,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    # Same safety net as extract_ticket_data used to run inline - kept here since this is now the
    # function actually responsible for ticket_name/description.
    missing_required = [f for f in ("ticket_name", "description") if not (data.get(f) or "").strip()]
    if missing_required:
        retry_content = (
            user_content
            + f"\n\nIMPORTANT CORRECTION NEEDED: your previous extraction left "
              f"{' and '.join(missing_required)} empty. Neither may ever be blank - look again at "
              f"the ENTIRE source above and build a real value for each from whatever facts it "
              f"actually contains (a title, heading, or the activity/city/duration described), "
              f"per the rules already given for these fields. Do not invent facts that aren't in "
              f"the source, but do not return an empty string either - every real document has "
              f"enough in it to write from."
        )
        retry_data = _call_claude(TICKET_MAIN_INFO_SYSTEM_PROMPT, retry_content, model, max_tokens=8192)
        for field in missing_required:
            new_value = (retry_data.get(field) or "").strip()
            if new_value:
                data[field] = new_value

    # CONFIRMED REAL BUG (product owner, real document): even the retry above isn't a guarantee - a
    # multi-excursion PDF came back with ticket_name empty on BOTH attempts even though the review
    # screen's own header already showed the correct name, because that header is built from
    # variant_hint (a SEPARATE, already-confirmed-correct AI call, detect_ticket_variants), not from
    # this function's own output. Deterministic, code-level fallback - not a third AI attempt.
    if not (data.get("ticket_name") or "").strip() and variant_hint:
        data["ticket_name"] = variant_hint.strip()

    if not (data.get("description") or "").strip():
        _desc_name = (data.get("ticket_name") or variant_hint or "This excursion").strip()
        _desc_city = (data.get("city") or "").strip()
        _desc_bits = _desc_name
        if _desc_city:
            _desc_bits += f" takes place in {_desc_city}."
        else:
            _desc_bits += "."
        data["description"] = f"<p>{_desc_bits}</p>"

    data["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(data.get("cancellation_policy_tiers"))

    if data.get("cancellation_policy_text") and not data.get("voucher_remarks"):
        data["voucher_remarks"] = data["cancellation_policy_text"]

    return data


TICKET_MODALITY_SYSTEM_PROMPT = """You are extracting ONLY pricing/schedule/Modality data for a Travel
Compositor Ticket Modality (ContractTicketModalityVO). This is NOT a full ticket extraction - do NOT
extract ticket name, description, city, meeting points, includes/excludes, or cancellation policy (those
were already captured separately, in an earlier step). The source is a rate/pricing table for an
excursion that has already been identified.

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies, then take each price from the cell covering that
same range. Never match a price to a season by counting values left to right - merged cells make
the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading. Every one of those is its own entry, all
carrying that season's SAME prices. Do not merge them into one long range. Do not drop the later
ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. ALWAYS OUTPUT YYYY-MM-DD.

Extract:
- base_adult_price, base_children_price, base_infant_price: the core prices found in the source, as numbers.
  CRITICAL RULE for base_children_price specifically: if children are allowed (not disallow_children) but
  the source gives only ONE price with no distinct child rate, set base_children_price EQUAL to
  base_adult_price - NOT 0. A price of 0 specifically means "this passenger type travels free", so only
  use 0 if the source EXPLICITLY says children are free/complimentary, or if disallow_children is true.
  base_infant_price commonly IS genuinely 0 by convention - only set it non-zero if the source states an
  actual infant price. If pricing is genuinely absent/blank in the source, leave base_adult_price (and
  therefore base_children_price too) as 0 - do NOT invent numbers - and mention this in pricing_notes.
- child_age_min, child_age_max: the age range that counts as "child" pricing, AND/OR any stated age
  eligibility restriction (e.g. "children must be at least 12"). Both kinds of language should populate
  these fields. Actively look anywhere in the source (a "Good to know" section counts, not just a
  pricing table), else 2/12 as the standard default.
- disallow_adult, disallow_children, disallow_infant: true only if the source explicitly says a passenger
  type isn't allowed (rare) - otherwise all false.
- operational_days: list of uppercase weekday names this is available. Actively search the ENTIRE source
  for weekday schedule information - phrases like "operated ... as following schedules:", "departs on",
  "available every", or a bare list of weekday names all count. Only fall back to all 7 days if the
  source is GENUINELY silent about which days it runs.
  MULTI-LANGUAGE SCHEDULE RULE: if the source gives a SEPARATE weekday schedule per guide language, and a
  human hint below names a specific language/focus for THIS modality, use that language's exact days -
  never a union, never all 7 by default. If no hint narrows it down, use the base/standard language's
  schedule and note any other languages' differing days in schedule_notes.
- schedule_notes: if operational days are NOT YET DETERMINED (e.g. "TBD by Operations"), say so plainly.
  Also use this field for the multi-language schedule discrepancy note described above. Empty otherwise.
- time_tables: list of specific departure/start times as strings (e.g. ["09:00", "14:00"]) if the source
  gives specific time slots - empty list if not applicable.
- start_date, end_date: the validity date range for this specific modality/price (YYYY-MM-DD). If the
  source gives no clear range, use a wide default like today's year to 3 years out.
- adult_taxes_amount, child_taxes_amount, infant_taxes_amount: any separately-stated taxes/fees, else 0.
- extra_cost_options: every EXTRA COST this same ticket can carry - the things that make the identical
  experience cost more. Each one becomes its own Ticket Modality downstream, priced at the base price PLUS
  this extra, so extract the EXTRA ON TOP, not the total.
  {"name": "clear customer-facing label, e.g. 'German-speaking guide'",
   "group": "the set of MUTUALLY EXCLUSIVE alternatives this belongs to, e.g. 'Guide language' - or \"\" if
     it is independently choosable alongside anything else, e.g. a lunch upgrade",
   "adult_price": <the EXTRA per adult, on top of the base>, "children_price": <the EXTRA per child>,
   "infant_price": <the EXTRA per infant, usually 0>}
  GROUP IS LOAD-BEARING. Options sharing a group can never be booked together (a booking has ONE guide
  language), so give every guide language the same group "Guide language". Give an independent add-on an
  empty group.
  CRITICAL - EXTRA, NOT TOTAL: if the base (English) adult price is 40 and the German-guide price is 50,
  output adult_price: 10. The app adds the base itself.
  CRITICAL - SEPARATE FULL PRICE TABLES: a very common real case is a document with a complete price table
  per guide language. Take the FIRST/primary language's table as base_adult_price/base_children_price/
  base_infant_price, then for every other language output the DIFFERENCE per passenger type here.
  CONFIRMED PRODUCT-OWNER RULE - FLAT PER-GROUP LANGUAGE SURCHARGE (not per-person): some documents instead
  give ONE flat per-day surcharge for the whole group for a foreign-language guide (e.g. "German: $47" - a
  flat add-on, not a per-adult price). Orient the conversion around 2 PAX specifically: output
  adult_price = <the flat surcharge> / 2 for that language, and say so in pricing_notes.
  CRITICAL - IGNORE voluntary carbon offset/carbon emission compensation charges entirely.
  PEAK SEASON IS NOT AN EXTRA COST OPTION: a peak-season/holiday surcharge is a date-restricted price
  change, not a product variant a customer chooses. Put it in modality_supplements below instead.
  Empty list if the document prices no extras at all - never invent one.
- modality_supplements: every DATED price change to THIS Modality that is NOT a customer choice - the same
  experience simply costs more during a specific window (a seasonal price table, a holiday surcharge).
  Genuinely different product variants are NEVER put here - those stay in extra_cost_options above.
  {"name": "clear label, e.g. 'High Season' or 'Tet Holiday Surcharge'",
   "adult_price_supplement": <the EXTRA per adult on top of base_adult_price during this window>,
   "children_price_supplement": <the EXTRA per child>, "infant_price_supplement": <usually 0>,
   "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}
  HOW TO FILL THIS FROM A SEASONAL PRICE TABLE: take the LOWEST-priced season as base_adult_price/
  base_children_price/base_infant_price above. For every OTHER season, output one entry here per
  passenger type with that season's price MINUS the base price. If a season is valid for SEVERAL separate
  non-contiguous date ranges, output ONE ENTRY PER DATE RANGE, all sharing that same price delta.
  HOW TO FILL THIS FROM A HOLIDAY/PEAK-DATE SURCHARGE: if stated as a PERCENTAGE, compute the actual
  currency delta and output it here with the holiday's own dates - explain the calculation in
  pricing_notes. If the holiday's dates are not stated, do NOT invent dates - leave it out and describe it
  in pricing_notes instead.
  Empty list if the document shows no seasonal/dated price variation at all - never invent one.
- occupancy_prices: ONLY populate if the human indicates Occupancy pricing mode is being used. If the
  source has a group-size-tiered price table (columns like "1", "2", "3-5", "6-8"), extract it here
  instead of forcing it into base_adult_price. Each entry is {"occupancy": exact integer headcount,
  "amount": price for that exact headcount} - EXPAND a range into one entry per exact number. Infants are
  always free and excluded from occupancy counts.
  NOTE: real documents commonly show "N/A" for the solo/1-pax column. If missing, do NOT invent one - just
  mention in pricing_notes that a solo/1-pax price is missing.
  CONFIRMED REAL SYSTEM LIMIT: 9 is the maximum bookable headcount - NEVER output an entry with occupancy
  above 9. If the source priced groups above 9, say so in pricing_notes but do not put it in occupancy_prices.
  SEAT-IN-COACH (SIC) COLUMN: some occupancy-band price tables carry an extra "SIC"/"Seat in Coach" column
  alongside the private/per-vehicle occupancy bands. If that column has a real price, it is a DIFFERENT
  pricing shape (a per-adult Distribution price) and becomes its OWN separate Modality - do NOT fold it
  into occupancy_prices. State it clearly in pricing_notes with the exact SIC price found.
- pricing_notes: leave empty UNLESS something had to be approximated - explain what, with real numbers
  where available, so a human can review.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
  "child_age_min": 2, "child_age_max": 12, "disallow_adult": false, "disallow_children": false,
  "disallow_infant": false, "operational_days": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
  "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
  "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0,
  "extra_cost_options": [], "modality_supplements": [], "pricing_notes": "",
  "price_type": "OCCUPANCY", "base_service_price": 0, "occupancy_prices": []
}"""


def extract_ticket_modality_data(raw_text: str, model: str = "claude-sonnet-5", variant_hint: str = None,
                                  modality_hint: str = None, human_hint: str = None) -> dict:
    """Extraction for a Ticket's FIRST/base Modality (pricing/schedule fields only) - the counterpart to
    extract_ticket_main_info() above. CONFIRMED PRODUCT-OWNER REQUEST: main info and Modality must be
    detected as two separate steps/calls, not blended into one. modality_hint optionally narrows which
    pricing category to read when this ticket has more than one (see detect_ticket_modalities) - same
    role as variant_hint plays for picking the right excursion in a multi-excursion document."""
    user_content = raw_text
    prefix_parts = []
    if variant_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct excursions/tickets, each under its "
            f"own heading. Extract pricing ONLY for the following one, and completely ignore any other "
            f"excursion mentioned elsewhere in the text: {variant_hint}\n"
            f"HOW TO FIND IT: locate the heading in the source that matches \"{variant_hint}\" (or is "
            f"clearly the same excursion, allowing for minor wording differences), then use ONLY the "
            f"price tables and details that appear DIRECTLY UNDER THAT HEADING - stop at the next heading."
        )
    if modality_hint:
        prefix_parts.append(
            f"IMPORTANT: This excursion has MORE THAN ONE pricing category/Modality. Extract pricing "
            f"ONLY for this specific one, ignoring any other pricing category's rows/columns/tables: "
            f"{modality_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    defaults = {
        "base_adult_price": 0, "base_children_price": 0, "base_infant_price": 0,
        "child_age_min": 2, "child_age_max": 12, "disallow_adult": False, "disallow_children": False,
        "disallow_infant": False,
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "time_tables": [], "start_date": "", "end_date": "",
        "adult_taxes_amount": 0, "child_taxes_amount": 0, "infant_taxes_amount": 0,
        "extra_cost_options": [], "modality_supplements": [], "pricing_notes": "", "stop_sales": [],
        "price_type": "OCCUPANCY", "base_service_price": 0, "occupancy_prices": [],
        "supplements": [],
    }
    data = _call_claude(TICKET_MODALITY_SYSTEM_PROMPT, user_content, model, max_tokens=16384,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default
    return _finalize_ticket_price_type(data)


TICKET_MODALITY_DETECTION_PROMPT = """You are checking whether a DMC supplier document/page prices MORE
THAN ONE distinct Modality (pricing category) for what is otherwise the SAME single excursion/ticket -
e.g. a complete separate price table per guide language ("English Speaking Guide" table, then a "German
Speaking Guide" table), or per service tier ("Private" vs "Seat-in-Coach"), where a human would want each
one created as its OWN Modality with its own dedicated extraction pass, rather than one one blended call
trying to average or approximate across all of them at once.

Only count it as multiple Modalities if they are genuinely separate, fully-priced categories - not just a
single price table with a few optional extras or a seasonal surcharge (those stay as extra_cost_options /
modality_supplements on ONE Modality, handled elsewhere).

CRITICAL - CONFIRMED REAL FAILURE TO AVOID: "suggested_code" gets sent DIRECTLY to Travel Compositor's
API as the Modality's identifier - it must be SHORT and CLEAN, just the category name itself. NEVER
include: parenthetical/explanatory text, numbers describing pax/occupancy/min-max requirements, the word
"people"/"pax"/"person", periods, or slashes. Correct: "Standard", "Deluxe", "German". Wrong: "Standard
English min. 2 people", "German Speaking Guide (on request)".

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_modalities": true or false,
  "modalities": [
    {"label": "short human-readable label, e.g. 'German Speaking Guide'", "suggested_code": "e.g. 'German' - ONLY the core category name, no / + - characters, no extra words"}
  ]
}
If there's only one pricing category (or pricing is a single flat table with optional extras), set
"multiple_modalities": false and "modalities": [] ."""


def detect_ticket_modalities(raw_text: str, variant_hint: str = None, model: str = HAIKU_MODEL) -> list:
    """
    Checks whether THIS excursion (optionally narrowed by variant_hint on a multi-excursion document)
    is priced as multiple distinct Modalities (e.g. one full price table per guide language) that
    should each get their own dedicated extraction call, rather than one call trying to cover all of
    them. CONFIRMED PRODUCT-OWNER REQUEST: "if the ticket has detected another Modality, the app must
    call for each modality separately." Mirrors detect_multiple_modalities (ClosedTour) exactly.
    """
    print("🔎 Checking for multiple pricing Modalities for this ticket...")
    content = raw_text
    if variant_hint:
        content = (
            f"IMPORTANT: only consider the section of this document under the heading for "
            f"\"{variant_hint}\" - ignore any other excursion's pricing entirely.\n\n--- Source content ---\n{raw_text}"
        )
    modalities = _detect_items(
        TICKET_MODALITY_DETECTION_PROMPT, content, model, "multiple_modalities", "modalities",
        lambda m: " ".join(str(m.get("suggested_code") or m.get("label") or "").split()).lower())
    if modalities:
        print(f"⚠️ Detected {len(modalities)} distinct ticket Modalities: {[m.get('label') for m in modalities]}")
    else:
        print("✅ Only one pricing Modality detected.")
    return modalities


TICKET_OPTION_ONLY_SYSTEM_PROMPT = """You are extracting ONLY pricing/schedule data for a Travel

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
Compositor Ticket Modality (ContractTicketModalityVO). This is NOT a full ticket extraction - do NOT
extract ticket name, description, city, meeting points, includes/excludes, or cancellation policy
(cancellation and release timing belong to the TICKET itself, not the modality, and aren't touched
when just adding/updating a modality). The source is often just a pricing table for an ALREADY-EXISTING ticket.

Extract ONLY: base_adult_price, base_children_price, base_infant_price, child_age_min, child_age_max,
start_date, end_date (this modality's validity window), operational_days, time_tables,
modality_supplements (DATED price changes only - a seasonal price table or a holiday surcharge, NOT a
customer choice - see full prompt's rules on this: each entry is {"name", "adult_price_supplement",
"children_price_supplement", "infant_price_supplement", "start_date", "end_date"}, both dates required),
pricing_notes. Never exclusive alternatives, different guide languages, or on-request items here - those
belong in extra_cost_options as their own Modality, and NEVER voluntary carbon offset/emission compensation
charges, ignore those entirely.

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
  "time_tables": [], "supplements": [], "extra_cost_options": [], "modality_supplements": [], "pricing_notes": ""
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
        "supplements": [], "extra_cost_options": [], "modality_supplements": [], "pricing_notes": "", "stop_sales": [],
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
    return _finalize_ticket_price_type(data)


# ==========================================
# 7. TRANSFER EXTRACTION
# Confirmed against 3 real supplier rate sheets (Hurghada point-to-point,
# Masons Travel Seychelles per-service/per-pax mix, Asian Trails Bali
# zone-based bracket pricing) plus a series of product-owner clarifications
# on how DMC rate-sheet conventions should map onto ContractTransferVO -
# see schemas.py's Transfer section and builder.py for the payload side.
# ==========================================

TRANSFER_PRODUCT_DETECTION_PROMPT = """You are scanning a DMC supplier TRANSFER rate sheet/tariff document to
identify every DISTINCT transfer product it describes, so each can be reviewed and uploaded to Travel
Compositor as its own record.

A DISTINCT transfer product is one specific ROUTE (a departure area/point to an arrival area/point) at one
specific SERVICE/VEHICLE CLASS/TIER (e.g. "Private Car Transfer", "Seat In Coach Transfer", "Standard",
"Superior").

CRITICAL - GUIDE LANGUAGE IS NEVER A SEPARATE PRODUCT: real rate sheets commonly repeat the ENTIRE set of
routes multiple times, once per guide language (e.g. a full "English-speaking guide" price table, then an
identical-structure "German-speaking guide" table, "French-speaking guide" table, etc, covering the same
routes with different prices). This is NOT multiple products - a transfer's default is always driver-only
(no guide), and any guide language is an optional extra on top. List each route+class combination only
ONCE regardless of how many guide-language tables it appears in - do not create a separate candidate per
language. A later, deeper extraction step handles pulling every language's price for you.

DIRECTION: CONFIRMED REAL RULE (product owner) - "when one Transport or Transfer is being created, it
always has to be the second one as well, for the return option." Travel Compositor stores a route as
departure -> arrival, so both directions are separate records. List each direction as its own candidate.
The reverse leg is added automatically afterwards if you miss it, so a route listed once is not lost -
but do NOT deliberately fold "A to B" and "B to A" into a single candidate. Same price both ways unless
the document says otherwise.

For each distinct route+class product found, output a candidate with:
- label: short human-readable summary, e.g. "Private Car Transfer: Mahe Central <-> Mahe Central"
- service_name: exactly as the document names this service/tier, e.g. "Private Car Transfer", "Standard", "Seat In Coach Transfer"
- departure_hint: the departure area/point name(s) as stated in the document
- arrival_hint: the arrival area/point name(s) as stated in the document

HUMAN INSTRUCTION OVERRIDES EVERYTHING ABOVE. If an instruction from the operator is given, it decides
what to list and the rules above are only a fallback for whatever the instruction does not cover. "Only
the Hurghada routes" means list every route in the Hurghada section and nothing from the other sections.
"Only the private ones" means ignore the shuttle/seat-in-coach column entirely. "All of them, including
the local ones" means exactly that - list them all, even the ones you would otherwise judge to be local
transfers. The operator can see the document and knows how these products are being sold; do not
second-guess an explicit instruction.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_transfers": true or false,
  "transfers": [
    {"label": "...", "service_name": "...", "departure_hint": "...", "arrival_hint": "..."}
  ]
}
ALWAYS LIST WHAT YOU FOUND, INCLUDING WHEN THERE IS ONLY ONE.
An earlier version of this prompt ended by telling you to return an EMPTY list when the document held only
one product. That instruction caused a real, repeated production failure: on a rate sheet pricing forty
routes the answer came back empty, the operator saw a blank screen with no explanation, and there was no
way to tell the difference between "found nothing" and "chose to say nothing". It is withdrawn.

  * One product found  -> "multiple_transfers": false, and "transfers" containing that ONE product.
  * Several found      -> "multiple_transfers": true, and every one of them.
  * The document prices no routes at all (it is an invoice, a contract, a covering letter)
                       -> "multiple_transfers": false and an empty "transfers". This is the ONLY case where the list
                          may be empty.

The boolean is a summary of the list, never a substitute for it. Never leave the list empty for a document
that visibly prices routes."""


def detect_transfer_products(raw_text: str, model: str = "claude-sonnet-5",
                             human_hint: str = None) -> list:
    """
    Checks whether the source describes MULTIPLE distinct transfer products
    (route + service/class combinations, deduplicated across any repeated
    guide-language tables) as opposed to a single one. Returns an empty
    list if only one is found, or a list of {"label", "service_name",
    "departure_hint", "arrival_hint"} dicts - mirrors detect_multiple_modalities'
    contract for the existing batch/queue review UI pattern.
    """
    print("🔎 Checking for multiple distinct transfer products (routes/classes) in this document...")
    transfers = _detect_items(TRANSFER_PRODUCT_DETECTION_PROMPT,
                              _with_hint(raw_text, human_hint), model,
                              "multiple_transfers", "transfers",
                              _directional_route_identity)
    if transfers:
        print(f"⚠️ Detected {len(transfers)} distinct transfer product(s): {[t.get('label') for t in transfers]}")
    else:
        print("✅ Only one distinct transfer product detected.")
    return transfers


TRANSFER_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data for a Travel Compositor TRANSFER

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
(a point-to-point or zone-to-zone vehicle transfer, e.g. airport-to-hotel) from a DMC supplier rate sheet.
Translate ALL content to English regardless of source language.

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly. Momira Travel is the tour operator the client actually deals with - the client must NEVER be
told to contact the DMC/supplier directly. Silently drop/omit any such text wherever it could appear.

If a human hint above names a specific route/service, extract ONLY that one product and ignore every other
route/service described elsewhere in the document.

FINDING THE HINTED ROUTE IN THE DOCUMENT. CONFIRMED REAL FAILURE (product owner): the hint read
"Private Transfer - Marsa Alam to El Quseir" while the document's row reads "RMF Airport | El Quseir |
20 | 30", and every extracted field came back empty. The hint names PLACES; a rate sheet often names
AIRPORTS or abbreviations, and a section heading may be the only place the departure city is written.
  * Resolve airports to their cities in BOTH directions before matching: "RMF Airport" is Marsa Alam,
    "HRG Airport" is Hurghada, "SSH Airport" is Sharm El Sheikh, "CAI Airport" is Cairo, and in general
    "<city> Airport" is that city. So the hint "Marsa Alam to El Quseir" MATCHES the row
    "RMF Airport | El Quseir".
  * Rows under a heading like "Transfer Fees Marsa Allam" depart from Marsa Alam even where the cell
    shows only an airport code.
  * The hint may also be reversed relative to the document - a return leg. "El Quseir to Marsa Alam"
    matches the same row; use that row's prices and simply swap departure and arrival.
  * If you cannot find an exact match, use the CLOSEST row and extract it. RETURNING EMPTY FIELDS IS
    NEVER CORRECT for a document that prices this route - a blank form tells the operator nothing about
    what went wrong, and they cannot correct a value that is not there. Extract your best reading and let
    the human fix it on the review screen.

Extract:
- service_name: the service/tier name exactly as the document states it, e.g. "Private Car Transfer", "Standard".
- departure_name, arrival_name: the departure and arrival point/area names, exactly as stated (e.g. "Mahe
  Island", "Hurghada Airport (HRG)", "South Bali (Tuban / Kuta / Legian / Seminyak / Sanur / Nusa Dua / Jimbaran / Tanjung Benoa)").
- is_zone_based: true if departure/arrival are named AREAS covering multiple localities (e.g. "South Bali
  (Tuban/Kuta/...)") rather than one specific point (a single named airport, hotel, or harbor) - this
  determines whether the location gets resolved against zone data or raw geocoding, so get it right.
- class_or_product_type: the tier/class label if the document distinguishes one, e.g. "Standard", "Superior", "Premium". Empty if not applicable.
- vehicle_hint: any vehicle type mentioned for this tier, e.g. "Car", "Mini-Van", "Coach", "Toyota Avanza". Empty if not stated.
- charge_unit: "per_pax" if the document charges per person (look for wording like "ChargeUnit-Pax", "per
  person", "net per person"), or "per_service" if it's a flat price for the whole vehicle/group regardless
  of headcount within the stated range (look for wording like "ChargeUnit-Service", "per vehicle", "flat rate").
- currency: the 3-letter currency code stated for this document/table.
- min_occupancy, max_occupancy: the smallest and largest passenger count this specific service/class covers.
- min_billable_pax: the SMALLEST number of passengers a per-person rate may be charged for, when the
  document states a minimum party size - e.g. "Private Transfer p.p. valid for (Min.2 pax) in Vehicle"
  means min_billable_pax = 2. CONFIRMED REAL RULE (product owner, same rule Transport already follows): a
  solo traveller must still be able to book, paying the two-person total - that becomes its own
  occupancy_price_tiers entry automatically, so do NOT invent a 1-pax row yourself here. Just report the
  minimum the document states. Use 1 (or leave it out) when the document states no minimum, and NEVER set
  it for a per-service/flat rate, where the price does not depend on headcount at all.
- occupancy_price_tiers: a list of {"occupancy": <integer>, "price": <number>, "child_price": <number or
  null>, "infant_price": <number or null>}. DRIVER-ONLY BASE RULE (CRITICAL): the price(s) here must be
  the DRIVER-ONLY rate (no guide included) - if the document ONLY gives guide-inclusive pricing with no
  separate driver-only rate, use the CHEAPEST/default guide-language table's prices here (this is a known
  approximation, note it in location_notes). Use ONE entry per occupancy count the document distinguishes
  with its own price - if the document gives a bracket/range (e.g. "3-5 paxes" at one price), use the
  bracket's LOWER bound as the occupancy number for that entry (e.g. "3-5" -> occupancy=3, "6-8" ->
  occupancy=6, "9-14" -> occupancy=9) rather than expanding every individual number - do not invent
  entries for occupancy counts the document doesn't actually price. For child_price/infant_price: if the
  document states an explicit number for that specific occupancy row, use it ("Free" = 0). If the column
  is marked "-", "N/A", or the pricing is flat/per-service (so child/infant categories genuinely don't
  apply to that row), leave child_price/infant_price as null - do NOT invent a number.
- child_infant_rule_text: if the document instead states a BLANKET child/infant rule that applies broadly
  rather than per-row numbers (e.g. "Children 2-12 traveling with parents receive 50% discount, under 2
  free of charge"), write that rule here in plain English. Leave empty if child/infant pricing is already
  fully captured in occupancy_price_tiers instead.
- additional_services: OPTIONAL/on-request extras only (child seats, booster seats, and similar) - list of
  {"name": "...", "price": <number>, "currency": "...", "max_quantity": <integer>, "on_request": true or false}.
  If the source describes the item as "on request"/"upon request"/"available on request", set
  "on_request": true AND also append "(on request)" to the "name" text itself (e.g. "Child Seat (on
  request)") - the Travel Compositor field this maps to has no separate on-request flag, so the wording
  must live in the name. If a price is stated per day/per unit-of-time (e.g. "$5/seat/service/day"), use
  that same flat number as a ONE-TIME charge (a transfer only lasts a few hours, never multiple days) -
  do NOT multiply it by a day count.
- guide_language_surcharges: for every OTHER guide language the document prices separately from the
  driver-only/base rate used above, one entry {"language": "...", "surcharge_estimate": <number>}. Compute
  the surcharge as that language's price MINUS the driver-only/base price for the SAME occupancy (prefer
  the lowest/solo occupancy tier for this comparison) - this is an approximation when the language's price
  difference actually varies by group size, note that in location_notes if it's noticeably inconsistent
  across occupancy tiers. Do not include the base/default language itself here.
- mandatory_supplements: ONLY genuinely mandatory, unconditional (never optional, never dependent on which
  specific pickup point within a zone was used) surcharges automatically applied to every booking on this
  route - most commonly a NIGHT SURCHARGE. Anything the client can choose (a child seat, a booster) is
  NOT a supplement - it belongs in additional_services. List of:
  {"name": "clear customer-facing label, e.g. 'Night Surcharge' - never put the price or the % in the name",
   "amount": <number>,
   "type": "PERCENT" or "ABSOLUTE",
   "start_time": "HH:MM" or "", "end_time": "HH:MM" or "",
   "start_date": "YYYY-MM-DD" or "", "end_date": "YYYY-MM-DD" or "",
   "notes": "..."}
  CRITICAL - PERCENTAGES ARE NEVER PRE-CALCULATED. If the source says "50% extra between 22:00 and
  08:00", output amount: 50 with type: "PERCENT" - do NOT work out what 50% of the fare is and put a
  currency figure there. Travel Compositor applies the percentage to the base price itself, so a
  pre-calculated figure would be right for one group size and wrong for every other one.
  TIME WINDOW: convert the source's wording to 24-hour HH:MM ("10pm to 8am" -> start_time "22:00",
  end_time "08:00"). A window that crosses midnight is normal and correct - write it exactly that way
  round, and never split it into two entries.  If the source states no hours at all, leave both empty.
  DATES: only fill start_date/end_date if the surcharge itself is restricted to a date range (e.g. a
  Christmas-only surcharge). A permanent night surcharge has NO dates of its own - leave both empty and
  it will inherit the transfer's own validity window.
  If a mandatory-sounding fee only applies to a subset of pickups within a broader zone/area (e.g. a
  harbor-only permit fee on a route that also serves airport pickups), do NOT put it here - put a
  plain-English note about it in location_notes instead, since this schema cannot apply a fee
  conditionally by location.
- location_notes: informational text about location-conditional costs that shouldn't be applied to the
  price directly (e.g. "Harbor pickup incurs an additional permit fee - not included in the price above
  since most clients depart from the airport"), plus any approximation caveats from the fields above.
  Empty string if nothing applies.
- description: 1-2 short plain-English sentences describing the transfer/route. Factual only, no invented details.
- pickup_information: any specific pickup logistics/instructions stated for this route. Empty if none.
- start_date, end_date: the REAL validity/season date range this document states for this rate (YYYY-MM-DD
  format). Different transfers/documents can have genuinely different validity windows - always use what
  THIS document actually states, never a fixed/hardcoded default. If the document gives no date range at
  all, leave both empty.
- cancellation_policy_tiers: whenever the document states its OWN specific cancellation-fee schedule -
  usually a tiered structure like "From 6 to 5 days Prior to Arrival: 10%, From 4 to 3 days: 50%, From 2 to
  0 days: 100%, No Show: 100%" - extract EVERY tier as {"days": <the LOWER bound of days-before-arrival for
  this tier>, "fee_percentage": <the cancellation FEE percentage for this tier, exactly as stated - a
  fee/charge percentage, NOT a refund percentage>}. Use the LOWER bound of each range as "days" (e.g. "6 to
  5 days" -> days=5, "4 to 3 days" -> days=3, "2 to 0 days" -> days=0). A separately-stated "No Show" fee
  does not need its own entry if it matches the final/most-expensive tier. If the document states NO
  specific cancellation terms (e.g. it only links out to general Terms & Conditions elsewhere, or says
  nothing at all), return an EMPTY list - do not invent one.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy as a
  short, clear, human-readable plain-text summary suitable for the customer-facing voucher, one line per
  tier. Leave empty whenever cancellation_policy_tiers is empty.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "service_name": "", "departure_name": "", "arrival_name": "", "is_zone_based": false,
  "class_or_product_type": "", "vehicle_hint": "", "charge_unit": "per_pax", "currency": "",
  "min_occupancy": 1, "max_occupancy": 4, "min_billable_pax": 1, "occupancy_price_tiers": [],
  "child_infant_rule_text": "", "additional_services": [], "guide_language_surcharges": [],
  "mandatory_supplements": [], "location_notes": "", "description": "", "pickup_information": "",
  "start_date": "", "end_date": "", "cancellation_policy_tiers": [], "cancellation_policy_text": ""
}"""


def extract_transfer_data(raw_text: str, model: str = "claude-sonnet-5", transfer_hint: str = None,
                           human_hint: str = None) -> dict:
    """Full extraction for one Transfer product (a single route+class combination)."""
    user_content = raw_text
    prefix_parts = []
    if transfer_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct transfer products. "
            f"Extract ONLY the following one, pulling in every guide-language price and any child/infant "
            f"or supplement detail that belongs to it specifically, and completely ignore every other "
            f"route/service described elsewhere in the text: {transfer_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    defaults = {
        "service_name": "", "departure_name": "", "arrival_name": "", "is_zone_based": False,
        "class_or_product_type": "", "vehicle_hint": "", "charge_unit": "per_pax", "currency": "EUR",
        "min_occupancy": 1, "max_occupancy": 4, "min_billable_pax": 1, "occupancy_price_tiers": [],
        "child_infant_rule_text": "", "additional_services": [], "guide_language_surcharges": [],
        "mandatory_supplements": [], "location_notes": "", "description": "", "pickup_information": "",
        "start_date": "", "end_date": "", "cancellation_policy_tiers": [], "cancellation_policy_text": "",
    }
    data = _call_claude(TRANSFER_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=8192,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    data["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(data.get("cancellation_policy_tiers"))

    return data


TRANSPORT_PRODUCT_DETECTION_PROMPT = """You are scanning a DMC supplier TRANSPORT rate sheet/tariff document to
identify every DISTINCT transport product it describes, so each can be reviewed and uploaded to Travel
Compositor as its own record.

TRANSPORT here means a connection between two named Travel Compositor destinations/locations (e.g. a private
car route between two towns, a car+ferry combined journey, a scheduled flight or train leg). These documents
are typically the same style/layout as Transfer rate sheets - very often they ARE a transfer rate sheet that
happens to also contain a few long-distance routes.

CRITICAL - LIST EVERY ROUTE. DO NOT DECIDE WHETHER SOMETHING IS "REALLY" A TRANSFER.
CONFIRMED REAL RULE (product owner): "a Transfer can also be a Transport." Whether a given route is sold
as a Transfer or as a Transport is a commercial decision the operator has already made before uploading
this document - it is not something to infer from the route's length, and the same route is legitimately
sold as both. A human ticks the ones they want on the next screen, so a route you leave out is simply
unavailable to them, while a route you include costs them one click to remove.

So: list EVERY distinct route-and-class combination the document prices. Never omit one on the grounds
that it looks like a short local hop, that the document is titled "Transfer", or that an airport is at one
end. Returning an empty list when the document plainly contains priced routes is the single worst outcome
here, because it leaves the operator with nothing to choose from and no way to tell why.

The only thing the local-vs-long-distance distinction is still used for is the "scope" field below, which
is advice to the human, not a filter you apply yourself.

A DISTINCT transport product is one specific ROUTE (a departure location to an arrival location) at one
specific SERVICE/CLASS/TIER (e.g. "Private Car", "Car + Ferry Combined", "Economy").

CRITICAL - AN AIRPORT STANDS FOR THE CITY IT SERVES. CONFIRMED REAL RULE (product owner): "if the
document says from airport (like RMF Airport) to Hurghada, this can also be a Transport from Marsa Alam
to Hurghada - airport to another city also means city to city." So read an airport as its city or resort
area, and then apply the local-vs-long-distance test to the CITIES, not to the words on the page:
  * "RMF Airport to Hurghada" = Marsa Alam to Hurghada = two different cities = TRANSPORT.
  * "HRG Airport to Luxor" = Hurghada to Luxor = TRANSPORT.
  * "HRG Airport to Sahl Hashish" = both within the Hurghada area = a local TRANSFER.
Name the CITY or resort area in departure_hint/arrival_hint (e.g. "Marsa Alam", not "RMF Airport"), and
keep the airport wording in the label if it helps a human recognise the row. Travel Compositor's transport
bases are named after places, not airport codes, so a city name is what actually resolves.

CRITICAL - GUIDE LANGUAGE / OPTIONAL EXTRAS ARE NEVER SEPARATE PRODUCTS: exactly as with Transfers, a
document may repeat the same routes once per guide language or per optional extra - this is NOT multiple
products. List each route+class combination only ONCE.

NOTE ON DIRECTION: unlike a Transfer, a Transport is directional - see the both-directions rule above. Do
NOT fold "A to B" and "B to A" into a single candidate.

For each distinct route+class product found, output a candidate with:
- label: short human-readable summary, e.g. "Private Car: Praslin <-> La Digue"
- service_name: exactly as the document names this service/tier
- departure_hint: the CITY or resort-area name for the departure (see the airport rule above)
- arrival_hint: the CITY or resort-area name for the arrival
- scope: "long_distance" when the two ends are different cities/regions a traveller would call separate
  destinations, or "local" when both ends are within one area (an airport and the hotels it serves). This
  is a LABEL to help the human filter, never a reason to leave a route out.

MATCHING THE OPERATOR'S INSTRUCTION TO THE DOCUMENT'S WORDING. CONFIRMED REAL FAILURE (product owner):
the instruction said "focus on Marsa Alam to Hurghada" and the document's row reads "RMF Airport | Hurghada",
so nothing matched and nothing was returned. An instruction names PLACES; a rate sheet often names AIRPORTS.
Resolve each airport to the city it serves before matching, in both directions:
  * "RMF Airport" is Marsa Alam, "HRG Airport" is Hurghada, "SSH Airport" is Sharm El Sheikh, "CAI Airport"
    is Cairo - and in general "<city> Airport" is that city.
  * So "Marsa Alam to Hurghada" MATCHES the row "RMF Airport -> Hurghada". Return that row.
  * The section heading also tells you where a block of rows departs from: rows under "Transfer Fees Marsa
    Allam" are Marsa Alam departures even where the cell says only an airport code.
Never return an empty list because the instruction's wording differs from the document's. If you are unsure
whether a row is the one meant, INCLUDE it - the human unticks what they did not want, but cannot ever tick
something you left out.

BOTH DIRECTIONS ARE SEPARATE PRODUCTS FOR TRANSPORT. CONFIRMED REAL RULE (product owner): "this line stands
always viceversa option too." A Travel Compositor transport is stored as departure -> arrival, so selling a
route both ways needs TWO records. For every route you list, emit a second candidate with departure and
arrival swapped, unless the document explicitly prices only one direction. Same price for both unless the
document says otherwise.

HUMAN INSTRUCTION OVERRIDES EVERYTHING ABOVE. If an instruction from the operator is given, it decides
what to list and the rules above are only a fallback for whatever the instruction does not cover. "Only
the Hurghada routes" means list every route in the Hurghada section and nothing from the other sections.
"Only the private ones" means ignore the shuttle/seat-in-coach column entirely. "All of them, including
the local ones" means exactly that - list them all, even the ones you would otherwise judge to be local
transfers. The operator can see the document and knows how these products are being sold; do not
second-guess an explicit instruction.

Output ONLY valid JSON, no markdown fences, no explanation. Use this exact structure:
{
  "multiple_transports": true or false,
  "transports": [
    {"label": "...", "service_name": "...", "departure_hint": "...", "arrival_hint": "...", "scope": "long_distance"}
  ]
}
ALWAYS LIST WHAT YOU FOUND, INCLUDING WHEN THERE IS ONLY ONE.
An earlier version of this prompt ended by telling you to return an EMPTY list when the document held only
one product. That instruction caused a real, repeated production failure: on a rate sheet pricing forty
routes the answer came back empty, the operator saw a blank screen with no explanation, and there was no
way to tell the difference between "found nothing" and "chose to say nothing". It is withdrawn.

  * One product found  -> "multiple_transports": false, and "transports" containing that ONE product.
  * Several found      -> "multiple_transports": true, and every one of them.
  * The document prices no routes at all (it is an invoice, a contract, a covering letter)
                       -> "multiple_transports": false and an empty "transports". This is the ONLY case where the list
                          may be empty.

The boolean is a summary of the list, never a substitute for it. Never leave the list empty for a document
that visibly prices routes."""


def detect_transport_products(raw_text: str, model: str = "claude-sonnet-5",
                              human_hint: str = None) -> list:
    """
    Checks whether the source describes MULTIPLE distinct transport products (route + service/
    class combinations) as opposed to a single one. Mirrors detect_transfer_products' contract
    for the existing batch/queue review UI pattern. Returns an empty list if only one is found,
    or a list of {"label", "service_name", "departure_hint", "arrival_hint"} dicts.
    """
    print("🔎 Checking for multiple distinct transport products (routes/classes) in this document...")
    transports = _detect_items(TRANSPORT_PRODUCT_DETECTION_PROMPT,
                               _with_hint(raw_text, human_hint), model,
                               "multiple_transports", "transports",
                               _directional_route_identity)
    if transports:
        print(f"⚠️ Detected {len(transports)} distinct transport product(s): {[t.get('label') for t in transports]}")
    else:
        print("✅ Only one distinct transport product detected.")
    return transports


TRANSPORT_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data for a Travel Compositor TRANSPORT

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
(a connection between two named destinations/locations - e.g. a private car route, a car+ferry combined
journey, a scheduled flight or train leg between two towns/cities/islands - NOT a local airport/hotel
transfer) from a DMC supplier rate sheet. Translate ALL content to English regardless of source language.

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly. Momira Travel is the tour operator the client actually deals with - the client must NEVER be told
to contact the DMC/supplier directly. Silently drop/omit any such text wherever it could appear.

If a human hint above names a specific route/service, extract ONLY that one product and ignore every other
route/service described elsewhere in the document.

FINDING THE HINTED ROUTE IN THE DOCUMENT. CONFIRMED REAL FAILURE (product owner): the hint read
"Private Transfer - Marsa Alam to El Quseir" while the document's row reads "RMF Airport | El Quseir |
20 | 30", and every extracted field came back empty. The hint names PLACES; a rate sheet often names
AIRPORTS or abbreviations, and a section heading may be the only place the departure city is written.
  * Resolve airports to their cities in BOTH directions before matching: "RMF Airport" is Marsa Alam,
    "HRG Airport" is Hurghada, "SSH Airport" is Sharm El Sheikh, "CAI Airport" is Cairo, and in general
    "<city> Airport" is that city. So the hint "Marsa Alam to El Quseir" MATCHES the row
    "RMF Airport | El Quseir".
  * Rows under a heading like "Transfer Fees Marsa Allam" depart from Marsa Alam even where the cell
    shows only an airport code.
  * The hint may also be reversed relative to the document - a return leg. "El Quseir to Marsa Alam"
    matches the same row; use that row's prices and simply swap departure and arrival.
  * If you cannot find an exact match, use the CLOSEST row and extract it. RETURNING EMPTY FIELDS IS
    NEVER CORRECT for a document that prices this route - a blank form tells the operator nothing about
    what went wrong, and they cannot correct a value that is not there. Extract your best reading and let
    the human fix it on the review screen.

Extract:
- service_name: the service/tier name exactly as the document states it, e.g. "Private Car", "Car + Ferry Combined".
AN AIRPORT STANDS FOR THE CITY IT SERVES (confirmed real rule, product owner): a row reading
"RMF Airport to Hurghada" is the city-to-city connection Marsa Alam to Hurghada. Put the CITY or resort-area
name in departure_name/arrival_name ("Marsa Alam", "Hurghada"), because Travel Compositor's transport bases
are named after places and an airport code resolves to nothing. If the airport itself matters to a human
reading the voucher, say so in the description rather than in the location names.

- departure_name, arrival_name: the departure and arrival location names, exactly as stated (e.g. "Praslin", "La Digue").
- transport_type_hint: any transport mode mentioned, e.g. "Car", "Car and Public Ferry", "Flight", "Train", "Coach". Empty if not stated.
- vehicle_model: a specific vehicle/aircraft/train model if genuinely stated (e.g. "Mercedes Vito", "Toyota Avanza"). Empty if not stated - do NOT invent one.
- service_number: a specific flight/train/service number if genuinely stated (e.g. "LH123"). Empty if not stated.
- charge_unit: "per_pax" if the document charges per person (look for wording like "per person", "net per
  person", "ChargeUnit-Pax"), or "per_service" if it's a flat price for the whole vehicle/group regardless of
  headcount within the stated range (look for wording like "per vehicle", "flat rate", "ChargeUnit-Service").
- currency: the 3-letter currency code stated for this document/table.
- departure_time: the scheduled departure clock time if the document states one ("HH:MM:SS" 24-hour).
  If it does not, use "09:00:00" as a placeholder - a human picks the real one on the review screen.
- arrival_time: LEAVE THIS EMPTY. It is calculated from departure_time plus duration_time, so anything
  you put here is discarded. Do not compute it yourself.
- duration_time: ALWAYS provide this - how long the journey actually takes door to door, as "HH:MM:SS".
  CONFIRMED REAL RULE (product owner): "we must add in Transport a Duration Time... the human shall in
  best case only select Departure time." If the document states a duration, use it exactly. If it does
  NOT - which is the usual case on a rate sheet - ESTIMATE it from your own knowledge of the real journey
  between those two specific places, by the mode the document implies (private car, coach, ferry, train,
  flight). Be realistic rather than optimistic: use the time a driver would actually take including the
  usual stops, not a theoretical best case. Worked examples for scale: Hurghada to Luxor is about 4h,
  Hurghada to Cairo about 6h, Marsa Alam to Hurghada about 3h, Sharm El Sheikh to Dahab about 1h30.
  A duration of "00:00:00" is never correct for a journey between two different places.
- duration_estimated: true when you estimated duration_time from knowledge rather than reading it in the
  document, false when the document stated it. This is shown to the human so they know which number to
  check, so do not mark an estimate as stated.
- plus_days: 0 unless the document explicitly states the arrival is a later calendar day than departure
  (e.g. an overnight ferry/train) - then the number of days later.
- min_billable_pax: the SMALLEST number of passengers a per-person rate may be charged for, when the
  document states a minimum party size - e.g. "Private Transfer p.p. valid for (Min.2 pax) in Vehicle"
  means min_billable_pax = 2. CONFIRMED REAL RULE (product owner): a solo traveller must still be able
  to book, paying the two-person total - that becomes its own occupancy bracket automatically, so do NOT
  invent a 1-pax row yourself. Just report the minimum the document states. Use 1 (or leave it out) when
  the document states no minimum, and NEVER set it for a per-vehicle/flat rate, where the price does not
  depend on headcount at all.
- occupancy_brackets: a list of {"min_occupancy": <integer>, "max_occupancy": <integer>, "price": <number>,
  "child_price": <number or null>, "infant_price": <number or null>}. Each entry is the ACTUAL FINAL price
  a party of that size pays - as literally stated by the document (e.g. "1-2 Pax: EUR 102 per person" ->
  {"min_occupancy": 1, "max_occupancy": 2, "price": 102}). DRIVER-ONLY BASE RULE (same as Transfer): use the
  driver-only rate (no guide) - if the document ONLY gives guide-inclusive pricing, use the cheapest/default
  guide-language table's prices and note the approximation in additional_notes. If the document gives one
  bracket per group-size range, create one entry per bracket exactly as ranged (e.g. "3-4 Pax", "5-6 Pax") -
  do NOT invent brackets the document doesn't state, and do NOT assume any mathematical relationship between
  different brackets' prices (real data has shown non-monotonic/alternating patterns - always use the
  document's own literal number for each bracket, never interpolate or derive one from another). If the
  document instead only describes ONE vehicle with a flat price up to some capacity (e.g. "Private car, max
  4 pax: EUR 100"), use ONE entry: {"min_occupancy": 1, "max_occupancy": 4, "price": 100} - larger groups are
  handled automatically downstream as needing multiple vehicles, do not try to compute that yourself. For
  child_price/infant_price: use an explicit number only if the document states one for that row ("Free" = 0);
  leave null if the column is "-"/"N/A"/not applicable to that pricing style - do NOT invent a number.
- child_infant_rule_text: if the document instead states a BLANKET child/infant rule rather than per-row
  numbers, write that rule here in plain English. Leave empty if already fully captured in occupancy_brackets.
- additional_notes: informational text for anything the document mentions that doesn't fit a structured field
  above - e.g. a guide-language surcharge, a child-seat fee, a location-conditional cost - since Transport
  (unlike Transfer/ClosedTour/Ticket) has NO additionalServices/supplements field at all to hold priced
  extras. Summarize plainly (e.g. "German-speaking guide available for an additional EUR 15" or "Harbor
  pickup incurs an extra permit fee, not included above") - NEVER silently invent a price adjustment for
  these into occupancy_brackets. Empty string if nothing applies.
- description: 1-2 short plain-English sentences describing the transport/route, covering every leg if it's
  a multi-leg/combined journey (e.g. car to harbor, ferry, car to hotel) even though only one overall segment
  gets recorded structurally. Factual only, no invented details.
- company_name: the descriptive service name as the document states it, if different/more detailed than
  service_name (e.g. "Car with Driver (Hotel to Hotel)", "Hotel-to-Hotel Land & Ferry Transfer"). Empty if
  service_name already covers it.
- start_date, end_date: the REAL validity/season date range this document states for this rate (YYYY-MM-DD).
  Always use what THIS document actually states - a title like "from 1st August 2026 till 31st July 2027"
  IS the range and must be used. CONFIRMED REAL RULE (product owner): "start date of the Transfer,
  Transport, Ticket can always be the day on the document, and if not stated, it is today." So when the
  document states no start date, leave start_date EMPTY and the tool fills in today - do not invent one.
  Leave end_date empty when the document states no end.
- cancellation_policy_tiers: whenever the document states its OWN specific cancellation-fee schedule, extract
  EVERY tier as {"days": <the LOWER bound of days-before-arrival for this tier>, "fee_percentage": <the
  cancellation FEE percentage, exactly as stated - a fee/charge percentage, NOT a refund percentage>}. Use
  the LOWER bound of each range as "days" (e.g. "6 to 5 days" -> days=5, "2 to 0 days" -> days=0). A
  separately-stated "No Show" fee does not need its own entry if it matches the final/most-expensive tier. If
  the document states NO specific cancellation terms, return an EMPTY list - do not invent one.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy as a short,
  clear, human-readable plain-text summary, one line per tier. Leave empty whenever cancellation_policy_tiers
  is empty.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "service_name": "", "departure_name": "", "arrival_name": "", "transport_type_hint": "",
  "vehicle_model": "", "service_number": "", "charge_unit": "per_pax", "currency": "",
  "departure_time": "09:00:00", "arrival_time": "", "plus_days": 0, "duration_time": "04:00:00",
  "duration_estimated": true,
  "min_billable_pax": 1,
  "occupancy_brackets": [], "child_infant_rule_text": "", "additional_notes": "",
  "description": "", "company_name": "", "start_date": "", "end_date": "",
  "cancellation_policy_tiers": [], "cancellation_policy_text": ""
}"""


def extract_transport_data(raw_text: str, model: str = "claude-sonnet-5", transport_hint: str = None,
                            human_hint: str = None) -> dict:
    """Full extraction for one Transport product (a single route+class combination)."""
    user_content = raw_text
    prefix_parts = []
    if transport_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct transport products. "
            f"Extract ONLY the following one, pulling in every bracket/price detail that belongs to it "
            f"specifically, and completely ignore every other route/service described elsewhere in the "
            f"text: {transport_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    defaults = {
        "service_name": "", "departure_name": "", "arrival_name": "", "transport_type_hint": "",
        "vehicle_model": "", "service_number": "", "charge_unit": "per_pax", "currency": "EUR",
        "departure_time": "09:00:00", "arrival_time": "", "plus_days": 0, "duration_time": "",
        "duration_estimated": False, "min_billable_pax": 1,
        "occupancy_brackets": [], "child_infant_rule_text": "", "additional_notes": "",
        "description": "", "company_name": "", "start_date": "", "end_date": "",
        "cancellation_policy_tiers": [], "cancellation_policy_text": "",
    }
    data = _call_claude(TRANSPORT_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=8192,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    data["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(data.get("cancellation_policy_tiers"))

    # Fold additional_notes (guide-language surcharges, location-conditional costs, etc - there
    # is no structured field on Transport to hold these, unlike Transfer/ClosedTour/Ticket) into
    # the description, same "must always reach customer/staff-facing text somewhere" principle
    # already applied to cancellation text (see builder.py's build_transport_payloads).
    if data.get("additional_notes"):
        data["description"] = f"{data['description']}\n\n{data['additional_notes']}".strip() if data.get("description") else data["additional_notes"]

    return data


# ==========================================
# HOTEL EXTRACTION
# Confirmed against the real Contract Hotel Swagger + 2 real GET pulls for a
# live hotel (CAI-H1, Four Seasons Hotel Cairo at Nile Plaza, supplier
# 48940) - see schemas.py's HOTEL SCHEMAS section and builder.py's HOTEL
# BUILDER section for every confirmed business rule and flagged assumption
# this prompt is written to satisfy.
# ==========================================

HOTEL_EXTRACTION_SYSTEM_PROMPT = """You are extracting structured data for a Travel Compositor HOTEL contract

HOW TABLES ARE WRITTEN IN THE TEXT YOU ARE GIVEN - READ THIS BEFORE ANY RATE TABLE:
Tables arrive as a grid with explicit column positions, because a rate sheet's meaning lives in
which price sits under which heading. The notation is:
  "COLUMNS: C1 | C2 | ..."      a ruler naming every column position in this table
  "R3:"                          the row number
  "High «spans C4-C5»"           this cell covers columns 4 AND 5 - a merged heading
  "$847 «spans C4-C5»"           this value belongs to columns 4 and 5, i.e. under "High"
  "24/9/2026 «C2»"               a single cell in column 2
  "·"                            a genuinely empty cell
  "NOTE: this table has NO header row of its own..."  the table is a CONTINUATION of the one
                                 immediately above it, and its columns line up with that table's
                                 headers one for one. Use the table above to know what each
                                 column means. This is common: Word often stores one visual
                                 table as two, with all the headings in the first and all the
                                 numbers in the second.
"BY COLUMN (the same table read downwards...)"   PREFER THIS. It is the whole table already
                               resolved for you: each line gives one column's full path from the
                               top heading down through the sub-headings, its dates, and every
                               value in it labelled with the row it came from, e.g.
                                 C2 = Season 2026 / 2027 > Normal > From > 24/9/2026 > 7/1/2027
                                      > 5/4/2027 > Single Luxury Cabin: $565 > Per person in
                                        Double Luxury Cabin: $353 > ...
                               Read that and the season, its date ranges and each cabin's price
                               are already together on one line. Use the R-rows above only to
                               check something that looks wrong.
TO READ A SEASON GRID: use the BY COLUMN list. If you must work from the rows instead, first
work out which COLUMN RANGE each season occupies (e.g. Normal = C2-C3, High = C4-C5), then take
each price from the cell covering that same range. Never match a price to a season by counting
values left to right - merged cells make the count wrong.
STACKED DATE RANGES ARE SEPARATE PERIODS, NOT A TYPO: a season often has SEVERAL From/To pairs
listed on consecutive rows under the same heading (e.g. Normal running 24/9/2026-23/12/2026,
then 7/1/2027-24/3/2027, then 5/4/2027-5/5/2027). Every one of those is its own entry in
price_list, all carrying that season's SAME prices. Do not merge them into one long range - the
gaps between them are other seasons, and merging would sell the high season at the normal rate.
Do not drop the later ones either; missing periods are the most common failure on these sheets.

READING DATES IN THE SOURCE - HOUSE RULE, applies to this whole document:
A numeric date written with slashes, dots or dashes is DAY FIRST. "03/04/2026" is 3 April 2026,
never 4 March. Momira and its suppliers are European and Middle Eastern and write dates that way,
and the two readings differ by a month with nothing to show for it - a season boundary quietly
moved, a rate applied to the wrong weeks. If a date is genuinely ambiguous AND day-first gives an
impossible result (e.g. "13/25/2026"), say so in the notes field rather than picking silently.
ALWAYS OUTPUT YYYY-MM-DD. That is the format Travel Compositor's API accepts and the format every
date field below expects; the app converts it back to DD/MM/YYYY for the human to read. Do not
output DD/MM/YYYY yourself, whatever the source used.
from a DMC supplier rate sheet/tariff document. Translate ALL content to English regardless of source language.

CRITICAL - NEVER include any instruction telling the CUSTOMER to contact the operator/supplier/provider
directly. Momira Travel is the tour operator the client actually deals with - the client must NEVER be told
to contact the DMC/supplier directly. Silently drop/omit any such text wherever it could appear.

A hotel contract document typically describes: the property itself, one or more ROOM TYPES (each with which
adult+children occupancy combinations are allowed), MEAL PLANS (room-only, breakfast, half board, etc, each
usually a per-night add-on cost), optional OFFERS (discounts) and SUPPLEMENTS (extra charges), and one or more
RATE groups, each containing SEASONS (date ranges) with per-room-type pricing for that season, plus any
STOP SALES (blackout dates for a specific room).

=== HOTEL-LEVEL FIELDS ===
- hotelname: the property's name exactly as stated.
- category: star rating or category exactly as stated (e.g. "5 STARS", "4*"). Empty if not stated.
- chain: the hotel chain/brand name if stated (e.g. "Four Seasons"). Empty if not stated.
- address: {"address": street address, "location_name": city/area, "postal_code": "", "country": "" (2-letter
  ISO code if determinable, else the country name as stated), "phone": "", "fax": "", "email": ""}. Leave any
  sub-field empty if not stated - do not invent contact details.
- latitude, longitude: only if the document genuinely states coordinates. null otherwise - do NOT estimate.
- description: 2-4 factual plain-English sentences describing the property, drawn only from what the document
  actually says.
- images: list of any image URLs the document/source explicitly provides. Empty list if none.
- infants_allowed: the maximum number of infants allowed per booking/room, if the document states a capacity
  number. If not stated, use 2 as a reasonable default (a human reviews this before publish).
- min_children_age, max_children_age: CONFIRMED this API only supports ONE combined age range covering both
  infants and children together (not two separate bands). If the document gives a single explicit children age
  range, use it. If it gives no explicit range, default to 0 and 12.
- minimum_stay, maximum_stay, release_days: hotel-level defaults for these, only if the document states hotel-
  wide values distinct from what's stated per-season below (per-season values take precedence there). Leave
  null if not stated at this level.
- cancellation_policy_tiers: whenever the document states its OWN specific cancellation-fee schedule, extract
  EVERY tier as {"days": <the LOWER bound of days-before-arrival for this tier>, "fee_percentage": <the
  cancellation FEE percentage, exactly as stated - a fee/charge percentage, NOT a refund percentage>}. If the
  document states NO specific cancellation terms, return an EMPTY list - do not invent one.
- cancellation_policy_text: if cancellation_policy_tiers is non-empty, ALSO write the same policy as a short,
  clear, human-readable plain-text summary. Leave empty whenever cancellation_policy_tiers is empty. NOTE:
  Hotel has NO separate structured cancellation field on the record itself - this text is what customer/staff
  ultimately see, so capture the real policy carefully.

=== ROOMS ===
"rooms": one entry per distinct room type (e.g. "Superior Room", "Premium Superior Room", "Deluxe Sea View"):
  {"name": "", "type_id": null, "distributions": [{"adults": <int>=1, "children": <int>=0}, ...]}
distributions = every ALLOWED adult+children occupancy combination for that room, exactly as the document's own
occupancy table/grid states (e.g. a table with columns "1 Adult", "2 Adults", "2 Adults + 1 Child" becomes
three distribution entries: {"adults":1,"children":0}, {"adults":2,"children":0}, {"adults":2,"children":1}).
Do NOT invent combinations the document doesn't show pricing/availability for. type_id: always null - there is
no known master-list reference for this field, never invent one.

=== MEAL PLANS ===
"meal_plans": one entry per DISTINCT meal plan the document prices as an add-on (do NOT create a "Room Only"
entry yourself - that is always added automatically downstream at 0 cost):
  {"meal_plan_hint": "" (the plan name as the document states it, e.g. "Breakfast", "Half Board", "All
   Inclusive" - will be mapped onto Travel Compositor's fixed 5-value list downstream),
   "base_price": <the cost for the 1st adult>,
   "adult_prices": [<cost for each ADDITIONAL adult beyond the first, in order - 2nd adult, 3rd adult, ...>],
   "child_prices": [<cost for each child, in order - 1st child, 2nd child, ...>]}
Only extract a meal plan the document actually prices as an addition to the room rate - if the document's room
rates already include a meal plan (e.g. "rate is All Inclusive"), still create one entry for it but with
base_price/adult_prices/child_prices all 0 (already included, no extra add-on cost), and note this in the
top-level description so a human reviewer understands the room rate already includes it.

=== OFFERS (discounts) and SUPPLEMENTS (extra charges) ===
Both use the same shape (supplements never use type="STAY_TO_PAY" or stay/pay - those are offer-only):
  {"name": "" (short human label, e.g. "10% Discount when staying 3+ nights", "Resort Fee"),
   CONFIRMED PRODUCT-OWNER RULE - FOR SUPPLEMENTS ONLY: the "name" the client sees must NEVER contain a
   date, a night count, or wording like "per night"/"per stay"/"per person per night". Travel Compositor
   only ever displays the supplement's single total charge to the client, never a per-night breakdown, so a
   name saying "per night" next to a total price reads as wrong or confusing even when the underlying math
   is correct. The charging basis already lives structurally in "apply" and the date restriction already
   lives in "travel_windows" - do not restate either in the name. Write "Resort Fee", not "Resort Fee (USD
   11 per night)" or "Resort Fee - valid 1 Dec to 31 Jan". This restriction is supplements only; an OFFER's
   name may still describe its condition (e.g. "10% Discount when staying 3+ nights") since an offer's
   savings are inherently tied to how long the client books.
   "type": "PERCENT" | "ABSOLUTE" | "STAY_TO_PAY" (offers only),
   "apply": one of "LODGING" | "MEAL" | "LODGING_AND_MEAL" | "PER_NIGHT" | "PER_NIGHT_PERSON" | "PER_STAY" |
     "PER_STAY_PERSON" - pick the single value the document ACTUALLY states (e.g. a fee explicitly worded
     "per person per night" -> "PER_NIGHT_PERSON"; a straightforward room-rate percentage discount ->
     "LODGING").
     CRITICAL - FOR SUPPLEMENTS ONLY: if the document does not make the basis explicit, output "" (empty
     string). Do NOT fall back to a sensible-looking value. "Per person" on its own is genuinely ambiguous
     - it can mean once per person for the whole stay or once per person per night, and those differ by the
     entire length of the stay - so a human picks it. An empty string here is the correct, expected answer
     whenever the document is vague, and it costs nothing; a guess costs the client money on every booking.
     Offers are different: for an OFFER, pick your best match rather than leaving it empty.
   "value": <percentage or absolute amount, matching "type">, "child_value": <same, for children, if stated>,
   "stay": <int, only for STAY_TO_PAY, e.g. 7 for "stay 7">, "pay": <int, only for STAY_TO_PAY, e.g. 6 for "pay 6">,
   "release_days": null, "minimum_stay": null, "maximum_stay": null,
   "minimum_adults": null, "maximum_adults": null, "minimum_childrens": null, "maximum_childrens": null,
   "travel_windows": [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}] (the stay-date window this applies to),
   "booking_windows": [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}] (the window during which this must be BOOKED, if the document states a booking deadline separate from the stay window),
   "room_names": [] (which room type name(s), from the "rooms" list above, this applies to - empty if it applies to all rooms),
   "meal_plans": [], "operational_days": []}
Only fill numeric constraint fields (minimum_stay, minimum_adults, etc) when the document genuinely states that
constraint for this specific offer/supplement - leave null otherwise, never invent a constraint.
IMPORTANT - COMBINABLE OFFERS: if a document describes an offer combining MULTIPLE conditions (e.g. "stay 3
pay 2, valid for stays Oct 1-19, must book within 2 weeks of stay"), extract that as ONE offer entry using
type="STAY_TO_PAY", stay=3, pay=2, travel_windows=[{Oct 1-19 dates}], booking_windows=[the 2-week booking
deadline, computed relative to the travel window if the document gives it as a relative rule].

=== RATES, SEASONS, and ROOM PRICES ===
"rates": Travel Compositor groups pricing under named "rate" containers (e.g. "Standard Rates", "Peak Season
Contract") - if the document doesn't explicitly name separate rate groups, use ONE rate entry named after the
hotel/contract itself:
  {"name": "", "minimum_stay": 1, "maximum_stay": null, "release_days": null,
   "booking_windows": [], "offer_names": [] (names from "offers" above that apply to this rate),
   "supplement_names": [] (names from "supplements" above that apply to this rate),
   "seasons": [...], "stop_sales": [...]}

"seasons" (within a rate): one entry per date-range/pricing period the document defines:
  {"name": "" (the document's own season label, e.g. "Summer 2026", "Low Season" - invent a short descriptive
   name like "Season 1" only if the document genuinely gives none),
   "date_ranges": [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}] (can be multiple non-contiguous ranges if the
   document groups them under one price table),
   "price_type": "DISTRIBUTION" (the normal case - one flat price per adult+children occupancy combination, as
   in a typical rate grid) or "PAX" (only if the document genuinely prices per-person with a base rate plus
   incremental extra-person charges, not a full occupancy grid),
   "minimum_stay": 1, "maximum_stay": null, "release_days": null,
   "meal_plans": [] (only if THIS season's meal-plan prices differ from the hotel-level ones above - same
   shape as top-level meal_plans; leave empty to just use the hotel-level ones),
   "room_prices": [...]}

"room_prices" (within a season): one entry per room type priced in this season:
  {"room_name": "" (must match a name from "rooms" above),
   "units_quota": <int> (how many rooms are allotted - if the document doesn't state a number, use 20),
   "units_on_request": <int> (how many additional rooms are available on-request only - if not stated, use 0),
   "distribution_prices": [{"adults": <int>, "children": <int>, "amount": <the ACTUAL FINAL price for exactly
     this adults+children combination, exactly as the document states it>}, ...],
   "base_price": 0.0, "adult_prices": [], "child_prices": []}
CRITICAL: extract distribution_prices LITERALLY, one entry per adults+children combination the document
actually prices - NEVER assume a formula or fixed increment between different occupancy combinations (real
contracts have shown non-monotonic, irregular differences between brackets). Only use base_price/adult_prices/
child_prices instead of distribution_prices when price_type is genuinely "PAX" for that season.

=== STOP SALES ===
"stop_sales" (within a rate): blackout date ranges for a specific room, if the document mentions any:
  {"room_name": "" (must match a name from "rooms" above), "date_ranges": [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}]}
Leave this list empty if the document states no blackout/stop-sale dates - do not invent any.

Respond with ONLY valid JSON (no markdown fences, no preamble), exactly this shape:
{
  "hotelname": "", "category": "", "chain": "",
  "address": {"address": "", "location_name": "", "postal_code": "", "country": "", "phone": "", "fax": "", "email": ""},
  "latitude": null, "longitude": null, "description": "", "images": [],
  "infants_allowed": 2, "min_children_age": 0, "max_children_age": 12,
  "minimum_stay": null, "maximum_stay": null, "release_days": null,
  "cancellation_policy_tiers": [], "cancellation_policy_text": "",
  "rooms": [{"name": "", "type_id": null, "distributions": []}],
  "meal_plans": [{"meal_plan_hint": "", "base_price": 0.0, "adult_prices": [], "child_prices": []}],
  "offers": [],
  "supplements": [],
  "rates": [{"name": "", "minimum_stay": 1, "maximum_stay": null, "release_days": null,
             "booking_windows": [], "offer_names": [], "supplement_names": [],
             "seasons": [{"name": "", "date_ranges": [], "price_type": "DISTRIBUTION",
                          "minimum_stay": 1, "maximum_stay": null, "release_days": null, "meal_plans": [],
                          "room_prices": [{"room_name": "", "units_quota": 20, "units_on_request": 0,
                                           "distribution_prices": [], "base_price": 0.0, "adult_prices": [], "child_prices": []}]}],
             "stop_sales": []}]
}"""


def detect_hotel_products(raw_text: str, model: str = "claude-sonnet-5") -> list:
    """
    Checks whether the source describes MULTIPLE distinct hotel properties (rather than the far
    more common case of one document = one hotel) - e.g. a DMC's combined rate sheet covering
    several properties. Mirrors detect_transport_products'/detect_transfer_products' contract for
    the existing batch/queue review UI pattern. Returns an empty list if only one hotel is found,
    or a list of {"label", "hotelname_hint"} dicts.
    """
    prompt = """You are scanning a DMC supplier document to identify whether it describes MULTIPLE DISTINCT
HOTEL PROPERTIES (not multiple room types or rate seasons within ONE hotel - those all belong to the same
hotel record) - e.g. a combined rate sheet covering several different hotels.

Output ONLY valid JSON, no markdown fences, no explanation:
{"multiple_hotels": true or false, "hotels": [{"label": "...", "hotelname_hint": "..."}]}
If there is genuinely only one hotel property described in the whole document (the overwhelmingly common
case - most documents describe just one property's rooms/rates/offers), set "multiple_hotels": false and
"hotels": [] ."""
    print("🔎 Checking for multiple distinct hotel properties in this document...")
    hotels = _detect_items(prompt, raw_text, model, "multiple_hotels", "hotels",
                           lambda h: " ".join(str(h.get("hotelname_hint") or h.get("label") or "").split()).lower())
    if hotels:
        print(f"⚠️ Detected {len(hotels)} distinct hotel propert(ies): {[h.get('label') for h in hotels]}")
    else:
        print("✅ Only one hotel property detected.")
    return hotels


def extract_hotel_data(raw_text: str, model: str = "claude-sonnet-5", hotel_hint: str = None,
                        human_hint: str = None) -> dict:
    """Full extraction for one Hotel contract - hotel fields, rooms, meal plans, offers,
    supplements, and rates (with nested seasons/room_prices/stop_sales). See
    HOTEL_EXTRACTION_SYSTEM_PROMPT for the full field-by-field extraction contract, and
    builder.py's build_hotel_contract_payload/build_hotel_offer_payloads/
    build_hotel_supplement_payloads/build_hotel_rate_payloads for how this shape is consumed."""
    user_content = raw_text
    prefix_parts = []
    if hotel_hint:
        prefix_parts.append(
            f"IMPORTANT: This document describes MULTIPLE distinct hotel properties. "
            f"Extract ONLY the following one, pulling in every room/rate/offer/supplement detail that belongs "
            f"to it specifically, and completely ignore every other property described elsewhere in the "
            f"text: {hotel_hint}"
        )
    if human_hint:
        prefix_parts.append(f"IMPORTANT - human guidance for this extraction: {human_hint}")
    if prefix_parts:
        user_content = "\n\n".join(prefix_parts) + f"\n\n--- Source content ---\n{raw_text}"

    defaults = {
        "hotelname": "", "category": "", "chain": "",
        "address": {}, "latitude": None, "longitude": None, "description": "", "images": [],
        "infants_allowed": 2, "min_children_age": 0, "max_children_age": 12,
        "minimum_stay": None, "maximum_stay": None, "release_days": None,
        "cancellation_policy_tiers": [], "cancellation_policy_text": "",
        "rooms": [], "meal_plans": [], "offers": [], "supplements": [], "rates": [],
    }
    data = _call_claude(HOTEL_EXTRACTION_SYSTEM_PROMPT, user_content, model, max_tokens=8192,
                       input_schema=_required_keys_schema(defaults))

    for key, default in defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    data["cancellation_policy_tiers"] = _sanitize_cancellation_tiers(data.get("cancellation_policy_tiers"))

    return data
