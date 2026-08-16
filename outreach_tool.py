"""
outreach_tool.py — the Supplier Discovery & Outreach tool, as it lives inside
the Momira Travel Platform.

Originally a React SPA (5-step wizard) talking to an Express API. The platform
runs on Streamlit, which is Python-only, so the backend services were ported
(see outreach_discovery.py and outreach_email.py, both differential-tested
against the original JavaScript) and this file replaces the whole React client.

TWO SCREENS, down from the original's five:

    original Step 1 + 2  ->  "Search"  (input, with live progress)
    original Step 3+4+5  ->  "Review & send"  (results, template, dispatch)

The original's steps 1 and 2 were separate only because discovery ran
asynchronously on a server - POST /api/search returned a sessionId immediately
and the browser polled every 2 seconds. Streamlit runs the search inline behind
a progress readout, so the polling, the session id and the whole api.js REST
client have no equivalent here.

Steps 3, 4 and 5 were then merged at the product owner's request once real
sending was verified working: reviewing the list, editing the template and
sending are one continuous task, and splitting them across screens meant
scrolling back and forth to check what you were about to send. The preview and
the dry run are still available - as an expander and an optional button - but
neither is a gate you have to pass through any more.

NOT PORTED: `server/services/db.js`, the JSON-file store that persisted
sessions/suppliers/campaigns server-side. Streamlit's session state holds the
working set, and the send log downloads as CSV, which is more portable than a
db.json only reachable over SSH. If durable campaign history is wanted later,
state_store.py (already in the platform, already SQLite) is the natural home.

SENDING SAFETY: sending is the one irreversible, externally-visible action in
the platform. The mandatory dry-run gate and the TEST_MODE_RECIPIENTS redirect
were both removed as requested, so what remains is deliberately minimal but not
nothing: the button states the exact recipient count, and a single confirmation
sits next to it. One click, no ceremony, but not something you can trigger by
misreading a screen.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-16-stop-sales-release-almost-full-sender-match"

import csv
import io
import os
import streamlit as st
import pandas as pd

import outreach_discovery as od
import outreach_email as oe
import outreach_memory as om  # new learning module
import outreach_scope as osc

_PHASE_KEY = "or_phase"


def _reset_run():
    for key in list(st.session_state.keys()):
        if key.startswith("or_"):
            del st.session_state[key]



# ============================================================================
# SCREEN 0 — WHAT SHOULD WE EVEN BE SELLING IN THIS COUNTRY?
# ============================================================================
def _render_country_scope():
    """The country's touristic map, before any supplier search.

    CONFIRMED PRODUCT-OWNER REQUEST: "the first step is a complete Country search... listing the
    most important touristic regions and... the most touristic themes according to this country.
    The goal of the presearch must be, that the first step is showing all possible touristic
    spots, that we as tour operator should have in our program."

    A keyword search only ever reports on what you thought to ask for. Search "Nile Cruise in
    Egypt" and you find Nile cruise operators - and never learn that you have nothing in Siwa, no
    Suez Canal day trip and no St Catherine's. Naming the whole board first turns an invisible gap
    into a visible tick box."""
    st.subheader("Step 1 — What should we be selling in this country?")
    st.caption("Before hunting for suppliers, look at the whole country: the regions worth having "
               "in a programme, and the kinds of product it is actually known for. Tick what you "
               "want and each combination becomes its own supplier search.")

    col1, col2 = st.columns([3, 1])
    with col1:
        country = st.text_input("Country", placeholder="e.g. Egypt", key="or_scope_country")
    with col2:
        st.write("")
        st.write("")
        if st.button("⏭️ Skip — I know what I want", key="or_scope_skip", use_container_width=True):
            st.session_state[_PHASE_KEY] = "search"
            st.rerun()

    known = osc.list_known_countries()
    if known:
        st.caption("Already researched: " + ", ".join(known) + " — those load instantly.")

    if not country.strip():
        st.info("Enter a country to see its map, or skip straight to a supplier search.")
        return

    cached = osc.get_cached_scope(country)
    bcol1, bcol2 = st.columns([1, 1])
    with bcol1:
        research = st.button("🌍 Show me this country", type="primary", key="or_scope_go")
    with bcol2:
        refresh = st.button("🔄 Research again from scratch", key="or_scope_refresh",
                            disabled=not (cached.get("places") or cached.get("themes")),
                            help="Replaces the stored list, including anything you added by hand.")

    if research or refresh:
        with st.spinner(f"Working out what {country} has to offer..."):
            st.session_state["or_scope"] = osc.suggest_country_scope(country, refresh=refresh)
        st.rerun()

    scope = st.session_state.get("or_scope") or cached
    if not scope:
        return
    if scope.get("error"):
        st.error(scope["error"])
        return
    if scope.get("from_cache"):
        st.caption("Loaded from the platform's memory — nothing was re-researched. Use **Research "
                   "again** if the country's programme has genuinely moved on.")
    if scope.get("notes"):
        st.info(scope["notes"])

    places = scope.get("places") or []
    themes = scope.get("themes") or []

    pcol, tcol = st.columns(2)
    chosen_places, chosen_themes = [], []
    with pcol:
        st.markdown(f"#### 📍 Places ({len(places)})")
        st.caption("Regions and sites a programme for this country should cover.")
        for i, place in enumerate(places):
            name = place.get("name", "")
            label = f"**{name}**" + (f" · {place['region']}" if place.get("region") else "")
            if st.checkbox(label, key=f"or_scope_place_{i}_{name}"):
                chosen_places.append(name)
            if place.get("why"):
                st.caption(place["why"])
        new_place = st.text_input("Add a place it missed", key="or_scope_new_place")
        if st.button("➕ Add place", key="or_scope_add_place", disabled=not new_place.strip()):
            osc.add_place(country, new_place.strip())
            st.session_state.pop("or_scope", None)
            st.rerun()

    with tcol:
        st.markdown(f"#### 🎯 Themes ({len(themes)})")
        st.caption("The kinds of product suppliers here actually sell.")
        for i, theme in enumerate(themes):
            name = theme.get("name", "")
            label = f"**{name}**" + (f" · {theme['where']}" if theme.get("where") else "")
            if st.checkbox(label, key=f"or_scope_theme_{i}_{name}"):
                chosen_themes.append(name)
            if theme.get("why"):
                st.caption(theme["why"])
        new_theme = st.text_input("Add a theme it missed", key="or_scope_new_theme")
        if st.button("➕ Add theme", key="or_scope_add_theme", disabled=not new_theme.strip()):
            osc.add_theme(country, new_theme.strip())
            st.session_state.pop("or_scope", None)
            st.rerun()

    st.divider()
    planned = osc.planned_searches(country, chosen_places, chosen_themes)
    if not planned:
        st.caption("Tick at least one place or theme to build a search list.")
        return

    # THE COUNT, BEFORE ANYTHING RUNS. Six places by five themes is thirty searches - several
    # minutes and a lot of API calls that nobody knowingly agreed to.
    st.markdown(f"**{len(planned)} search(es)** would run: "
                f"{len(chosen_places) or 'any'} place(s) × {len(chosen_themes) or 'any'} theme(s).")
    over_cap = len(planned) > _MAX_COMBINATIONS
    if over_cap:
        st.error(f"🚫 That's {len(planned)} combinations - only up to {_MAX_COMBINATIONS} can run at "
                 f"once. Untick some places or themes to bring it down to {_MAX_COMBINATIONS} or fewer, "
                 f"then come back for the rest in a second run.")
    elif len(planned) > 12:
        st.warning(f"That is a lot of searching and will take a while. Consider starting with the "
                   f"handful you most need, then coming back — the list is remembered.")
    with st.expander("See exactly what will be searched"):
        st.dataframe(pd.DataFrame(planned), use_container_width=True, hide_index=True)

    if st.button(f"🔎 Search suppliers for these {len(planned)} combination(s)", type="primary",
                 key="or_scope_run", disabled=over_cap):
        st.session_state["or_queue"] = planned
        st.session_state["or_queue_index"] = 0
        st.session_state[_PHASE_KEY] = "search"
        st.rerun()


# ============================================================================
# SCREEN 1 — SEARCH
# ============================================================================
# CONFIRMED RULE (product owner, 2026-08-16): "only one supplier at all, even if the supplier
# has multiple matches. We can contact each supplier only once." A country-scope run queues up
# to _MAX_COMBINATIONS place/theme searches, and the same real business routinely turns up under
# several of them (the same Nile Cruise operator matches both "Luxor" and "Aswan"). One dedupe
# pass by domain/name catches the obvious case, but two searches can also surface the SAME
# supplier under a different domain or a slightly different name (an aggregator listing on one
# side, the operator's own site on the other) - which is exactly what the email/social-based
# dedupe_suppliers_by_contact() pass already does for a single search. Running that same pass
# again across the merged cross-combination list closes that gap, so a supplier can never end up
# as two rows that both get ticked and both get an email.
_MAX_COMBINATIONS = 20
_MAX_MERGED_RESULTS = 30


def _run_queued_searches(queue):
    """Run a scope-built list of searches and merge them into one supplier list.

    Merged rather than run one at a time because the point of the country step is a single
    picture of who exists across the whole programme. Deduplicated by domain, then a second time
    by email/social across the WHOLE merged list (see _MAX_COMBINATIONS comment above) - the same
    operator legitimately turns up under Luxor/Nile Cruise and Aswan/Nile Cruise, and reviewing
    them twice is how a supplier gets emailed twice. Finally capped to _MAX_MERGED_RESULTS total,
    so a wide combination run still hands back a reviewable list rather than several hundred rows."""
    merged, seen = [], set()
    stats = {"raw": 0, "after_prefilter": 0, "final": 0, "used_mock_provider": False}
    progress = st.progress(0.0)
    status = st.empty()
    failures = []

    for index, job in enumerate(queue):
        label = " · ".join(x for x in (job.get("city"), job.get("keyword")) if x) or job["country"]
        status.caption(f"⏳ {index + 1} of {len(queue)}: {label}")
        try:
            result = od.discover_suppliers(job["country"], job.get("city", ""),
                                           job.get("keyword", "") or job["country"])
        except Exception as e:
            failures.append(f"{label}: {e}")
            progress.progress((index + 1) / len(queue))
            continue
        for key in ("raw", "after_prefilter", "final"):
            stats[key] += result["stats"].get(key, 0)
        stats["used_mock_provider"] = stats["used_mock_provider"] or result["stats"].get("used_mock_provider", False)
        for supplier in result["suppliers"]:
            domain = om.extract_domain(supplier.get("website") or supplier.get("listingUrl") or "")
            fingerprint = domain or (supplier.get("name") or "").strip().lower()
            if fingerprint and fingerprint in seen:
                continue
            if fingerprint:
                seen.add(fingerprint)
            supplier = dict(supplier)
            supplier["foundVia"] = label
            merged.append(supplier)
        progress.progress((index + 1) / len(queue))

    progress.empty()
    status.empty()
    if failures:
        st.warning("Some searches failed and were skipped:\n\n" +
                   "\n".join(f"- {f}" for f in failures))

    # Second dedupe pass, this time by email/social across every combination's results
    # together - catches the same real supplier surfacing under two different domains/names
    # in two different searches, which the per-job fingerprint pass above cannot see.
    merged = od.dedupe_suppliers_by_contact(merged)

    # Same priority the single-search path uses: a real email first, then a website, then
    # rating - so if a wide run finds more than _MAX_MERGED_RESULTS suppliers, the ones
    # actually worth reviewing survive the cut rather than whichever combination ran first.
    merged.sort(key=lambda s: (
        0 if s.get("email") else 1,
        0 if s.get("website") else 1,
        -(s["rating"] if s.get("rating") is not None else -1),
    ))
    dropped = max(0, len(merged) - _MAX_MERGED_RESULTS)
    merged = merged[:_MAX_MERGED_RESULTS]
    if dropped:
        stats["capped_at"] = _MAX_MERGED_RESULTS
        stats["dropped_over_cap"] = dropped

    return {"suppliers": merged, "stats": stats}


def _render_search():
    # A list built on the country screen, waiting to be run.
    queue = st.session_state.get("or_queue")
    if queue:
        st.subheader(f"Searching {len(queue)} place/theme combination(s)")
        st.caption("Results are merged into one list, with duplicates removed - the same operator "
                   "often appears under several of them.")
        qcol1, qcol2 = st.columns([1, 1])
        with qcol1:
            go = st.button("▶️ Run them now", type="primary", key="or_queue_run")
        with qcol2:
            if st.button("⬅️ Back to the country list", key="or_queue_back"):
                st.session_state.pop("or_queue", None)
                st.session_state[_PHASE_KEY] = "scope"
                st.rerun()
        st.dataframe(pd.DataFrame(queue), use_container_width=True, hide_index=True)
        if go:
            result = _run_queued_searches(queue)
            st.session_state.or_result = result
            st.session_state.or_session = {
                "country": queue[0]["country"],
                "city": "",
                "keyword": f"{len(queue)} place/theme combination(s)",
            }
            st.session_state.or_template = dict(oe.DEFAULT_TEMPLATE)
            st.session_state.pop("or_queue", None)
            st.session_state[_PHASE_KEY] = "review"
            st.rerun()
        return

    st.subheader("Find suppliers")
    st.caption("Searches Google, Tripadvisor, Trustpilot, Viator and GetYourGuide for well-reviewed "
               "local operators, filters out articles, listicles, forums and booking marketplaces, then "
               "looks for a direct email for each one.")

    # ---- THREE COLUMNS: Country, City (optional), Keyword ----
    col1, col2, col3 = st.columns(3)
    with col1:
        country = st.text_input("Country", placeholder="e.g. Egypt", key="or_country")
    with col2:
        city = st.text_input("City (optional)", placeholder="e.g. Cairo", key="or_city")
    with col3:
        keyword = st.text_input("What they should offer", placeholder="e.g. Nile Cruise", key="or_keyword")

    if not (os.getenv("TAVILY_API_KEY") or os.getenv("SERPAPI_API_KEY")):
        st.warning("🔍 No search API key configured (`TAVILY_API_KEY` or `SERPAPI_API_KEY`), so this runs "
                   "against clearly-labelled **mock data** — useful for trying the workflow, but the "
                   "suppliers won't be real.")

    if st.button("🔎 Find suppliers", type="primary", disabled=not (country.strip() and keyword.strip())):
        progress_box = st.empty()
        with st.spinner("Searching…"):
            try:
                result = od.discover_suppliers(
                    country.strip(),
                    city.strip(),          # <-- Pass city (may be empty)
                    keyword.strip(),
                    progress=lambda msg: progress_box.caption(f"⏳ {msg}"),
                )
            except Exception as e:
                progress_box.empty()
                st.error(f"Search failed: {e}")
                return
        progress_box.empty()
        st.session_state.or_result = result
        st.session_state.or_session = {
            "country": country.strip(),
            "city": city.strip(),
            "keyword": keyword.strip(),
        }
        st.session_state.or_template = dict(oe.DEFAULT_TEMPLATE)
        st.session_state[_PHASE_KEY] = "review"
        st.rerun()


# ============================================================================
# SCREEN 2 — REVIEW, TEMPLATE AND SEND (one continuous screen)
# ============================================================================
def _log_dataframe(log):
    return pd.DataFrame([{
        "Status": e["status"],
        "Supplier": e["supplierName"],
        "Email": e.get("email") or "",
        "Detail": e.get("reason") or e.get("subject") or e.get("messageId") or "",
        "Time": e["timestamp"],
    } for e in log])


def _render_review_and_send():
    result = st.session_state.or_result
    session = st.session_state.or_session
    template = st.session_state.or_template
    suppliers = result["suppliers"]
    stats = result["stats"]

    top1, top2 = st.columns([5, 1])
    with top1:
        # Show city if provided, otherwise fall back to country
        location = session.get("city") or session.get("country")
        st.subheader(f"{len(suppliers)} supplier(s) for {session['keyword']} in {location}")
    with top2:
        if st.button("🔎 New search", use_container_width=True):
            _reset_run()
            st.rerun()

    if stats["used_mock_provider"]:
        st.warning("⚠️ These are **mock results** — no search API key is configured. Don't email them.")

    if not suppliers:
        st.error("No suppliers survived filtering. The breakdown below shows where they dropped out.")

    # ---- Results table ----
    st.caption("Untick anyone you don't want to contact. You can also edit the **Name** or **Email** fields directly "
               "— corrections are saved back to the supplier list.")

    if suppliers:
        df = pd.DataFrame([{
            "Send": s["selected"],
            "Name": s["name"],
            "Email": s["email"] or "",
            "Website": s["website"] or "",
            "Social": s["social"] or "",
            "Listing": s["listingUrl"] or "",
            "Rating": s["rating"],
            "Why selected": s["selectionReason"],
        } for s in suppliers])

        edited = st.data_editor(
            df, use_container_width=True, hide_index=True, key="or_editor",
            column_config={
                "Send": st.column_config.CheckboxColumn("Send", help="Rows ticked here will be emailed."),
                "Name": st.column_config.TextColumn("Name", help="Editable — correct the supplier name if needed."),
                "Email": st.column_config.TextColumn("Email", help="Editable — add one the search missed."),
                "Rating": st.column_config.NumberColumn("Rating", format="%.1f"),
                "Website": st.column_config.LinkColumn("Website"),
                "Social": st.column_config.LinkColumn("Social"),
                "Listing": st.column_config.LinkColumn("Listing"),
                "Why selected": st.column_config.TextColumn("Why selected", width="large"),
            },
            # Only the read‑only columns remain disabled – Name is now editable
            disabled=["Website", "Social", "Listing", "Rating", "Why selected"],
        )

        # Fold the operator's edits back onto the real records, matched by row order.
        for i, s in enumerate(suppliers):
            if i < len(edited):
                s["selected"] = bool(edited.iloc[i]["Send"])
                s["name"] = str(edited.iloc[i]["Name"]).strip() or s["name"]  # update name
                s["email"] = (str(edited.iloc[i]["Email"]).strip() or None)

        # ---- LEARNING: Block domains of unticked suppliers ----
        st.divider()
        st.markdown("##### 🧠 Teach the system to block these in future searches")
        st.caption("If you see domains that are never useful (e.g. directories, aggregators, unrelated sites), "
                   "click the button below to add their domains to the permanent blocklist. "
                   "Future searches from **anyone** will skip them automatically.")

        # Which domains this would affect, taken from the UNTICKED rows so it follows whatever
        # the operator just did in the table above.
        already = set(om.get_blocklist())
        candidates = {}
        for s in suppliers:
            if not s["selected"]:
                url = s.get("website") or s.get("listingUrl")
                domain = om.extract_domain(url) if url else ""
                if domain and domain not in already:
                    candidates.setdefault(domain, []).append(s["name"])

        if st.session_state.get("or_block_result"):
            st.success(st.session_state.pop("or_block_result"))

        if not candidates:
            st.caption("Nothing new to block — every unticked row is either already blocked or has no "
                       "website to block.")
        else:
            # SHOW BEFORE BLOCKING. This writes to a list that every future search by every user
            # reads, so a one-click bulk action with only a count on screen is not enough: unticking
            # a row for today ("not this campaign") looks identical to rejecting it forever, and the
            # difference only surfaces months later when a supplier quietly stops appearing.
            st.caption(f"These **{len(candidates)}** domain(s) would be blocked for **everyone**, in "
                       f"**all future searches**. Untick any you only skipped for this campaign.")
            chosen = []
            for domain in sorted(candidates):
                who = ", ".join(sorted(set(candidates[domain]))[:3])
                if st.checkbox(f"`{domain}` — {who}", value=True, key=f"or_blk_{domain}"):
                    chosen.append(domain)

            if st.button(f"🧠 Block {len(chosen)} domain(s)", key="or_block_unticked",
                         disabled=not chosen):
                added = [d for d in chosen if om.add_domain_to_blocklist(d)]
                failed = [d for d in chosen if d not in added]
                parts = []
                if added:
                    parts.append(f"✅ Blocked {len(added)}: {', '.join(added)}. Future searches skip them.")
                if failed:
                    # Never report a block that didn't land. The store can be unreachable, and a
                    # false "blocked" is worse than an error, because nobody goes back to re-check.
                    parts.append(f"⚠️ NOT saved: {', '.join(failed)} — check the Memory line at the "
                                 f"bottom of the page before relying on this.")
                st.session_state["or_block_result"] = "  ".join(parts)
                st.rerun()

        # ---- Optional: Show current blocklist ----
        with st.expander("🔍 Show current blocklist"):
            # ---- Manual add: for a known-bad domain that never showed up in a search
            # result, so there's nothing to tick in the "unticked suppliers" flow above.
            # add_domain_to_blocklist() already normalizes a bare domain or a full URL
            # and rejects anything it can't extract a domain from - reuse that instead of
            # re-validating the format here, so the UI and the search filter never disagree
            # on what counts as a valid domain (see outreach_memory.py's docstring).
            ac1, ac2 = st.columns([4, 1])
            with ac1:
                new_domain = st.text_input(
                    "Add a domain to block", placeholder="e.g. example.com or https://example.com/path",
                    key="or_new_block_domain", label_visibility="collapsed")
            with ac2:
                add_clicked = st.button("➕ Add", key="or_add_block_domain")
            if add_clicked:
                typed = new_domain.strip()
                extracted = om.extract_domain(typed)
                if not extracted:
                    st.session_state["or_block_result"] = (
                        f"⚠️ Could not extract a domain from `{typed}` — enter a bare domain "
                        f"(example.com) or a full URL.")
                elif extracted in om.get_blocklist():
                    st.session_state["or_block_result"] = f"`{extracted}` is already blocked."
                elif om.add_domain_to_blocklist(typed):
                    st.session_state["or_block_result"] = (
                        f"✅ Blocked `{extracted}`. Future searches skip it.")
                else:
                    st.session_state["or_block_result"] = (
                        f"⚠️ `{extracted}` was NOT saved — check the Memory line at the bottom "
                        f"of the page before relying on this.")
                st.rerun()

            blocked = om.get_blocklist()
            if blocked:
                st.caption(f"⚠️ {len(blocked)} domain(s) are currently blocked and will never appear "
                           f"in search results for anyone.")
            if not blocked:
                st.caption("No domains blocked yet.")
            else:
                for domain in blocked:
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.write(f"`{domain}`")
                    with c2:
                        if st.button("🗑️ Remove", key=f"or_unblock_{domain}"):
                            if om.remove_domain_from_blocklist(domain):
                                st.session_state["or_block_result"] = (
                                    f"Removed `{domain}` — it can appear in searches again.")
                            else:
                                st.session_state["or_block_result"] = (
                                    f"⚠️ `{domain}` could NOT be removed — the blocklist may not have "
                                    f"been written. Check the Memory line at the bottom of the page.")
                            st.rerun()

    if stats.get("dropped_over_cap"):
        st.caption(f"ℹ️ {stats['dropped_over_cap']} additional supplier(s) were found across these "
                   f"combinations but not shown — capped at the top {stats['capped_at']} (by email, "
                   f"then website, then rating). Run fewer combinations at once to see the rest.")

    # A combination/queue run merges several searches' own stats together (see
    # _run_queued_searches) and has no single drop_log or per-stage breakdown of its own -
    # only a single search (this screen's other entry point) produces those. Every metric here
    # is read with .get() so the expander degrades to "not available for a combined run" instead
    # of a KeyError, and the same for result.get("drop_log") below.
    with st.expander(f"🔬 How the {stats.get('raw', 0)} raw results became {stats.get('final', len(suppliers))}"):
        st.caption("The original tool wrote this to a server console. It's here instead because the "
                   "distinction matters: a known operator never appearing at all is a search problem, "
                   "whereas appearing and being dropped is a filter problem.")
        if "after_vetting" in stats:
            scol1, scol2, scol3 = st.columns(3)
            scol1.metric("Raw results", stats["raw"])
            scol1.metric("Passed pre-filter", stats["after_prefilter"])
            scol2.metric("Passed vetting", stats["after_vetting"])
            scol2.metric("After merging duplicates", stats["after_dedupe"])
            scol3.metric("Dropped by AI check", stats["ai_dropped"])
            scol3.metric("Dropped: no way to contact", stats["no_contact_dropped"])
        else:
            st.caption("This breakdown is only available for a single Country/City/Keyword search - a "
                       "combination run merges several searches together, so there's no single "
                       "raw-to-final path to show.")
        drop_log = result.get("drop_log")
        if drop_log:
            st.dataframe(pd.DataFrame(drop_log), use_container_width=True, hide_index=True)
        elif "after_vetting" in stats:
            st.caption("Nothing was dropped.")

    # ---- Template ----
    st.markdown("##### Email")
    st.caption("Tags in square brackets are filled in per supplier: `[SupplierName]`, `[ContactName]`, "
               "`[Country]`, `[FocusKeyword]`, `[Website]`, `[SenderName]`. A tag with no value stays "
               "visible rather than blanking, so a typo is obvious. The HTML version is generated from "
               "this text at send time, so there's only one body to edit.")
    template["subject"] = st.text_input("Subject", value=template["subject"], key="or_subject")
    template["textBody"] = st.text_area("Body", value=template["textBody"], height=320, key="or_body")

    selected = [s for s in suppliers if s.get("selected")]
    sendable = [s for s in selected if s.get("email")]

    if selected:
        with st.expander(f"👀 Preview as {selected[0]['name']} would receive it"):
            msg = oe.build_message(selected[0], session, template)
            st.caption(f"**To:** {', '.join(msg['to']) or '(no address)'}  ·  **From:** {msg['from']}")
            st.caption(f"**Subject:** {msg['subject']}")
            st.text(msg["text"])

    # ---- Send ----
    st.divider()
    st.markdown("##### Send")
    status = oe.verify_transport()
    provider = status["provider"]

    scol1, scol2 = st.columns([3, 2])
    with scol1:
        if provider == "demo":
            st.warning("📭 **Demo mode** — no email provider configured, so nothing is delivered. "
                       "Set `RESEND_API_KEY` (recommended) or the `SMTP_*` values to send for real.")
        elif not status["ok"]:
            st.error(f"❌ **{provider.upper()} is configured but not working:** {status.get('error')}")
        else:
            st.success(f"✅ Sending via **{provider.upper()}** from **{oe.get_from_address()}**")
    with scol2:
        pdf = oe.get_pdf_status()
        if pdf["attached"]:
            st.caption(f"📎 Company profile PDF attached ({pdf['sizeKb']} KB).")
        else:
            # A warning, not a caption: a missing attachment changes what the supplier
            # receives while nothing about the send looks wrong, so it's the kind of thing
            # only noticed after a batch has gone out. (Real case: the first live send went
            # without the profile because the PDF wasn't in the deployment.)
            st.warning(f"📎 **No PDF found — sending without an attachment.** "
                       f"Place it at `{pdf['path']}`.")

    st.caption(f"**{len(selected)}** selected · **{len(sendable)}** with an email address · "
               f"**{len(selected) - len(sendable)}** would be skipped")

    if sendable:
        with st.expander(f"📋 The {len(sendable)} address(es) that would receive this"):
            st.dataframe(pd.DataFrame([{"Supplier": s["name"], "Email": s["email"]} for s in sendable]),
                         use_container_width=True, hide_index=True)

    if not sendable:
        st.info("No selected supplier has an email address yet — tick a row and add one above.")
        return

    if provider != "demo" and status["ok"]:
        st.caption(f"This sends real email from {oe.get_from_address()} to {len(sendable)} external "
                   f"companies and cannot be recalled.")

    ccol1, ccol2 = st.columns([2, 3])
    with ccol1:
        confirmed = st.checkbox(f"Yes, send to {len(sendable)}", key="or_confirm_real")
    with ccol2:
        send_clicked = st.button(f"📨 Send to {len(sendable)} supplier(s)", type="primary",
                                 disabled=not confirmed, use_container_width=True)

    # Optional preview run - available, never required.
    if st.button("🧪 Dry run instead (builds everything, sends nothing)"):
        st.session_state.or_dry_log = oe.dispatch_batch(selected, session, template, dry_run=True)
        st.rerun()

    if send_clicked:
        progress_box = st.empty()
        live = []

        def on_progress(entry):
            live.append(entry)
            progress_box.caption(f"📤 {len(live)}/{len(selected)} — {entry['supplierName']}: {entry['status']}")

        with st.spinner("Sending…"):
            st.session_state.or_send_log = oe.dispatch_batch(selected, session, template,
                                                              on_progress=on_progress, dry_run=False)
        progress_box.empty()
        st.session_state.pop("or_dry_log", None)
        st.rerun()

    dry_log = st.session_state.get("or_dry_log")
    if dry_log:
        would = sum(1 for e in dry_log if e["status"] == "would_send")
        skipped = sum(1 for e in dry_log if e["status"] == "skipped")
        st.info(f"Dry run — **{would}** would send, **{skipped}** skipped. Nothing was sent.")
        st.dataframe(_log_dataframe(dry_log), use_container_width=True, hide_index=True)

    send_log = st.session_state.get("or_send_log")
    if send_log:
        sent = sum(1 for e in send_log if e["status"] == "sent")
        skipped = sum(1 for e in send_log if e["status"] == "skipped")
        failed = sum(1 for e in send_log if e["status"] == "failed")
        if failed:
            st.warning(f"Finished — **{sent}** sent, **{skipped}** skipped, **{failed}** failed. "
                       f"Failures are per-recipient; the rest of the batch still went out.")
        else:
            st.balloons()
            st.success(f"🎉 Finished — **{sent}** sent, **{skipped}** skipped.")
        st.dataframe(_log_dataframe(send_log), use_container_width=True, hide_index=True)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["status", "supplierName", "email", "reason",
                                                  "messageId", "timestamp"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(send_log)
        # Build filename with city if available
        location = session.get("city") or session.get("country")
        filename = f"outreach-{location}-{session['keyword']}.csv".replace(" ", "-")
        st.download_button("⬇️ Download send log (CSV)", buf.getvalue(),
                           file_name=filename,
                           mime="text/csv")


# ============================================================================
# ENTRY POINT
# ============================================================================
def render_outreach_tool():
    """Supplier discovery, vetting and outreach. Finds well-reviewed local operators,
    lets a human curate the list, and emails the approved ones."""
    if _PHASE_KEY not in st.session_state:
        st.session_state[_PHASE_KEY] = "scope"

    if st.session_state[_PHASE_KEY] == "scope":
        _render_country_scope()
    elif st.session_state[_PHASE_KEY] == "search":
        _render_search()
    else:
        _render_review_and_send()