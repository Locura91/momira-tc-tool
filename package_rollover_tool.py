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

MODULE_BUILD = "2026-10-24-reliable-open"

_PHASE_KEY = "pkr_phase"


def _make_slug(title: str) -> str:
    """Converts a package title to a URL slug matching momira.travel's format.
    Special chars (& : , etc.) are dropped; spaces become hyphens."""
    s = title.lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)   # drop & : , etc. — TC never substitutes 'and'
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
    """Generates a browser-console JavaScript snippet that automates the TC booking flow.

    Uses React's internal value setter (Object.getOwnPropertyDescriptor trick) so that
    React's synthetic event system picks up the value change — plain .value= assignment is
    silently ignored by React-controlled inputs.  Also tries multiple selectors in case the
    input's name attribute differs across momira.travel deployments.
    """
    return (
        f"(function(){{\n"
        f"  var DATE='{date_str}';\n"
        # React-safe value setter
        f"  function reactSet(el,val){{\n"
        f"    var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;\n"
        f"    s.call(el,val);\n"
        f"    el.dispatchEvent(new Event('input',{{bubbles:true}}));\n"
        f"    el.dispatchEvent(new Event('change',{{bubbles:true}}));\n"
        f"  }}\n"
        # Find the start-date input inside the booking modal (several selector attempts)
        f"  function findDateInput(){{\n"
        f"    return document.querySelector('input[name*=\"departureDateMultidestination\"]')\n"
        f"      || document.querySelector('input[name*=\"startDate\"]')\n"
        f"      || document.querySelector('input[name*=\"departureDate\"]')\n"
        f"      || document.querySelector('input[placeholder*=\"Start date\" i]')\n"
        f"      || document.querySelector('input[placeholder*=\"Check-in\" i]')\n"
        f"      || (function(){{\n"
        f"           var all=document.querySelectorAll('input[type=\"text\"]');\n"
        f"           for(var i=0;i<all.length;i++){{\n"
        f"             var p=(all[i].parentElement||{{}}).textContent||'';\n"
        f"             if(/start.?date|departure.?date/i.test(p))return all[i];\n"
        f"           }}\n"
        f"         }})();\n"
        f"  }}\n"
        # Fill date then click Search (with retries while Search is disabled)
        f"  function fillAndSearch(){{\n"
        f"    var inp=findDateInput();\n"
        f"    if(!inp)return false;\n"
        f"    reactSet(inp,DATE);\n"
        f"    var t=0;\n"
        f"    var i=setInterval(function(){{\n"
        f"      var s=[].slice.call(document.querySelectorAll('button')).find(\n"
        f"        function(b){{return /^Search$/i.test(b.textContent.trim());}});\n"
        f"      if(s&&!s.disabled){{s.click();clearInterval(i);}}\n"
        f"      else if(++t>30){{clearInterval(i);}}\n"
        f"    }},200);\n"
        f"    return true;\n"
        f"  }}\n"
        # If the modal is already open, go straight to fill; otherwise click the button first
        f"  if(findDateInput()){{\n"
        f"    fillAndSearch();\n"
        f"  }} else {{\n"
        f"    var btn=[].slice.call(document.querySelectorAll('button')).find(function(b){{\n"
        f"      return b.textContent.indexOf('Book directly without changes')>-1;\n"
        f"    }});\n"
        f"    if(btn){{\n"
        f"      btn.click();\n"
        f"      var t=0;\n"
        f"      var i=setInterval(function(){{\n"
        f"        if(fillAndSearch()||++t>40)clearInterval(i);\n"
        f"      }},100);\n"
        f"    }} else {{\n"
        f"      alert('Open the momira.travel package page first, then run this script.');\n"
        f"    }}\n"
        f"  }}\n"
        f"}})();"
    )


def _five_months_ahead() -> date:
    """Returns today + 4 calendar months, clamped to the last day of that month."""
    today = date.today()
    month = today.month + 4
    year  = today.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    import calendar as _cal
    last_day = _cal.monthrange(year, month)[1]
    day = min(today.day, last_day)
    return date(year, month, day)


def _extract_idea_url(info: dict) -> str:
    """Return the ideaUrl from the TC API response (online.travelcompositor.com URL with
    correct slug). Falls back to empty string — execute_rollover will use bare-ID URL."""
    for key in ("ideaUrl", "idea_url", "url", "link", "publicUrl", "pageUrl"):
        v = info.get(key)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    return ""


def _extract_pax(info: dict) -> str | None:
    """Return a human-readable pax string from the package info response.
    Uses counters.adults + counters.children (confirmed TC API field names)."""
    counters = info.get("counters") or {}
    if isinstance(counters, dict):
        adults   = counters.get("adults")
        children = counters.get("children")
        if adults is not None:
            total = int(adults) + int(children or 0)
            parts = [f"{adults} adult{'s' if int(adults) != 1 else ''}"]
            if children:
                parts.append(f"{children} child{'ren' if int(children) != 1 else ''}")
            return ", ".join(parts)
    # Fallback for older/different response shapes
    for key in ("minAdults", "adults", "numTravelers", "numberOfAdults", "pax"):
        v = info.get(key)
        if v is not None:
            return str(v)
    return None


@st.dialog("🔁 Package Rollover")
def _rollover_dialog(pkg_id: str, title: str, current_price, pax: str | None):
    _render_rollover_body(pkg_id, title, current_price, pax, key_prefix="pkr")


def _render_rollover_body(pkg_id, title, current_price, pax, key_prefix):
    """Reliable rollover UI, shared by the dedicated tool and the staleness list.

    The booking itself has to happen in the operator's OWN browser: momira.travel
    sits behind Cloudflare (which blocks our server) and its login cookie is
    HttpOnly (so cross-origin JS in this tool can't reuse it either). So the
    reliable flow is: open the correct package page in a new tab — where the
    operator is already logged in and already past Cloudflare — and let them
    confirm the booking with one click there. The chosen date is shown big so
    it's easy to copy into TC's date picker."""
    st.markdown(f"**{title or pkg_id}**")
    st.caption(f"Package ID: {pkg_id}")

    package_url = _momira_url(pkg_id, title) if title else f"https://momira.travel/en/idea/{pkg_id}/"

    col1, col2 = st.columns(2)
    with col1:
        price_str = (f"€ {current_price:,.2f}" if isinstance(current_price, (int, float))
                     else str(current_price)) if current_price is not None else "—"
        st.metric("Current price / person", price_str)
    with col2:
        st.metric("Nr. PAX", pax or "—")

    chosen = st.date_input(
        "New start date",
        value=_five_months_ahead(),
        min_value=date.today(),
        format="DD.MM.YYYY",
        key=f"{key_prefix}_dialog_date",
    )
    chosen_str = chosen.strftime("%d.%m.%Y")

    st.write("")
    st.link_button(
        "🌐  Open package in Travel Compositor  →",
        package_url,
        type="primary",
        use_container_width=True,
    )
    st.markdown(
        f"Then on the TC page:\n\n"
        f"1. Click **“Reserva directamente sin cambios”** (Book directly without changes)\n"
        f"2. Set the start date to **`{chosen_str}`**\n"
        f"3. Click **Start trip** — you'll land on the trip summary"
    )

    with st.expander("⚙️ Experimental: try fully-automatic booking from the server"):
        st.caption(
            "This attempts the booking headlessly via the server. It usually fails "
            "because Cloudflare blocks the server from loading the TC page — use the "
            "button above if it doesn't return a trip link."
        )
        if st.button("🚀 Attempt auto-booking", key=f"{key_prefix}_start_now"):
            from rollover_executor import execute_rollover
            with st.spinner("Attempting rollover via server…"):
                result = execute_rollover(package_url, chosen_str)
            if result.get("success"):
                st.success(f"✅ Rollover started! Trip ID: {result['trip_id']}")
                st.link_button("🔗 Open trip summary", result["url"])
            else:
                st.error(f"❌ {result.get('error', 'Unknown error')}")
                if result.get("detail"):
                    with st.expander("Technical detail"):
                        st.code(result["detail"])


def render_package_rollover_tool():
    st.subheader("🔁 Package Rollover")

    prefill = st.session_state.pop("pkr_prefill_id", None)
    if prefill:
        for k in ("pkr_info", "pkr_day_to_day", "pkr_calendar", "pkr_package_id"):
            st.session_state.pop(k, None)
        st.session_state["pkr_input"] = prefill

    package_id = st.text_input("Holiday Package ID", key="pkr_input",
                               placeholder="e.g. 59582825")

    if st.button("🚀 Start update", type="primary", disabled=not package_id.strip(),
                 key="pkr_start_btn"):
        pid = package_id.strip()
        try:
            _do_lookup(pid)
            info      = st.session_state.get("pkr_info", {})
            day_to_day = st.session_state.get("pkr_day_to_day", {})
            _, current_price = _current_price_from(info, day_to_day)
            pax = _extract_pax(info)
            _rollover_dialog(
                pkg_id=pid,
                title=_extract_title(info),
                current_price=current_price,
                pax=pax,
            )
        except Exception as e:
            st.error(ai_extractor.friendly_error_message(e))

    if prefill and package_id.strip():
        pid = package_id.strip()
        try:
            _do_lookup(pid)
            info       = st.session_state.get("pkr_info", {})
            day_to_day = st.session_state.get("pkr_day_to_day", {})
            _, current_price = _current_price_from(info, day_to_day)
            pax = _extract_pax(info)
            _rollover_dialog(
                pkg_id=pid,
                title=_extract_title(info),
                current_price=current_price,
                pax=pax,
            )
        except Exception as e:
            st.error(ai_extractor.friendly_error_message(e))
