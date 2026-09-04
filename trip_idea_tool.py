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
import trip_quote_client as tqc
import trip_search_rules as tsr
from api_client import TravelCompositorAPI

MODULE_BUILD = "2026-09-04-pptx-text-and-image-extraction"

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


def _run_debug_quote(build_fn):
    """Shared runner for every branch of the debug panel below: builds an api_client +
    TripQuoteClient, calls `build_fn(api, client)` to do the actual product-specific quote call,
    and stores the raw result (or a raw error string - deliberately NOT run through
    ai_extractor.friendly_error_message, since that's tuned for the AI/Claude service, not a raw
    Travel Compositor HTTP/network error - this is an advanced debug tool for Chris, seeing the
    real underlying error is the whole point) into session_state for display below."""
    with st.spinner("Calling the live Travel Compositor account..."):
        try:
            api = TravelCompositorAPI()
            client = tqc.TripQuoteClient(api)
            st.session_state.qdbg_last_result = build_fn(api, client)
            st.session_state.qdbg_last_error = None
        except Exception as e:
            st.session_state.qdbg_last_result = None
            st.session_state.qdbg_last_error = f"{type(e).__name__}: {e}"


def _render_quote_debug_panel():
    """⚠️ Fires a REAL call against Travel Compositor's live booking/Quote API - Momira's actual
    account, not a sandbox. Exists specifically to let a human running this tool somewhere with
    real TRAVELC_* credentials and real network access (this internal tool's usual home - NOT the
    cloud sandbox this feature is often developed in, which has neither) verify the guessed
    request shapes in trip_quote_client.py actually work, and see what a real Quote RESPONSE
    looks like - something that has never been observed at all before this panel existed (see
    that module's own docstring for the full "unverified" caveat).

    Stays QUOTE ONLY - the exact same hard boundary as the rest of this feature; nothing here can
    Confirm, Prebook, or Book (enforced by tests/test_trip_idea_never_books.py, which covers
    every trip_*.py file, this one included)."""
    st.divider()
    with st.expander("🔧 Live Quote endpoint test (advanced — hits the real Travel Compositor account)"):
        st.warning("⚠️ This fires a REAL call against Momira's live Travel Compositor account "
                  "(not a sandbox/test account) — it needs real `TRAVELC_*` credentials and "
                  "network access to work at all. It only ever calls a **Quote** endpoint — the "
                  "same non-binding step Travel Compositor's own booking wizard uses for its "
                  "live ~30s search — never Confirm, Prebook, or Book, so nothing gets held or "
                  "charged. Useful for checking that the request shapes in `trip_quote_client.py` "
                  "(the best-available guess from a different operator's API docs — see that "
                  "file's own warning) actually work against Momira's real account, and for "
                  "seeing a real response shape for the first time.")

        endpoint = st.selectbox("Which Quote endpoint?",
                                ["Accommodations (hotels)", "Transports (flights)", "Transfer",
                                 "Tickets (activities)", "Closed Tour"], key="qdbg_endpoint")

        pcol1, pcol2 = st.columns(2)
        with pcol1:
            adults = st.number_input("Adults", min_value=1, max_value=9, value=2, key="qdbg_adults")
        with pcol2:
            children_raw = st.text_input("Children ages (comma-separated, e.g. 6,9)",
                                         value="", key="qdbg_children")
        try:
            children_ages = [int(a.strip()) for a in children_raw.split(",") if a.strip()]
        except ValueError:
            children_ages = []
            st.caption("⚠️ Couldn't parse children ages — treating as no children.")

        if endpoint == "Accommodations (hotels)":
            dest_query = st.text_input("Destination (name or code)", value="Cairo", key="qdbg_dest")
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                date_from = st.text_input("Check-in (YYYY-MM-DD)", value="2027-03-18", key="qdbg_from")
            with dcol2:
                date_to = st.text_input("Check-out (YYYY-MM-DD)", value="2027-03-21", key="qdbg_to")

            if st.button("🚀 Fire real Accommodations Quote call", key="qdbg_fire_acc"):
                def _do(api, client):
                    dest = api.resolve_destination(dest_query)
                    dist = tqc.build_distributions(adults, children_ages)
                    result = client.quote_accommodations(dist, date_from, date_to,
                                                          destination_code=dest.get("tc_code"))
                    return {"resolved_destination": dest, "request_distributions": dist, "response": result}
                _run_debug_quote(_do)

        elif endpoint == "Transports (flights)":
            tcol1, tcol2 = st.columns(2)
            with tcol1:
                dep_query = st.text_input("Departure (airport/place name or code)", value="Frankfurt", key="qdbg_dep")
            with tcol2:
                arr_query = st.text_input("Arrival (destination name or code)", value="Cairo", key="qdbg_arr")
            dep_date = st.text_input("Departure date (YYYY-MM-DD)", value="2027-03-18", key="qdbg_dep_date")

            if st.button("🚀 Fire real Transports Quote call", key="qdbg_fire_transport"):
                def _do(api, client):
                    dep = api.resolve_transport_base(dep_query)
                    arr = api.resolve_destination(arr_query)
                    journey = tqc.build_transport_journey(
                        dep.get("code"), "TRANSPORT_BASE", arr.get("tc_code"), "DESTINATION", dep_date)
                    # Transports' real "persons" field is FLAT (no rooms) - confirmed 2026-09-01,
                    # see build_persons()'s docstring. build_distributions() would be wrong here.
                    persons = tqc.build_persons(adults, children_ages)
                    result = client.quote_transports([journey], persons)
                    return {"resolved_departure": dep, "resolved_arrival": arr,
                           "request_journey": journey, "response": result}
                _run_debug_quote(_do)

        elif endpoint == "Transfer":
            xcol1, xcol2 = st.columns(2)
            with xcol1:
                pickup_query = st.text_input("Pickup (airport/place name or code)", value="Cairo Airport", key="qdbg_pu")
            with xcol2:
                dropoff_query = st.text_input("Drop-off (place name or code)", value="Cairo", key="qdbg_do")
            pickup_date = st.text_input("Pickup date-time (YYYY-MM-DDTHH:MM:SS)", value="2027-03-18T14:00:00", key="qdbg_pu_date")
            st.caption("Confirmed 2026-09-01: a transfer endpoint is either an accommodation or a "
                      "transport base (airport/station), not a generic pickup/dropoff pair - this "
                      "debug form always resolves Pickup as a transport base and Drop-off as a "
                      "destination's accommodation lookup isn't wired here yet, so this uses "
                      "transport-base-to-transport-base as the simplest real-shape test.")

            if st.button("🚀 Fire real Transfer Quote call", key="qdbg_fire_transfer"):
                def _do(api, client):
                    pu = api.resolve_transport_base(pickup_query)
                    do = api.resolve_transport_base(dropoff_query)
                    persons = tqc.build_persons(adults, children_ages)
                    from_loc = tqc.build_transfer_location(transport_base_id=pu.get("code"))
                    to_loc = tqc.build_transfer_location(transport_base_id=do.get("code"))
                    result = client.quote_transfers(persons, from_loc, to_loc, pickup_date)
                    return {"resolved_pickup": pu, "resolved_dropoff": do,
                           "request_from": from_loc, "request_to": to_loc, "response": result}
                _run_debug_quote(_do)

        elif endpoint == "Tickets (activities)":
            ticket_id = st.text_input("Ticket catalog code (e.g. TICKET-417967)", value="", key="qdbg_ticket_id")
            ticket_date = st.text_input("Activity date (YYYY-MM-DD)", value="2027-03-19", key="qdbg_ticket_date")
            if not ticket_id.strip():
                st.caption("Need a real ticket catalog code first — see a saved Idea's page or "
                          "the project doc's captured Ticket examples (e.g. TICKET-417967).")
            st.caption("Confirmed 2026-09-01: this calls the single-ticket endpoint "
                      "(`/booking/tickets/{ticketId}/quote`), which returns EVERY modality and "
                      "its prices for this one ticket - there is no modality filter on the "
                      "request. checkIn/checkOut are both set to the activity date below (a real "
                      "date range is possible but tickets in the captured examples are always "
                      "single-day).")

            if st.button("🚀 Fire real Tickets Quote call", key="qdbg_fire_ticket", disabled=not ticket_id.strip()):
                def _do(api, client):
                    persons = tqc.build_persons(adults, children_ages)
                    result = client.quote_ticket(ticket_id.strip(), persons, ticket_date, ticket_date)
                    return {"response": result}
                _run_debug_quote(_do)

        else:  # Closed Tour
            ct_id = st.text_input("Closed Tour code", value="", key="qdbg_ct_id")
            start_date = st.text_input("Start date (YYYY-MM-DD)", value="2027-03-18", key="qdbg_ct_start")
            origin = st.text_input("Origin code (optional)", value="", key="qdbg_ct_origin")
            if not ct_id.strip():
                st.caption("Need a real Closed Tour code first — this product type hasn't been "
                          "captured yet (still an open item in the project doc).")

            if st.button("🚀 Fire real Closed Tour Quote call", key="qdbg_fire_ct", disabled=not ct_id.strip()):
                def _do(api, client):
                    dist = tqc.build_distributions(adults, children_ages)
                    result = client.quote_closed_tour(ct_id.strip(), start_date, dist,
                                                       origin_code=origin.strip())
                    return {"response": result}
                _run_debug_quote(_do)

        if st.session_state.get("qdbg_last_error"):
            st.error(f"Raw error (not a customer-facing message — this is a debug tool): "
                    f"{st.session_state.qdbg_last_error}")
        if st.session_state.get("qdbg_last_result") is not None:
            st.markdown("##### Raw response")
            st.json(st.session_state.qdbg_last_result)


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
        st.caption("This is as far as the automatic extraction goes today — turning this into "
                  "real, priced options (Phase 1's selection logic) is in progress. The panel "
                  "below lets you fire a real Quote call directly, ahead of that being wired in.")

    _render_quote_debug_panel()
