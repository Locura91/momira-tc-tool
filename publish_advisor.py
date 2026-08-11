"""
publish_advisor.py — a second opinion on a product, read out loud before it goes live.

WHY A SEPARATE PASS: the extraction step's job is to report what a document says. It is not
asked whether the result makes commercial sense, and it has no idea what this operator's
house conventions are. So a transport can pass every hard validation the platform has -
locations resolved, dates present, at least one priced bracket - and still be wrong in ways
that only show up later as a booking nobody can deliver or a price nobody meant:

  * a five-hour drive published as taking one hour, because a duration was guessed low;
  * a per-person rate read as per-vehicle, so a four-person party sells at a quarter price;
  * an arrival time that says 09:00 for a route that plainly cannot arrive at 09:00;
  * a name or description that does not match the house style, so the product reads as
    something a different company sells.

None of those can be checked by a rule, because each depends on knowing what the route
actually is. They CAN be checked by asking a model that knows roughly how far Hurghada is
from Luxor and what a private transfer normally costs.

WHAT IT IS NOT: a gate. It never blocks publishing and never edits anything. It returns
observations, ranked, each naming the field and saying what it would expect instead - the
human decides. A blocking check that is sometimes wrong quickly gets ignored; advice that is
sometimes wrong is still worth reading.
"""
import json
from typing import Any, Dict

import ai_extractor

PUBLISH_ADVICE_SYSTEM_PROMPT = """You are the last pair of eyes on a travel product about to be published
into a live booking system, where customers can book it immediately and mistakes cost real money.

You will be given the product as structured JSON, and the house conventions it is supposed to follow. Say
what looks WRONG or SUSPICIOUS. You are advising a human who will decide - you are not blocking anything,
so it is better to raise a real doubt than to stay quiet, but a list of trivia buries the one thing that
matters. Aim for the few observations you would actually want someone to read.

CHECK, IN ROUGHLY THIS ORDER OF IMPORTANCE:

1. MONEY. Does the price make sense for this route and this service class? A private car from Hurghada to
   Cairo (about 6 hours) at 10 EUR is wrong; the same route at 100 EUR is plausible. Is the charge unit
   right - a "p.p." rate published as per-vehicle undercharges by a factor of the party size, and the
   reverse overcharges. Do the occupancy brackets make sense, and does a solo traveller pay a sensible
   amount?
2. TIME AND DISTANCE. Compare the journey duration against what that trip really takes by that mode. Say
   so if it is implausible - and give your own estimate. A duration that is far too short is the dangerous
   one: it publishes an arrival time the vehicle cannot meet.
3. GEOGRAPHY. Are departure and arrival genuinely different places, in the direction stated? Does the
   route make sense at all (not "Luxor to Luxor", not two places on different continents by car)?
4. DATES. Is the validity range sensible - not in the past, not absurdly long, not a single day by
   accident?
5. HOUSE STYLE. Does the name and description follow the stated conventions? Quote what it should be.
6. ANYTHING ELSE that would embarrass whoever published it.

Say nothing about fields that are fine. Do not repeat the product back. Do not invent problems to fill
the list - "nothing looks wrong" is a valid and useful answer.

Output ONLY valid JSON, no markdown fences:
{
  "verdict": "looks_right" or "check_first" or "likely_wrong",
  "observations": [
    {"field": "the field name this is about, e.g. duration_time",
     "severity": "high" or "medium" or "low",
     "issue": "what is wrong, in one sentence a busy person can act on",
     "suggestion": "the value or wording you would expect instead, or empty if you cannot say"}
  ],
  "summary": "one sentence overall"
}
Use severity "high" ONLY for something that would produce a wrong price, an undeliverable booking, or a
product in the wrong place."""


HOUSE_RULES_TRANSPORT = """HOUSE CONVENTIONS FOR A TRANSPORT (this operator's own rules):
- Name is exactly "DEPARTURE - ARRIVAL", e.g. "Luxor - Hurghada".
- Description is ONE sentence: "<Style>. Transfer between <ORIGIN> and your booked accommodation in
  <DESTINATION>." where Style is Private, Shuttle, etc.
- Departure is the FROM place, arrival is the TO place.
- Transport type: CAR for private, VAN for shuttle, COMBINED when the journey mixes modes (car + train,
  car + boat, island to island).
- Journey duration is rounded UP to a full hour.
- Occupancy brackets are normally two: 1-1 pax, and 2-N pax. On a per-person rate with a stated minimum
  party size, the 1-pax bracket is priced at the per-person rate times that minimum, so a solo traveller
  pays the smallest party total.
- Start date is the date stated on the document, or today when the document states none."""


def _payload_for_review(data: Dict[str, Any], build_result: Dict[str, Any]) -> Dict[str, Any]:
    """The few dozen fields worth an opinion, rather than the whole payload.

    Sending everything would bury the price and the duration - the two fields that actually
    matter - under images, inventory windows and translation scaffolding."""
    payload = (build_result or {}).get("transport_payload") or {}
    segment = (payload.get("segments") or [{}])[0]
    options = []
    for action in (build_result or {}).get("option_actions") or []:
        opt = action.get("option_payload") or {}
        supplement = 0.0
        for price in (opt.get("prices") or []):
            if isinstance(price, dict):
                supplement = price.get("adultPriceSupplement", 0.0) or 0.0
        options.append({
            "code": action.get("code"),
            "min_pax": action.get("min_occupancy"),
            "max_pax": action.get("max_occupancy"),
            "price_for_this_bracket": round((payload.get("baseAdultPrice") or 0) + supplement, 2),
        })
    return {
        "name": payload.get("name"),
        "description": ((payload.get("datasheets") or {}).get("EN") or {}).get("description"),
        "service_class_from_document": data.get("service_name"),
        "departure": data.get("departure_name"),
        "arrival": data.get("arrival_name"),
        "transport_type": payload.get("transportType"),
        "currency": payload.get("currency"),
        "price_is_per_person": payload.get("pricePerPax"),
        "minimum_party_size_from_document": data.get("min_billable_pax"),
        "departure_time": segment.get("departureTime"),
        "arrival_time": segment.get("arrivalTime"),
        "plus_days": segment.get("plusDays"),
        "journey_duration": segment.get("durationTime") or data.get("duration_time"),
        "duration_was_estimated_not_stated": bool(data.get("duration_estimated")),
        "valid_from": payload.get("startDate"),
        "valid_to": payload.get("endDate"),
        "modalities": options,
    }


def advise_transport(data: Dict[str, Any], build_result: Dict[str, Any],
                     model: str = "claude-sonnet-5") -> Dict[str, Any]:
    """Ask for a second opinion on one transport. Never raises: advice failing to load must
    not stand between a correct product and the Publish button."""
    try:
        review = _payload_for_review(data, build_result)
        user_content = (f"{HOUSE_RULES_TRANSPORT}\n\nTHE PRODUCT ABOUT TO BE PUBLISHED:\n"
                        f"{json.dumps(review, indent=2, ensure_ascii=False)}")
        result = ai_extractor._call_claude(PUBLISH_ADVICE_SYSTEM_PROMPT, user_content,
                                           model, max_tokens=2048) or {}
    except Exception as e:
        return {"verdict": "unavailable", "observations": [],
                "summary": f"Couldn't get a second opinion: {ai_extractor.friendly_error_message(e)}",
                "error": True}

    observations = []
    for item in (result.get("observations") or []):
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "").strip()
        if not issue:
            continue
        severity = str(item.get("severity") or "low").lower()
        observations.append({
            "field": str(item.get("field") or "").strip(),
            "severity": severity if severity in ("high", "medium", "low") else "low",
            "issue": issue,
            "suggestion": str(item.get("suggestion") or "").strip(),
        })
    rank = {"high": 0, "medium": 1, "low": 2}
    observations.sort(key=lambda o: rank[o["severity"]])
    verdict = str(result.get("verdict") or "").strip().lower()
    if verdict not in ("looks_right", "check_first", "likely_wrong"):
        verdict = "likely_wrong" if any(o["severity"] == "high" for o in observations) else \
                  ("check_first" if observations else "looks_right")
    return {"verdict": verdict, "observations": observations,
            "summary": str(result.get("summary") or "").strip(), "error": False}


def render_advice(advice: Dict[str, Any]) -> None:
    """Show the advice. Deliberately never disables Publish - see the module docstring."""
    import streamlit as st

    if not advice:
        return
    if advice.get("error"):
        st.caption(f"⚠️ {advice.get('summary')}")
        return

    icon = {"looks_right": "✅", "check_first": "🟡", "likely_wrong": "🔴"}.get(advice["verdict"], "🟡")
    headline = {"looks_right": "Nothing looks wrong.",
                "check_first": "Worth checking before you publish.",
                "likely_wrong": "This looks wrong — read before publishing."}.get(advice["verdict"], "")
    (st.success if advice["verdict"] == "looks_right" else
     st.error if advice["verdict"] == "likely_wrong" else st.warning)(
        f"{icon} **{headline}** {advice.get('summary', '')}")

    for obs in advice["observations"]:
        badge = {"high": "🔴", "medium": "🟡", "low": "⚪"}[obs["severity"]]
        line = f"{badge} **{obs['field'] or 'general'}** — {obs['issue']}"
        if obs["suggestion"]:
            line += f"  \n&nbsp;&nbsp;&nbsp;&nbsp;*Suggested:* `{obs['suggestion']}`"
        st.markdown(line, unsafe_allow_html=True)

    st.caption("This is advice, not a check — it never blocks publishing and never changes "
              "anything. It is a second reading by a model that knows roughly how far these "
              "places are apart and what these services normally cost.")
