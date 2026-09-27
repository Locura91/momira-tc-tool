# ClosedTour multi-factor pricing: design discussion (2026-09-27)

Investigation + design questions only — no code changed yet. Captured here so the eventual build
has the full context in one place rather than scattered across a long conversation.

## Trigger documents

Two real contracts drove this: a Bush Camp Safari (Thailand) ClosedTour with compound season
labels and a hidden peak-season pricing gap (this same conversation's Bush Camp season-date
discussion, not yet its own doc), and **Our Jungle Resorts** (Khao Sok, Thailand) — a resort
contract requiring Room + mandatory Package (meals & tours) + National Park Fees + Transfers to
all be reconciled into one ClosedTour Modality price. The same shape of problem was already known
from Nile cruises (per-night cabin rates) — see `per_night_occupancy_prices` in `builder.py` and
the "PER-NIGHT PRICING" house rule in `ai_extractor.py` (~line 548), which already correctly
handles a SINGLE per-night rate becoming a Modality price. The gap is combining MULTIPLE
independently-priced components into one total.

## Confirmed decisions

1. **Product type**: a resort like Our Jungle Resorts (room + mandatory package during High
   Season) is built as a **ClosedTour**, not a Hotel — Hotel's per-night-per-room model with a
   fixed 5-tier meal-plan enum (`ROOM_ONLY`/`BED_AND_BREAKFAST`/`HALF_BOARD`/`FULL_BOARD`/
   `ALL_INCLUSIVE`) can't represent room + package + national park fee + transfer stacked together.
   ClosedTour's existing occupancy-based per-person pricing (`occupancyPrices`: single/double/
   triple/quadruple) is the right fit, since it already lets a per-person total absorb multiple
   components divided across room-sharing.

2. **Room costs, by season**: "Room costs (land-based accommodation charged separately per room
   type/season)" — every season variant of the room rate (High Season, Low Season, and any others
   the document states) always gets added into the Modality — never just one season used for the
   whole year. Each season becomes its own dated price_list entry on the SAME Modality, computed
   via the existing per-night-times-nights-divided-by-occupancy logic, dated to match that
   season's real date range (same mechanism as ordinary ClosedTour seasonal pricing already uses).

3. **Transfers are always included, never left out or left to the client to add at booking.**
   Confirmed reasoning (verbatim): "a closedtour always includes a transfer from a city as we
   have no option to add them on travel c booking side. We must include transfers, if there are
   options on the documents, but we just need to make sure that it includes the correct transfer
   costs." A ClosedTour publishes as one fixed all-in price with no post-booking way to add or
   swap components, so the transfer decision has to be made and baked in at creation time.

4. **National Park Fee is NEVER folded into the price — it's an on-site, pay-locally cost.**
   Confirmed reasoning (verbatim): "It includes room + package + transfer but the park fee must
   be stated it is paid on field when client is there. This information must be added to the
   voucher remarks as well." So the final Modality price = Room (by season) + Package + Transfer
   only. The National Park Fee is deliberately excluded from that sum and instead surfaces as an
   informational note in the Modality's `voucherRemarks`, telling the client it's payable in
   person, on-site, at the time of the visit.

   Maps directly onto an existing, already-shipped mechanism: Transfer already has a
   `location_notes` field for exactly this shape of case (`builder.py` ~line 3439): "a
   location-conditional cost that can't be safely auto-applied to price ... becomes an
   informational voucher note instead — never a mandatory charge applied to every booking."
   ClosedTour doesn't yet have an equivalent hookup in its own voucherRemarks composition chain
   (`builder.py` ~line 2416, `_with_manual_notes(_with_what_to_bring(...))`), but the pattern to
   extend is proven and low-risk — add a `park_fee_notes` (or similarly named) field to the
   ClosedTour extraction schema, and append it into voucherRemarks the same way `location_notes`
   is appended for Transfer. Not yet implemented.

5. **Human confirms start/end destination; the destination step should move earlier in the
   creation flow.** The pickup/drop-off city is also the ClosedTour's first destination in Travel
   Compositor's own destination masterdata (`itinerary_destinations`, resolved via
   `api_client.find_destination_candidates`/`resolve_destinations_bulk` before publish is
   allowed). Verbatim: "The City it comes from is also the first destination in travel c. Maybe
   we could add the itinerary not at the end and rather start with it in the closedtour, so human
   can review the destinations and the app automatically knows which transfer costs are
   included." So the destination-confirmation step should be reordered to happen EARLY in the
   ClosedTour creation flow (currently late/at publish-time gating only) — once the human confirms
   start/end destinations up front, the app can automatically look up matching transfer costs
   from the document, rather than transfer cost being decided independently of destination.

   The AI cannot infer the correct pickup city on its own even when it seems "obvious" to a
   person: verbatim, "In this tour it would start in Phuket, the app can't know that but because
   of a typically travel route from humans it will be Phuket as it has an own airport." Real-world
   travel-pattern reasoning (nearest airport, typical client origin) requires a human — this is a
   mandatory human-confirmed field, never an AI guess.

6. **Day-by-day description must include the transfer, on Day 1 and the last day.** Verbatim:
   "The day by day description also must include the transfer then and therefore on day 1 and
   the last day it must be included and adopted." Confirms two transfer legs exist: an arrival
   transfer (Day 1) and a departure transfer (last day) — matches decision #8 below (round-trip
   transfer cost).

7. **Meeting Point (free text) must always match the confirmed starting point.** Confirmed: "yes."

8. **Transfer cost formula, fully confirmed with real numbers from the Our Jungle Resorts
   contract**: the human defines both a start point and an end point for the ClosedTour (in this
   example, both = Phuket). The transfer cost is calculated from the MOST EXPENSIVE rate among
   only the rows whose destination text matches the confirmed city — e.g. for "Phuket," the
   contract's own zone breakdown is Phuket Center 3,800 / Phuket West 4,090 / Phuket South 3,800 /
   Phuket Far South 4,090 / Airport North 3,230 (all one-way THB); Hua Hin's 9,220 is correctly
   OUT of scope since it's a different city entirely. Highest in-scope = **4,090**.

   The contract's transfer table gives ONE-WAY rates only. Confirmed: this 4,090 one-way rate is
   used **doubled for the round trip** (arrival + departure legs) = **8,180 total**. That total is
   then **divided by the total number of pax sharing the transfer** (verbatim: "transfer total
   price must be devided be the total pax price") — i.e. the same divisor already used for
   room/cabin costs (÷1 single, ÷2 double, ÷3 triple, ÷4 quadruple — never inventing an occupancy
   tier the room doesn't actually sell), so the transfer's per-person contribution is computed
   the same way as every other per-unit-divided-by-occupancy component in this stack.

   **Full confirmed formula for a given occupancy tier N** (e.g. N=2 for Double):
   `per-person price = (room rate for that season × nights ÷ N) + (package price per adult/child)
   + ((one-way transfer rate for confirmed city × 2) ÷ N)`
   National Park Fee excluded from this sum entirely (decision #4) — shown only as a voucher note.

## Open questions, not yet answered

1. **Structured multi-factor breakdown field** (proposed by the product owner): rather than
   folding Room + Package + Transfer into one opaque final number, add a field on the Modality
   that records each component explicitly — e.g. `[{"label": "Room (2 nights, Double)", "amount":
   3780, "basis": "per_room"}, {"label": "3D2N Discovery package", "amount": 6650, "basis":
   "per_pax"}, {"label": "Transfer: Phuket round trip (2x4090)", "amount": 8180, "basis":
   "per_room_or_group"}]` (Park Fee excluded from this sum per decision #4, shown only as a
   voucher note) so the review screen shows the real math and a human can edit one line and see
   the total recompute, instead of trusting one black-box number. Still open:
   - Exact shape (structured list vs. flag + free text)
   - Whether "per group/party size" needs to be a third basis value alongside the existing
     `per_pax`/`per_room` (relevant for tables like Private Lake Tours, priced by total party
     size 2/3/4/5/6, not per person and not per room)
   - Whether it's populated for every Modality (even simple single-factor ones) or only when a
     genuinely multi-factor price is detected
   - Whether it persists anywhere after publish (Travel Compositor's API has no native field for
     it) — e.g. written into the Modality's own remarks/voucher-notes — or exists only at review
     time and is discarded once confirmed
   - Whether this replaces or sits alongside the existing per-supplement `per_pax` flag

2. **Should the tool auto-filter transfer rows to the confirmed city, or show the human a
   shortlist?** Confirmed that only rows whose destination text matches the confirmed city are
   "in scope" and the highest among those wins (e.g. 4,090 for Phuket) — but not yet confirmed
   whether the tool should silently compute this filtered max itself, or always show the human
   the filtered shortlist (Phuket Center/West/South/Far South/Airport North) so they can pick
   directly rather than trust a silent auto-pick.

3. **Does `itinerary_destinations` mixing pickup city + actual visited places affect the
   generated description or destination search?** If the pickup city (Phuket) is added ahead of
   the real visited places (Khao Sok, Cheow Lan Lake) in `itinerary_destinations`, does that risk
   making the tour harder to find when a client searches Travel Compositor by "Khao Sok" instead
   of "Phuket"? Should both always be included together (pickup city AND actual destination) so
   the tour resolves/searches correctly either way? Not yet confirmed.

4. **Are start and end points ever different cities** (e.g. arrive via Phuket, depart via
   Krabi)? Decision #8's formula assumes one confirmed "city" feeding both legs at the same
   per-one-way-rate doubled — if start ≠ end, would each leg instead use its OWN city's
   most-expensive in-scope rate (i.e. NOT simply doubling one number), summed together instead of
   doubled? Not yet confirmed — the worked example (Phuket → Phuket) doesn't exercise this case.

5. **Modality granularity for Room × Package combinations**: Our Jungle Resorts has 6 room
   categories × 5 package variants for High Season (up to 30 combinations). Should every
   Room Category × Package combination become its own Modality, or should Modalities be built
   one-per-Package, with room categories as different price points inside that Modality's
   occupancy tiers? Not yet confirmed.

6. **Nights-to-package matching**: should the tool enforce that only packages whose length
   matches the booked room-nights are selectable (2-night room → only "3D2N" packages)? Not yet
   confirmed.

7. **Low Season handling**: Low Season at this resort has no mandatory package (room-only,
   optionally combined with a la carte F&B/tours/transfer). Should that stay a genuine Hotel
   product, or also become a ClosedTour Modality (room-only base + selectable supplements)?
   Not yet confirmed.

8. **"Challenge" vs "Relaxed" package variant**: the 4D3N Adventure package branches into two
   differently-priced sub-choices for the same length of stay. Is this a customer-chosen variant
   that must become its own separate Modality (matching the existing confirmed rule that a
   priced customer choice — e.g. Ticket language options — always gets its own Modality, never a
   supplement)? Not yet confirmed.

## Also still open, from earlier in the same conversation (broader "what's unclear" review)

- Two-digit year vs. day-of-month ambiguity in date labels like "Nov 26" (month + number that
  could be a truncated year or a day) — proposed rule: trust it as a year whenever the contract's
  own validity header states matching years, otherwise treat it as a day.
- Bare month defaulting to the full calendar month, overridden by explicit day-level dates
  elsewhere in the same document for that same season.
- Cross-year rollover for a range like "20 Dec – 10 Jan" with no year stated on either date —
  proposed rule: take the start year from the contract's own header, roll forward when the month
  number decreases.
- Open-ended final season (only a start date given) defaulting to the contract's own overall
  validity end date.
- Named-but-undated holiday windows ("Easter", "Golden Week") with no explicit day numbers —
  should the tool ever calculate these itself, or always require explicit dates and flag when
  missing?
- Multi-level nested seasons (a nested season inside another nested season) still bail out to a
  human flag rather than auto-splitting — confirmed acceptable for now, no real document has hit
  this case yet.
- Whether a document should be classified by type (plain hotel / bundled resort package /
  multi-day cruise-style product) before the AI picks which extraction prompts to run, instead of
  a human manually choosing ClosedTour vs. Hotel every time.
- Whether Room Category needs to be explicitly tied to one of two named properties when a single
  agreement covers more than one physical resort sharing one room-rate table (Our Jungle House vs.
  Our Jungle Camp) — likely needs a direct question back to the supplier, not something the AI can
  infer.

## Status

Nothing built yet. This is a live design discussion — next step is narrowing the remaining open
questions above (especially #2-4 in "Open questions") into confirmed rules, then implementing:
(a) the reordered destination-confirmation-first ClosedTour creation flow, (b) the full room +
package + round-trip-transfer-divided-by-occupancy pricing formula (confirmed, decision #8),
(c) the park-fee-as-voucher-note fix (decision #4), and (d) Day-1/last-day transfer text in the
generated description (decision #6) — in `ai_extractor.py`, `builder.py`, and `flows/multi_tour.py`,
following the same pattern as the existing per-night-pricing, nested-season-split, and Transfer
`location_notes` house rules.
