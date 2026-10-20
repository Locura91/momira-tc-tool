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
import re
from datetime import date

import streamlit as st

import ai_extractor
import package_rollover_rules as prr
import package_service_compare as psc
from travelcompositor_api import TravelCompositorAPI

MODULE_BUILD = "2026-10-20-fix-memory-widget-key"
MODULE_BUILD = "2026-10-20-fix-memory-widget-key"

_PHASE_KEY = "pkr_phase"


def _make_slug(title: str) -> str:
    """Converts a package title to a URL slug matching momira.travel's format."""
    s = title.lower()
    s = re.sub(r'[&]', 'and', s)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    return re.sub(r'-+', '-', s)


def _momira_url(pkg_id: str, title: str) -> str:
    return f"https://momira.travel/en/idea/{pkg_id}/{_make_slug(title)}"


def _extract_title(info: dict) -> str:
    raw = (info.get("title") or info.get("name") or info.get("description") or "")
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("text") or ""
    return str(raw).strip()


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


_COMPONENT_LABELS = {
    "flight":     "✈️ Flights",
    "hotel":      "🏨 Hotels",
    "transfer":   "🚐 Transfers",
    "ticket":     "🎟️ Tickets",
    "transport":  "🚌 Transports",
    "closedtour": "🛳️ Closed Tours",
}


def _render_service_comparison(cmp: dict):
    """Renders the psc.compare_components() result as a clear match/mismatch table."""
    all_match = cmp.get("all_match", False)
    unknown   = cmp.get("unknown_fields", False)

    if all_match and not unknown:
        st.success("✅ All services match — flights (incl. luggage), hotels (incl. meal type), "
                   "transfers, tickets, transports and closed tours are identical.")
    elif all_match and unknown:
        st.warning("⚠️ Services appear to match, but some component field names could not be "
                   "read from the API response — see the detail below to confirm.")
    else:
        st.error("❌ One or more services differ — auto-update not safe. Review the detail below.")

    if unknown:
        st.caption(
            "ℹ️ Some field names are not yet confirmed against a real API response. "
            "The 'matched field' column shows exactly which key was used — check it "
            "makes sense for this package, and report anything unexpected."
        )

    for ctype, label in _COMPONENT_LABELS.items():
        comp = cmp["components"].get(ctype, {})
        match  = comp.get("match", False)
        reason = comp.get("reason", "")
        cur    = comp.get("current", [])
        prop   = comp.get("proposed", [])
        icon = "✅" if match else "❌"

        with st.expander(f"{icon} {label}  —  {reason}", expanded=not match):
            if not cur and not prop:
                st.caption("Not present in either current or proposed.")
                continue

            cols = st.columns(2)
            for col, side_label, side_list in [
                (cols[0], "Current package", cur),
                (cols[1], "Proposed departure", prop),
            ]:
                with col:
                    st.markdown(f"**{side_label}** ({len(side_list)} item(s))")
                    for item in side_list:
                        id_field = item.get("id_field") or "*(no id field found)*"
                        id_val   = item.get("id_value") or "*(unknown)*"
                        lines = [f"- **{id_val}** *(field: `{id_field}`)*"]
                        if "meal_value" in item:
                            mf = item.get("meal_field") or "*(no meal field found)*"
                            mv = item.get("meal_value") or "*(unknown)*"
                            lines.append(f"  Meal: **{mv}** *(field: `{mf}`)*")
                        if "baggage_value" in item:
                            bf = item.get("baggage_field") or "*(no baggage field found)*"
                            bv = item.get("baggage_value") or "*(unknown)*"
                            lines.append(f"  Baggage: **{bv}** *(field: `{bf}`)*")
                        st.markdown("\n".join(lines))
                    if not side_list:
                        st.caption("None")


def _make_rollover_script(date_str: str) -> str:
    """Generates a browser-console JavaScript snippet that automates the TC booking flow."""
    return (
        f"(function(){{\n"
        f"  var d='{date_str}';\n"
        f"  var btn=[].slice.call(document.querySelectorAll('button')).find(function(b){{\n"
        f"    return b.textContent.indexOf('Book directly without changes')>-1;\n"
        f"  }});\n"
        f"  function go(){{\n"
        f"    var inp=document.querySelector('input[name*=\"departureDateMultidestination\"]');\n"
        f"    if(!inp)return false;\n"
        f"    inp.value=d;\n"
        f"    inp.dispatchEvent(new Event('input',{{bubbles:true}}));\n"
        f"    inp.dispatchEvent(new Event('change',{{bubbles:true}}));\n"
        f"    setTimeout(function(){{\n"
        f"      var s=[].slice.call(document.querySelectorAll('button')).find(\n"
        f"        function(b){{return b.textContent.trim()==='Search';}});\n"
        f"      if(s)s.click();\n"
        f"    }},900);\n"
        f"    return true;\n"
        f"  }}\n"
        f"  if(btn){{btn.click();var t=0;var i=setInterval(function(){{\n"
        f"    if(go()||++t>30)clearInterval(i);\n"
        f"  }},100);}}\n"
        f"  else if(!go()){{alert('Open the Momira.travel package page first.');}}\n"
        f"}})();"
    )


def _render_open_in_tc(pkg_id: str, title: str, proposed_date: date):
    """Editable date + single-click start-update flow."""
    st.divider()
    st.markdown("##### 🚀 Apply the rollover in Travel Compositor")

    chosen = st.date_input(
        "New departure date (adjust if needed)",
        value=proposed_date,
        key="pkr_chosen_date",
        format="DD.MM.YYYY",
        min_value=date.today(),
    )
    chosen_str = chosen.strftime("%d.%m.%Y")

    url = _momira_url(pkg_id, title) if title else f"https://momira.travel/en/idea/{pkg_id}/"
    script = _make_rollover_script(chosen_str)

    import json as _json
    import streamlit.components.v1 as _components

    # components.v1.html() renders in a sandboxed iframe that includes allow-popups,
    # so window.open() and navigator.clipboard both work from inside it.
    # Script and URL are stored in JS variables (not inline) to avoid HTML-escaping issues.
    _components.html(
        f"""
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
  .btn{{display:block;width:100%;padding:.7rem 1rem;background:#e53e3e;color:#fff;
        border:none;border-radius:8px;font-size:1rem;font-weight:700;cursor:pointer;
        text-align:center;box-sizing:border-box;}}
  .btn:hover{{background:#c53030}}
  .hint{{font-size:.82rem;color:#374151;margin:.5rem 0 0;line-height:1.5}}
  .hint b{{color:#111}}
</style>
<script>
var TC_SCRIPT = {_json.dumps(script)};
var TC_URL    = {_json.dumps(url)};
function startUpdate() {{
  navigator.clipboard.writeText(TC_SCRIPT).catch(function(){{}});
  window.open(TC_URL, '_blank');
}}
</script>
<button class="btn" onclick="startUpdate()">🚀 Start update</button>
<p class="hint">
  After the page opens: <b>F12 → Console tab → Ctrl+V → Enter</b>
  — the script is already on your clipboard.
</p>
""",
        height=95,
    )

    st.caption(
        "The script clicks **Book directly without changes**, fills in the date, clicks Search — "
        "you land on the summary page. Review the services, then click **Override existing trip**. "
        "⚠️ You must be logged in as operator on momira.travel."
    )


def render_package_rollover_tool():
    st.subheader("🔁 Package Rollover")
    st.caption(
        "Looks up a Holiday Package, checks whether it needs rolling (departure < 14 days out), "
        "proposes a new departure ~4 months ahead, and gives you a one-paste script to apply "
        "it in Travel Compositor — you only need to review the summary and click Override."
    )

    # Pre-fill support: the staleness list sets pkr_prefill_id when the human clicks
    # "Open in Package Rollover" from that screen, so the ID arrives ready to look up.
    prefill = st.session_state.pop("pkr_prefill_id", None)
    auto_lookup = False
    if prefill:
        # Clear any previous lookup result so we fetch fresh data for this package
        for k in ("pkr_info", "pkr_day_to_day", "pkr_calendar", "pkr_package_id"):
            st.session_state.pop(k, None)
        st.session_state["pkr_input"] = prefill
        auto_lookup = True

    package_id = st.text_input("Holiday Package ID", key="pkr_input",
                               placeholder="e.g. 59582825")

    if auto_lookup and package_id.strip():
        try:
            _do_lookup(package_id.strip())
        except Exception as e:
            st.error(ai_extractor.friendly_error_message(e))
    elif st.button("🔍 Look up package", type="primary", disabled=not package_id.strip()):
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
            st.warning("⚠️ **If this package includes a fixed-departure component** (e.g. a "
                      "cruise or coach tour that only departs on specific weekdays, like a "
                      "Nile cruise), this proposed date is NOT checked against that schedule — "
                      "confirm it matches before applying it in Travel Compositor.")
            _render_open_in_tc(
                pkg_id=st.session_state.get("pkr_package_id", ""),
                title=_extract_title(info),
                proposed_date=dyn["proposed_date"],
            )
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

                # ---- Service comparison (flights, hotels+meal, transfers, tickets,
                # transports, closed tours) — required before any auto-update ----
                st.markdown("##### 🔍 Service comparison (current vs. proposed)")
                st.caption(
                    "All six component types must match exactly before an auto-update "
                    "is safe. Flights must include the same luggage; hotels must have "
                    "the same meal type."
                )
                cmp = psc.compare_components(day_to_day, best.get("raw", {}))
                _render_service_comparison(cmp)

                _render_open_in_tc(
                    pkg_id=st.session_state.get("pkr_package_id", ""),
                    title=_extract_title(info),
                    proposed_date=best["date"],
                )

    with st.expander("🔎 Raw API responses"):
        st.markdown("**Package info** (`GET /package/{micrositeId}/info/{id}`)")
        st.json(info)
        st.markdown("**Day-to-day** (`GET /package/{micrositeId}/{id}`)")
        st.json(day_to_day)
        st.markdown("**Calendar** (`GET /package/calendar/{micrositeId}/{id}`)")
        st.json(calendar)
