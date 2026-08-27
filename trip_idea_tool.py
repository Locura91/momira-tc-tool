"""
trip_idea_tool.py — "AI Trip Idea" prototype screen: a customer's free-text trip idea, turned
into structured search criteria and shown back to a human.

CONTEXT (2026-08-19): Chris's long-term vision is a client-facing widget where a customer types
their travel idea as a sentence ("2 adults, travelling in February, city and beach in Spain")
instead of filling in separate destination/date/pax/theme fields, and the platform turns that
into a real, priced package from Travel Compositor.

THIS SCREEN IS ONLY THE FIRST HALF, made visible inside the internal tool so it can be tried and
reacted to. It does NOT search Travel Compositor and does NOT produce a bookable package - that
half is still blocked on two open questions Chris is checking with Travel Compositor: whether
the account has the Multidestination Booking Engine / live search-availability API enabled at
all (a different module from the contract-management API this platform already uses), and how
flights would be sourced (TC's own inventory if the module includes it, vs. a separate flight
API). See the project doc "client-trip-prompt-idea.md" for the full writeup.

WHY THIS LIVES IN THE INTERNAL TOOL FOR NOW, NOT ON A PUBLIC WEBSITE: the extraction quality
needs to be seen and judged by a human before it's worth exposing to a real customer, and a
public-facing widget is a materially different piece of software (needs to handle anonymous
public traffic, live embedded on the marketing site) than this internal Streamlit app - see the
project doc for why. This screen is the fastest way to let Chris try the idea and react to real
output, nothing more.
"""
import streamlit as st

import ai_extractor
import trip_prompt_extractor as tpe
import trip_search_rules as tsr

MODULE_BUILD = "2026-08-27-outreach-select-all-place-themes"

_PHASE_KEY = "ti_phase"


def _reset():
    for key in list(st.session_state.keys()):
        if key.startswith("ti_"):
            del st.session_state[key]


def _render_criteria(result):
    """Human-readable summary of the extracted criteria, not a raw JSON dump - the whole
    point of this screen is letting a human judge whether the extraction actually understood
    the customer, and nobody judges that by reading a JSON blob."""
    dest_bits = [b for b in (result.get("destination_region_or_city"), result.get("destination_country")) if b]
    dest = ", ".join(dest_bits) if dest_bits else "*(not stated)*"

    when_bits = []
    if result.get("date_range_start") and result.get("date_range_end"):
        when_bits.append(f"{result['date_range_start']} → {result['date_range_end']}")
    elif result.get("travel_month"):
        when_bits.append(result["travel_month"])
    if result.get("duration_nights"):
        when_bits.append(f"{result['duration_nights']} night(s)")
    when = " · ".join(when_bits) if when_bits else "*(not stated)*"

    party_bits = [f"{result.get('adults', 0)} adult(s)"]
    if result.get("children"):
        ages = result.get("children_ages") or []
        ages_str = f" (ages {', '.join(str(a) for a in ages)})" if ages else ""
        party_bits.append(f"{result['children']} child(ren){ages_str}")
    party = " + ".join(party_bits)

    themes = result.get("themes") or []
    budget = result.get("budget_hint") or ""
    budget_tier = result.get("budget_tier") or "unspecified"
    tier_label = {"budget": "💰 Budget-friendly", "superior": "⭐ Superior",
                 "luxury": "💎 Luxury", "unspecified": "*(not stated)*"}.get(budget_tier, budget_tier)

    ccol1, ccol2 = st.columns(2)
    with ccol1:
        st.markdown(f"**📍 Destination:** {dest}")
        st.markdown(f"**📅 When:** {when}")
        st.markdown(f"**👥 Travellers:** {party}")
    with ccol2:
        st.markdown(f"**🎯 Themes:** {', '.join(themes) if themes else '*(not stated)*'}")
        st.markdown(f"**💶 Budget tier:** {tier_label}" + (f" *(\"{budget}\")*" if budget else ""))

    confidence = result.get("confidence", "low")
    badge = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}.get(confidence, confidence)
    st.markdown(f"**Confidence:** {badge}")

    if result.get("clarification_needed"):
        st.info(f"💬 Before searching, the widget would ask the customer: "
               f"*\"{result['clarification_needed']}\"*")

    # CONFIRMED PRODUCT-OWNER RULE (2026-08-19): "Budget friendly means 3* hotel, small car (if
    # requested). Superior means 4* hotel. Luxury means 5* hotel. Rule must be always with
    # breakfast, hotel reviews minimum 8." See trip_search_rules.py for where this rule lives.
    rules = tsr.resolve_search_rules(budget_tier, car_wanted=bool(result.get("car_wanted")))
    st.markdown("##### 🧭 Search rules this tier would apply")
    rcol1, rcol2, rcol3, rcol4 = st.columns(4)
    with rcol1:
        stars = rules["hotel_star_rating"]
        st.metric("Hotel stars", f"{stars}★" if stars else "any")
    with rcol2:
        st.metric("Board", rules["board_type"].capitalize())
    with rcol3:
        st.metric("Min. review score", f"{rules['min_hotel_review_score']}/10*")
    with rcol4:
        st.metric("Car category", rules["car_category"] or ("none requested" if not result.get("car_wanted") else "standard"))
    st.caption("*Review score assumed on a /10 scale — not yet confirmed against Travel "
              "Compositor's actual review scale. Breakfast and the minimum review score apply "
              "regardless of tier; car category only applies when a rental car is actually part "
              "of the trip.")

    with st.expander("🔎 Raw structured output (what would be handed to a search step)"):
        st.json({**result, "resolved_search_rules": rules})


def render_trip_idea_tool():
    st.subheader("💡 AI Trip Idea (prototype)")
    st.warning("⚠️ **Prototype — not connected to Travel Compositor.** This only shows how a "
              "customer's free-text trip idea gets turned into structured search criteria. It "
              "does not search for real availability or produce a bookable package yet — that "
              "depends on confirming Travel Compositor's search/booking API access. See the "
              "\"client-trip-prompt-idea\" project note for the full picture.")

    st.caption("Type a trip idea the way a customer might describe it — casually, not filling "
              "in separate fields. Try leaving something out (destination, dates, party size) "
              "to see how it handles an incomplete idea.")

    example = st.selectbox(
        "Try an example, or write your own below:",
        ["(write my own)",
         "2 adults, travelling in February, with goal of city and beach in Spain",
         "we'd love something relaxing by the sea next month, budget-friendly",
         "Family trip with 2 kids (ages 6 and 9) to Portugal this summer, looking for beaches "
         "and some culture too, about 10 days",
         "honeymoon somewhere romantic",
         "solo trip, adventure and hiking, Norway, first two weeks of September"],
        key="ti_example",
    )
    default_text = "" if example == "(write my own)" else example
    prompt = st.text_area("Trip idea", value=default_text, height=100, key="ti_prompt",
                          placeholder="e.g. \"2 adults, travelling in February, with goal of city "
                                      "and beach in Spain\"")

    if st.button("✨ Understand this trip idea", type="primary", disabled=not prompt.strip()):
        with st.spinner("Reading the trip idea..."):
            try:
                st.session_state.ti_result = tpe.extract_trip_criteria(prompt.strip())
                st.session_state.ti_result_prompt = prompt.strip()
            except Exception as e:
                st.session_state.ti_result = None
                st.error(ai_extractor.friendly_error_message(e))
        st.rerun()

    result = st.session_state.get("ti_result")
    if result:
        st.divider()
        st.caption(f"For: *\"{st.session_state.get('ti_result_prompt', '')}\"*")
        _render_criteria(result)
        st.caption("This is as far as the prototype goes today — the next step (turning this "
                  "into a real search against Travel Compositor) is still pending confirmation "
                  "of Travel Compositor's search/booking API access.")
