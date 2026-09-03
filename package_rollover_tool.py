"""
package_rollover_tool.py — "Package Rollover" prototype screen: a human enters ONE Holiday
Package ID, the tool fetches its current departure/price/hotel data and its calendar of
future departure dates from Travel Compositor, proposes a replacement departure under the
confirmed rules (see package_rollover_rules.py), and shows it for a human to review — see the
"package-auto-rollover-rules" project note for the full background.

SCOPE FOR THIS FIRST VERSION (2026-08-19): Chris asked to start with a human-driven,
one-Package-ID-at-a-time tool rather than an automatic scan of every package on the site —
this screen is exactly that. It makes REAL, LIVE GET calls to Travel Compositor (the
Packages API is already reachable with the existing credentials — see
travelcompositor_api.py's get_holiday_package_info / get_holiday_packages, already used by the
Holiday Package translation sync). It does NOT call PUT / update anything — the exact
request body update_holiday_package's departure/price fields would need is still unconfirmed
(WRITABLE_FIELDS in sync_holiday_package.py only covers title/description/active/visible/etc,
never departure date or price), so applying a change is intentionally not wired yet. This
screen's job is to get a REAL example response from a real Package ID in front of a human, so
the field names package_rollover_rules.py is guessing at can be confirmed or corrected, and so
the "propose it, human approves" review step (already confirmed as the required flow) can
start being tested with real data.

CONFIRMED (product owner, 2026-08-19): "I need to make sure that nothing will be booked and
only accept the new departure time after the human review the package." Answering "where does
the human see the new departure/price" — the best case Chris described is the human applying
the change themselves inside Travel Compositor's own package-edit screen, the exact same
"regular" click-Save action already used today, rather than trusting a button in THIS app to
have written the right thing. So this screen never applies anything itself (no PUT call
anywhere in this file or in package_rollover_rules.py — enforced by
tests/test_package_rollover_never_writes.py, the same kind of regression-test guardrail used
for the AI Trip Idea feature's "never book" boundary). Instead it shows an explicit OLD vs. NEW
comparison (current departure/price vs. proposed departure/price) and tells the human to make
that exact change in Travel Compositor's normal edit screen and click Save there themselves.
"""
from datetime import date

import streamlit as st

import ai_extractor
import package_rollover_rules as prr
from travelcompositor_api import TravelCompositorAPI

MODULE_BUILD = "2026-09-03-stale-image-warning-wired"

_PHASE_KEY = "pkr_phase"


def _reset():
    for key in list(st.session_state.keys()):
        if key.startswith("pkr_"):
            del st.session_state[key]


@st.cache_resource
def _get_package_api_client():
    """A separate client from the app's main api_client.TravelCompositorAPI (st.session_state
    .client) — the Packages GET/PUT methods only exist on travelcompositor_api.py's client
    class (see sync_holiday_package.py / run_sync_packages.py, which already use it for the
    Holiday Package translation sync). Same underlying account/credentials, different Python
    class. Cached so this tool doesn't re-authenticate on every rerun."""
    return TravelCompositorAPI()


def _render_field_note(label, value, field_name, unit=""):
    if field_name is None:
        st.markdown(f"**{label}:** *(not found in the response — see raw data below)*")
    else:
        st.markdown(f"**{label}:** {value}{unit}  \n"
                   f"<span style='color:gray;font-size:0.8em'>matched field: `{field_name}`</span>",
                   unsafe_allow_html=True)


def _do_lookup(package_id: str):
    api = _get_package_api_client()
    with st.spinner(f"Looking up package {package_id}..."):
        info = api.get_holiday_package_info(api.microsite_id, package_id)
        day_to_day = api.get_holiday_package_day_to_day(api.microsite_id, package_id)
        calendar = api.get_holiday_package_calendar(api.microsite_id, package_id)
    st.session_state.pkr_info = info
    st.session_state.pkr_day_to_day = day_to_day
    st.session_state.pkr_calendar = calendar
    st.session_state.pkr_package_id = package_id


def _current_price_from(info, day_to_day):
    """Tries the info response first, then day-to-day — whichever has a recognizable price
    field. Both are best-effort, same heuristic as the calendar candidates. Returns
    (matched_field_name, price) so the UI can show the REAL key that was matched (e.g.
    "pricePerPerson"), not a guessed/hardcoded label — see _render_field_note's docstring
    intent and package_rollover_rules.py's "flagged, not guessed" design."""
    for source in (info, day_to_day):
        if isinstance(source, dict) and "error" not in source:
            field, price = prr.find_price(source)
            if price is not None:
                return field, price
    return None, None


def _current_departure_from(info, day_to_day):
    """Same idea as _current_price_from, for the CURRENT departure date — needed for the
    old-vs-new comparison a human applies manually in Travel Compositor. Returns
    (matched_field_name, date)."""
    for source in (info, day_to_day):
        if isinstance(source, dict) and "error" not in source:
            field, dep_date = prr.find_departure_date(source)
            if dep_date is not None:
                return field, dep_date
    return None, None


def render_package_rollover_tool():
    st.subheader("🔁 Package Rollover (prototype)")
    st.warning("⚠️ **Prototype — read-only, nothing is ever booked or written from this "
              "screen.** It looks up a real package and proposes a replacement departure "
              "using the confirmed rules (under 14 days out → roll to ~4 months ahead, hotel "
              "rating 8+, price within +3.5% of today's live price). It makes real GET calls "
              "to Travel Compositor but has no PUT/write call anywhere in its code — that's "
              "enforced by a regression test, not just a promise. **Applying a proposed "
              "change is a manual step you do yourself, inside Travel Compositor's normal "
              "package-edit screen** — exactly the same click-Save you already do today. See "
              "the \"package-auto-rollover-rules\" project note for the full picture.")
    st.info("📌 **Scope, confirmed 2026-08-21:** a Holiday Package ID that's ONLY a ClosedTour "
           "(e.g. a fixed-schedule cruise) doesn't need this tool — its departures are managed "
           "through the normal ClosedTour update flow instead. Use this tool for a package "
           "that's fully dynamic (no ClosedTour component — any day works) or one built around "
           "a ClosedTour PLUS dynamic flights and a pre/post program. **For now, testing is "
           "focused on fully dynamic packages (no ClosedTour component)** — those are the ones "
           "where the 'no calendar found' path below applies.")

    package_id = st.text_input("Holiday Package ID", key="pkr_input",
                               placeholder="e.g. 59582825")

    if st.button("🔍 Look up package", type="primary", disabled=not package_id.strip()):
        try:
            _do_lookup(package_id.strip())
        except Exception as e:
            st.error(ai_extractor.friendly_error_message(e))
        st.rerun()

    info = st.session_state.get("pkr_info")
    if info is None:
        return

    st.divider()
    st.caption(f"Package ID: **{st.session_state.get('pkr_package_id')}**")

    for label, payload in (("Package info", info),
                           ("Day-to-day detail", st.session_state.get("pkr_day_to_day")),
                           ("Calendar", st.session_state.get("pkr_calendar"))):
        if isinstance(payload, dict) and "error" in payload:
            st.error(f"{label}: Travel Compositor returned an error — "
                    f"{payload.get('error')}: {payload.get('message')}")

    day_to_day = st.session_state.get("pkr_day_to_day")
    calendar = st.session_state.get("pkr_calendar")

    current_departure_field, current_departure = _current_departure_from(info, day_to_day)
    current_price_field, current_price = _current_price_from(info, day_to_day)
    st.markdown("##### Current state (as published in Travel Compositor today)")
    ccol1, ccol2 = st.columns(2)
    with ccol1:
        _render_field_note("Current departure", current_departure.isoformat() if current_departure else None,
                           current_departure_field)
    with ccol2:
        _render_field_note("Current price", current_price, current_price_field)

    # CONFIRMED RULE (product owner, 2026-08-19): a departure only needs rolling once it's
    # under 14 days out. Real test (package 59011047, 2026-08-21) surfaced that this screen
    # was proposing a replacement unconditionally, even for a departure 8+ months away - which
    # produced a nonsensical "proposed" date EARLIER than the real current departure. This
    # check doesn't block the proposal below (still useful to preview while testing), but makes
    # it unmistakable whether the confirmed trigger has actually fired yet.
    today = date.today()
    if current_departure is not None:
        days_out = (current_departure - today).days
        if prr.is_departure_closed(current_departure, today):
            st.error(f"🔴 This departure is {days_out} day(s) away — under the confirmed "
                    f"14-day trigger, this package NEEDS rolling.")
        else:
            st.success(f"✅ This departure is {days_out} day(s) away — still well outside the "
                      f"confirmed 14-day trigger, so it does NOT need rolling yet. The "
                      f"proposal below is shown for testing/preview only.")
    else:
        st.warning("⚠️ Couldn't determine the current departure date, so it's unknown whether "
                  "this package actually needs rolling yet.")

    if isinstance(calendar, dict) and "error" in calendar:
        st.info("Can't propose a replacement departure — the calendar call failed (see error "
               "above).")
    else:
        candidates = prr.find_candidates(calendar)
        st.markdown("##### Proposed replacement departure")
        if not candidates:
            # CONFIRMED (product owner, 2026-08-21): an empty/unusable calendar is the NORMAL
            # case for a dynamic package (any day can be a departure), not a data gap - see
            # package_rollover_rules.py's "DYNAMIC PACKAGES" note. Propose today + ~4 months
            # directly, avoiding the confirmed Christmas/New Year and Easter blackout windows.
            dyn = prr.propose_dynamic_rollover(today=date.today())
            st.markdown("###### Old → New (what you'd change in Travel Compositor)")
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.markdown("**Departure date**")
                old_dep = current_departure.isoformat() if current_departure else "*(unknown)*"
                st.markdown(f"{old_dep} → **{dyn['proposed_date'].isoformat()}**")
            with dcol2:
                st.markdown("**Price**")
                st.markdown(f"{current_price if current_price is not None else '*(unknown)*'} → "
                           f"*(check the live quote for this date in Travel Compositor)*")
            st.info("No calendar of fixed departure dates was found for this package, which is "
                   "the normal case for a dynamic package — any day can work, so this proposes "
                   "today + ~4 months directly rather than matching against a calendar entry.")
            if dyn["shifted_for_blackout"]:
                st.warning(f"⚠️ The plain ~4-months-out date ({dyn['target_date'].isoformat()}) "
                          f"fell inside a no-departure window ({dyn['blackout_reason']}), so "
                          f"the proposal was pushed forward to the next clear date.")
            st.warning("⚠️ **This doesn't check price or hotel rating automatically** — a "
                      "dynamic package has no calendar entry to read those from. Get a real "
                      "quote for this date in Travel Compositor and confirm the +3.5% price "
                      "cap and 8+ rating rules yourself before applying it.")
            st.warning("⚠️ **If this package includes a fixed-departure component** (e.g. a "
                      "cruise or coach tour that only departs on specific weekdays, like a "
                      "Nile cruise), this proposed date is NOT checked against that schedule — "
                      "confirm it matches before applying it in Travel Compositor.")
            st.info("👉 **This app never applies this change.** Open this package in Travel "
                   "Compositor's own edit screen, enter this departure date the same way you "
                   "would for any regular update, and click **Save there** — the normal, "
                   "manual step you already do today.")
        else:
            result = prr.propose_rollover(candidates, current_price, today=date.today())
            if result["status"] == "no_dated_candidates":
                st.info(f"Found {result['candidates_seen']} calendar entr(y/ies) but couldn't "
                       f"parse a future date from any of them — see raw data below.")
            elif result["status"] == "no_qualifying_candidates":
                if result.get("below_minimum_test_coverage"):
                    st.warning(f"⚠️ Only {result['candidates_tested']} future departure day(s) "
                              f"were available to test — fewer than the minimum "
                              f"{prr.MIN_CANDIDATES_TO_TEST} this process is meant to check.")
                st.warning(f"Found {result['candidates_seen']} future departure(s), but none "
                          f"passed the rating/price rules. See the rejected list below.")
                with st.expander(f"Rejected candidates ({len(result['rejected'])})"):
                    st.json(result["rejected"])
            else:
                best = result["proposed"]

                if result.get("below_minimum_test_coverage"):
                    st.warning(f"⚠️ Only {result['candidates_tested']} future departure day(s) "
                              f"were available to test — fewer than the minimum "
                              f"{prr.MIN_CANDIDATES_TO_TEST} this process is meant to check. "
                              f"Review this recommendation with extra caution.")

                st.markdown("###### Old → New (what you'd change in Travel Compositor)")
                ocol1, ocol2 = st.columns(2)
                with ocol1:
                    st.markdown("**Departure date**")
                    old_dep = current_departure.isoformat() if current_departure else "*(unknown)*"
                    st.markdown(f"{old_dep} → **{best['date'].isoformat()}**")
                with ocol2:
                    st.markdown("**Price**")
                    old_price = current_price if current_price is not None else "*(unknown)*"
                    new_price = best["price"] if best["price"] is not None else "*(unknown)*"
                    st.markdown(f"{old_price} → **{new_price}**")
                if result.get("picked_by") == "cheapest_price":
                    st.caption(f"✅ Picked as the **cheapest** of {result['candidates_tested']} "
                              f"day(s) tested that passed the rating/price rules "
                              f"({result['alternatives_considered']} other qualifying "
                              f"candidate(s) were more expensive). Hotel rating: "
                              f"{best['rating'] if best['rating'] is not None else '(unknown)'}.")
                else:
                    st.caption(f"Hotel rating on the new departure: "
                              f"{best['rating'] if best['rating'] is not None else '(unknown)'}. "
                              f"No qualifying candidate had a known price to compare, so the "
                              f"closest to the ~4-month target ({result['target_date'].isoformat()}) "
                              f"was used instead. "
                              f"{result['alternatives_considered']} other qualifying candidate(s) "
                              f"considered.")

                with st.expander(f"📋 All {result['candidates_tested']} departure day(s) tested"):
                    rows = []
                    for c in result.get("qualifying") or []:
                        rows.append({"date": c["date"].isoformat() if c["date"] else None,
                                    "price": c["price"], "rating": c["rating"],
                                    "status": "👉 recommended" if c is best else "qualifying"})
                    for c in result.get("rejected") or []:
                        rows.append({"date": c["date"].isoformat() if c["date"] else None,
                                    "price": c["price"], "rating": c["rating"],
                                    "status": "rejected: " + "; ".join(c["rejected_because"])})
                    st.dataframe(rows, use_container_width=True, hide_index=True)

                if result["rating_unverifiable"]:
                    st.warning("⚠️ Couldn't find a rating field on this candidate — the 8+ "
                              "rule could NOT be checked automatically. Check the hotel's "
                              "review score in Travel Compositor yourself before applying "
                              "this.")
                if result["price_unverifiable"] or current_price is None:
                    st.warning("⚠️ Couldn't find a price field to compare — the +3.5% cap "
                              "could NOT be checked automatically. Check the price in Travel "
                              "Compositor yourself before applying this.")

                st.info("👉 **This app never applies this change.** Open this package in "
                       "Travel Compositor's own edit screen, enter this departure date and "
                       "price the same way you would for any regular update, double-check "
                       "the hotel's rating, and click **Save there** — the normal, manual "
                       "step you already do today.")

    with st.expander("🔎 Raw API responses"):
        st.markdown("**Package info** (`GET /package/{micrositeId}/info/{id}`)")
        st.json(info)
        st.markdown("**Day-to-day** (`GET /package/{micrositeId}/{id}`)")
        st.json(day_to_day)
        st.markdown("**Calendar** (`GET /package/calendar/{micrositeId}/{id}`)")
        st.json(calendar)
