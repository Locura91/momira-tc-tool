# Momira TC Tool — Developer Notes for Claude

## Pricing tiers (bulk update rule — CRITICAL)

When building bulk update tools for **transport**, **transfers**, or **tickets**:

- Prices in TC may include cheaper per-unit prices for larger packs (e.g. 5–9 packs cheaper than 1–4).
- We manually maintain tiered pricing:
  - **1–4 packs** → one price (higher)
  - **5–9 packs** → one price (usually also higher than raw TC prices)
- If a cheaper price exists in the system, **show it as informational only** (e.g. "A cheaper price of €X is available for this quantity").
- **Never auto-apply the cheaper price when the user clicks Apply/Confirm.** Always keep the existing (higher) price.
- Reason: applying granular per-quantity prices requires significant manual work and the difference is usually only €1–5, not worth it.

**Summary rule**: display cheaper prices as a warning/hint, but the Apply action must leave prices unchanged (or apply the same existing price, not the cheaper one).

## ClosedTour pricing structure (CRITICAL)

When creating or building prices for a **ClosedTour**:

- Prices may only be set for these occupancy types: **single room**, **double room**, **triple room**, **quadruple room**. No other occupancy types exist.
- **Seasonal pricing** (e.g. high season vs low season): one modality CAN have multiple price_list entries with **non-overlapping** date ranges. Different seasons = different rows in the same modality's price_list, NOT separate modalities.
- **Separate modalities** are needed ONLY when the **type of service** differs (e.g. "Standard" vs "Deluxe" cabin, English vs German guide). NOT for group-size pricing tiers.
- Date ranges within one modality must NEVER overlap — overlapping ranges require separate modalities.

## ClosedTour pricing rules (derived from real DMC documents)

- **Price ordering invariant**: singlePrice >= doublePrice >= triplePrice >= quadruplePrice ALWAYS (fewer people sharing = higher per-person cost).
- **Quadruple price default**: if not explicitly stated, quadruplePrice = doublePrice (a quad booking = two double rooms). Code-enforced in builder.py.
- **Single supplement**: singlePrice = doublePrice + single supplement. Never copy doublePrice as singlePrice unless the document explicitly waives the supplement.
- **Per-night pricing**: multiply by number of nights before entering prices. A "per cabin" rate must be divided by occupancy count to get per-person price.
- **Tour code → modality code**: if the document provides a tour code, use it as the modality_code.
- **TC 2-hour airport buffer**: tour start_time = airport arrival + 2h; tour end_time = flight departure − 2h.
- **Group-size tiers → occupancy prices, NOT separate modalities**: DMC documents often list per-group-size prices (1 pax, 2-3 pax, 4-6 pax). These map to singlePrice/doublePrice/triplePrice/quadruplePrice within ONE modality. Max 9 passengers per booking. Tiers beyond 4 pax are informational only.
