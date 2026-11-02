"""
Flight Change Monitor — v1 test phase (2026-11-01).

CONFIRMED PRODUCT-OWNER SCOPE (2026-11-01, verbatim): "for the test phase, we do not need [a
cron]. I will just for the test phase integrate booking numbers manually, and then we can check
if the import was working and if we can survive, if you can see the real time of the flight."

A human pastes in a Travel Compositor booking reference (BNR-XXXX) -> this screen fetches that
booking, extracts its flights (shapes (a) API-booked and (b) manually-entered only - see
flight_extractor.py's module docstring for why closed-tour flights are out of scope), and for
each flight shows the TC data plus - if AVIATIONSTACK_API_KEY is configured - a live comparison
with a GREEN/RED/UNKNOWN flag. No cron, no bulk 48h pull, no persistent storage yet; this is the
manual single-booking test tool the later bulk version will be built on top of.
"""
import streamlit as st

import aviationstack_client
from flight_extractor import extract_flights
from flight_monitor import compare_flight
from travelcompositor_api import TravelCompositorAPI

_RESET_KEYS = ("fm_booking_ref", "fm_booking_result", "fm_flights", "fm_error")


@st.cache_resource
def _get_booking_api_client():
    """Separate client from the app's main api_client.TravelCompositorAPI (st.session_state
    .client) - booking GET methods only exist on travelcompositor_api.py's client class, same
    pattern package_rollover_tool.py already uses for its own Packages calls."""
    return TravelCompositorAPI()


def _flag_badge(flag: str) -> str:
    return {"GREEN": "🟢 OK", "RED": "🔴 Attention needed", "UNKNOWN": "🟡 Unknown"}.get(flag, flag)


def render_flight_monitor_flow():
    st.subheader("✈️ Flight Change Monitor — test phase")
    st.caption(
        "Paste in a Travel Compositor booking reference (e.g. **BNR-1021**) to pull its flights "
        "and check them against live flight data. This is the manual test tool for the upcoming "
        "automatic 48-hour monitor - no cron job runs yet."
    )

    if not aviationstack_client.has_api_key():
        st.info(
            "ℹ️ **AVIATIONSTACK_API_KEY** is not configured yet - flights will be fetched from "
            "Travel Compositor and shown, but live comparison (🟢/🔴) is not available until a key "
            "is added.",
            icon="ℹ️",
        )

    booking_ref = st.text_input(
        "Booking reference",
        value=st.session_state.get("fm_booking_ref", ""),
        placeholder="BNR-1021",
        key="fm_booking_ref_input",
    )

    if st.button("Fetch flights", type="primary", key="fm_fetch_btn"):
        st.session_state.fm_booking_ref = booking_ref.strip()
        st.session_state.fm_booking_result = None
        st.session_state.fm_flights = None
        st.session_state.fm_error = None
        # CONFIRMED BUG FIX (weekly audit, 2026-11-02): per-flight comparison results are keyed
        # by INDEX (fm_comparison_0, fm_comparison_1, ...), not by flight number - without this,
        # looking up a second booking showed the FIRST booking's stale check result against the
        # second booking's flight at the same index until re-checked.
        for key in list(st.session_state.keys()):
            if key.startswith("fm_comparison_"):
                del st.session_state[key]

        if not booking_ref.strip():
            st.session_state.fm_error = "Please enter a booking reference."
        else:
            client = _get_booking_api_client()
            try:
                result = client.get_booking("momiratravel", booking_ref.strip())
            except Exception as e:
                st.session_state.fm_error = f"Error calling Travel Compositor: {e}"
            else:
                if isinstance(result, dict) and "error" in result:
                    st.session_state.fm_error = f"Travel Compositor returned an error: {result['error']}"
                else:
                    st.session_state.fm_booking_result = result
                    st.session_state.fm_flights = extract_flights(result)

    if st.session_state.get("fm_error"):
        st.error(st.session_state.fm_error)

    flights = st.session_state.get("fm_flights")
    if flights is not None:
        if not flights:
            st.warning(
                "No flights found on this booking (shapes `transportservice[].segment[]` or "
                "`manualServices.transport[]`). It may be a hotel-only or closed-tour booking - "
                "closed-tour flights are intentionally not tracked here."
            )
        else:
            st.success(f"Found {len(flights)} flight(s) on **{st.session_state.fm_booking_ref}**.")
            for i, flight in enumerate(flights):
                with st.container(border=True):
                    cols = st.columns([2, 2, 2, 2])
                    cols[0].markdown(f"**Flight**  \n{flight.flight_number}")
                    dep_label = f"{flight.departure_iata or '?'}" + (f" ({flight.departure_name})" if flight.departure_name else "")
                    arr_label = f"{flight.arrival_iata or '?'}" + (f" ({flight.arrival_name})" if flight.arrival_name else "")
                    cols[1].markdown(f"**Route**  \n{dep_label} → {arr_label}")
                    cols[2].markdown(f"**Departure (TC)**  \n{flight.scheduled_departure or '—'}")
                    cols[3].markdown(f"**Pax**  \n{flight.pax_count if flight.pax_count is not None else '—'}")

                    if st.button("Check live status", key=f"fm_check_{i}"):
                        with st.spinner("Checking AviationStack…"):
                            try:
                                comparison = compare_flight(flight)
                            except Exception as e:
                                # Defensive: compare_flight is written to never raise, but a
                                # crash here would otherwise take down the whole page - show it
                                # as an error on this one flight instead (weekly audit, 2026-11-02).
                                st.error(f"Could not check this flight: {e}")
                                comparison = None
                        if comparison is not None:
                            st.session_state[f"fm_comparison_{i}"] = comparison

                    comparison = st.session_state.get(f"fm_comparison_{i}")
                    if comparison:
                        st.markdown(f"**Status:** {_flag_badge(comparison.flag)}")
                        st.caption(comparison.message)
                        if comparison.live_scheduled_departure:
                            st.caption(f"Live scheduled departure: {comparison.live_scheduled_departure}")
                        if comparison.live_flight_number:
                            st.caption(f"Live flight number: {comparison.live_flight_number}")

    if st.button("Reset", key="fm_reset_btn"):
        for key in _RESET_KEYS:
            st.session_state.pop(key, None)
        for key in list(st.session_state.keys()):
            if key.startswith("fm_comparison_"):
                del st.session_state[key]
        st.rerun()
