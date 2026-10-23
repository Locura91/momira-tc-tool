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
