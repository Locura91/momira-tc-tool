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
scrolling back and forth to check what you were about to send. The preview
(the "addresses that would receive this" expander) is still available, but
isn't a gate you have to pass through any more.

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

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "remove the button Dry run at the supplier
outreach, I don't need it any more." The standalone "🧪 Dry run instead" button (and the
"Dry run - N would send, N skipped" result panel it populated) is gone from
_render_review_and_send() - the "addresses that would receive this" expander above the Send
button already covers the same "see before you send" need without a second button. dispatch_batch
itself still supports dry_run=True (oe.dispatch_batch's own signature/docstring) - only this UI's
button to reach it was removed, not the underlying capability, in case a future screen or a
script still wants it.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-01-audit-critical-3-4-fix"

import csv
import io
import os
import uuid
import streamlit as st
import pandas as pd

import outreach_discovery as od
import outreach_email as oe
import outreach_followups as ofw
import outreach_learned_suppliers as oln  # remembers suppliers added by hand - see its own docstring
import outreach_memory as om  # domain blocklist
import outreach_scope as osc

_PHASE_KEY = "or_phase"


def _mark_already_contacted(suppliers):
    """CONFIRMED FIX (2026-08-19 audit): the existing dedupe passes (_merge_one_job_result's
    fingerprint check, dedupe_suppliers_by_contact) only ever compare suppliers WITHIN the
    current search/queue run. outreach_followups.py has held a durable, cross-session send
    history since the follow-up-reminders round, but nothing checked it during discovery - so
    a supplier found again next month under a slightly different name/domain could get a
    second cold-outreach email, contradicting "we can contact each supplier only once" (see
    the _PER_COMBINATION_RESULTS block above).

    Pre-unticks (does not remove) any result whose email was already sent to before, and tags
    it so the review table can show why. A human can still deliberately re-tick a row - this
    is a safety default, not a hard block, the same posture the rest of this screen takes
    everywhere else a human makes the final call."""
    contacted_emails = {row["email"] for row in ofw.list_all_sends() if row.get("email")}
    if not contacted_emails:
        return suppliers
    for supplier in suppliers:
        email = (supplier.get("email") or "").strip().lower()
        if email and email in contacted_emails:
            supplier["alreadyContacted"] = True
            supplier["selected"] = False
    return suppliers


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

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): a nudge back to whoever was emailed a while
    # ago with no reply logged yet, surfaced right where a session naturally starts rather than
    # buried behind a menu the operator has to remember exists.
    due = ofw.pending_followups()
    if due:
        if st.button(f"📋 {len(due)} follow-up(s) due — suppliers emailed a while ago with no "
                    f"reply logged yet", key="or_followups_nav"):
            st.session_state[_PHASE_KEY] = "followups"
            st.rerun()

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

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-27): "group the possible and smart themes with
    # the correct Places already... If we group them: we match automatically the needed service
    # and we limitate the useless search." Themes are matched to the places they're actually
    # sold at (by outreach_scope's AI prompt), so each place only ever shows its own fitting
    # themes - no "Snorkeling" under Cairo. `pairs` collects the explicit (place, theme)
    # combinations ticked, replacing the old blind place x theme cross product.
    per_place, countrywide = osc.group_themes_by_place(places, themes)
    pairs = []

    st.markdown(f"#### 📍 Places & their themes ({len(places)})")
    st.caption("Open a place to see only the themes it's actually known for. Tick a theme to "
               "search for it there, or tick the place alone for a general supplier search.")
    for slot in per_place:
        place = slot["place"]
        name = place.get("name", "")
        place_themes = slot["themes"]
        theme_keys = [f"or_scope_pt_{name}_{j}_{t.get('name', '')}"
                     for j, t in enumerate(place_themes)]
        header = f"📍 {name}" + (f" · {place['region']}" if place.get("region") else "")
        with st.expander(f"{header}  —  {len(place_themes)} theme(s)"):
            if place.get("why"):
                st.caption(place["why"])
            if st.checkbox("Just search this place (no specific theme)",
                           key=f"or_scope_place_only_{name}"):
                pairs.append((name, ""))
            if not place_themes:
                st.caption("No theme from the list is matched to this place yet — add one "
                           "below, or use the general search above.")
            else:
                # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-27): "one click to automatically
                # include all themes in one search. But still, the human shall be able to
                # unmark single place & theme combinations." Pre-checks every theme box for
                # this place by writing True into each checkbox's own session_state key before
                # it renders - the individual checkboxes below are untouched, so any one of
                # them can still be unticked afterwards without affecting the others.
                if st.button(f"✅ Select all {len(place_themes)} theme(s) for {name}",
                            key=f"or_scope_select_all_{name}"):
                    for k in theme_keys:
                        st.session_state[k] = True
                    st.rerun()
            for j, theme in enumerate(place_themes):
                tname = theme.get("name", "")
                if st.checkbox(tname, key=theme_keys[j]):
                    pairs.append((name, tname))
                if theme.get("why"):
                    st.caption(theme["why"])
    new_place = st.text_input("Add a place it missed", key="or_scope_new_place")
    if st.button("➕ Add place", key="or_scope_add_place", disabled=not new_place.strip()):
        osc.add_place(country, new_place.strip())
        st.session_state.pop("or_scope", None)
        st.rerun()

    st.markdown(f"#### 🌐 Country-wide themes ({len(countrywide)})")
    st.caption("Not tied to one place — sold, or worth searching for, across the whole "
               "country (e.g. Airport Transfer, Custom Private Tour).")
    for i, theme in enumerate(countrywide):
        name = theme.get("name", "")
        label = f"**{name}**" + (f" · {theme['where']}" if theme.get("where") else "")
        if st.checkbox(label, key=f"or_scope_cw_{i}_{name}"):
            pairs.append(("", name))
        if theme.get("why"):
            st.caption(theme["why"])
    new_theme = st.text_input("Add a theme it missed", key="or_scope_new_theme")
    if st.button("➕ Add theme", key="or_scope_add_theme", disabled=not new_theme.strip()):
        osc.add_theme(country, new_theme.strip())
        st.session_state.pop("or_scope", None)
        st.rerun()

    st.divider()
    planned = osc.planned_searches(country, pairs)
    if not planned:
        st.caption("Tick at least one place or theme to build a search list.")
        return

    # THE COUNT, BEFORE ANYTHING RUNS.
    # CONFIRMED RULE (product owner, 2026-08-16): no cap on how many combinations can run at
    # once - a prior round added a hard block at 20, and the product owner asked for it to be
    # removed. Speed comes from _process_one_queued_job passing max_results=1 to each combination's
    # own search instead (one AI-verification call and one website fetch per combination rather
    # than up to _max_candidates() of each), not from limiting how many combinations run.
    st.markdown(f"**{len(planned)} search(es)** would run, one per ticked place/theme "
                f"combination. Each combination looks for its single best-reviewed supplier, so "
                f"even a large list runs at one search's worth of time per combination.")
    if len(planned) > 30:
        st.info(f"{len(planned)} combinations queued. This still takes a while purely from the "
                f"number of searches - you can watch progress once it starts, and the list is "
                f"remembered if you want to come back to it.")
    with st.expander("See exactly what will be searched"):
        st.dataframe(pd.DataFrame(planned), use_container_width=True, hide_index=True)

    if st.button(f"🔎 Search suppliers for these {len(planned)} combination(s)", type="primary",
                 key="or_scope_run"):
        st.session_state["or_queue"] = planned
        st.session_state["or_queue_index"] = 0
        st.session_state[_PHASE_KEY] = "search"
        st.rerun()


# ============================================================================
# SCREEN 1 — SEARCH
# ============================================================================
# CONFIRMED RULE (product owner, 2026-08-16): "only one supplier at all, even if the supplier
# has multiple matches. We can contact each supplier only once." A country-scope run queues
# every place/theme combination the operator ticked - no cap on the count (see the note in
# _render_country_scope) - and the same real business routinely turns up under several of them
# (the same Nile Cruise operator matches both "Luxor" and "Aswan"). One dedupe pass by
# domain/name catches the obvious case, but two searches can also surface the SAME supplier under
# a different domain or a slightly different name (an aggregator listing on one side, the
# operator's own site on the other) - which is exactly what the email/social-based
# dedupe_suppliers_by_contact() pass already does for a single search. Running that same pass
# again across the merged cross-combination list closes that gap, so a supplier can never end up
# as two rows that both get ticked and both get an email.
#
# CONFIRMED RULE (product owner, 2026-08-16): "search per each combination only one supplier, so
# the search is faster." _PER_COMBINATION_RESULTS=1 was passed to discover_suppliers so each
# combination's own AI-verification and website-enrichment work (the slow parts) only ever ran
# on its single best-rated candidate instead of up to _max_candidates() of them - see that
# function's max_results docstring for exactly where the time is saved.
#
# CHANGED (2026-08-26, CONFIRMED PRODUCT-OWNER REQUEST): "the results are very bad" - one
# candidate per combination meant one bad/mismatched pick sank that whole combination with
# nothing to fall back to. Raised to 3. Cost check before raising this: AI verification is
# ALREADY one batched call per combination covering every surviving candidate together (see
# verify_candidates), not one call per candidate, so this does not multiply that cost. Only
# per-candidate website enrichment scales with this number, and it already runs concurrently
# (ThreadPoolExecutor, see discover_suppliers) rather than one at a time - so 3 candidates costs
# noticeably less than 3x the wall-clock time of 1, not the full 3x a naive read of the old
# comment above would suggest. Revisit upward again if 3 still feels thin, but each step up
# does add some real time and more rows to review per combination.
_PER_COMBINATION_RESULTS = 3
_MAX_MERGED_RESULTS = 30


def _merge_one_job_result(merged, seen, stats, label, result, drop_log=None):
    """Fold one combination's search result into the running merged list/seen-set/stats -
    pulled out as its own function so it's the same code whether a full queue runs straight
    through or is processed one job per Streamlit rerun (see _process_one_queued_job).

    CONFIRMED REAL GAP (product owner report, 2026-08-25: "the results for South Korea were
    actually very bad - no local DMC and no local tour guide at all"): a Country Scope run
    (many combinations) used to only merge `raw`/`after_prefilter`/`final` and drop every
    combination's own `drop_log` on the floor - the "How N raw results became M" breakdown that
    already answers exactly this question ("did the search find nothing, or find things and
    filter them out?") was only ever populated for a single Country/City/Keyword search, per the
    UI's own `if "after_vetting" in stats` check in _render_review_and_send(). A bad-results
    report from the combination flow (the flow the product owner's own 2026-08-16 request made
    the default entry point) was previously undiagnosable - there was no way to tell a real
    search-recall problem (the provider found nothing) from an over-aggressive filter (found
    real businesses, rejected them) without re-running as a single search instead. Now every
    combination's full stats and drop_log merge in, so the same breakdown lights up here too."""
    for key in ("raw", "after_prefilter", "after_vetting", "after_dedupe",
                "ai_dropped", "no_contact_dropped", "final", "provider_error_count",
                "remembered_available", "remembered_added"):
        stats[key] = stats.get(key, 0) + result["stats"].get(key, 0)
    stats["used_mock_provider"] = stats["used_mock_provider"] or result["stats"].get("used_mock_provider", False)
    # CONFIRMED REAL INCIDENT (2026-08-25): a Morocco run came back "0 raw results" across all
    # 40 combinations - which reads as "no suppliers exist for Morocco" but can just as easily
    # mean every single search call failed (bad/expired API key, rate limit, network issue) -
    # see outreach_discovery._run_provider_search_with_diagnostics' own docstring. Keep the
    # FIRST error message seen across the whole run as a representative sample, not every one -
    # 40 combinations x up to 10 queries each failing identically would otherwise flood this
    # into an unreadable wall of repeated text.
    if not stats.get("provider_error_sample"):
        stats["provider_error_sample"] = result["stats"].get("provider_error_sample")
    if drop_log is not None:
        for entry in result.get("drop_log") or []:
            tagged = dict(entry)
            tagged["combination"] = label
            drop_log.append(tagged)
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


def _finalize_queue_result(merged, stats, failures, drop_log=None, stopped_early=False, searched=0, total=0):
    """The second dedupe pass + cap + reporting, run once after every job that's GOING to run
    has run - whether that's the whole queue or however much got through before Stop was
    pressed. CONFIRMED RULE (product owner, 2026-08-16): "give the human one button that says
    'Stop the search' and give the human all results found until then" - a stopped run is not
    an error case, it goes through the exact same finishing steps a completed one does."""
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
    if stopped_early:
        stats["stopped_early"] = True
        stats["searched"] = searched
        stats["total_planned"] = total

    return {"suppliers": merged, "stats": stats, "drop_log": drop_log or []}


def _init_queue_run(queue):
    """Start a combination run. State lives in session_state, not a local variable, because
    the run is processed ONE JOB PER RERUN (see _process_one_queued_job) so the 'Stop the
    search' button has an actual gap to be clicked in - a plain Python for-loop over 40
    combinations blocks the whole script and Streamlit cannot service a button click until it
    returns, so a for-loop can never be interrupted once started."""
    st.session_state.or_queue_running = True
    st.session_state.or_queue_full = queue
    st.session_state.or_queue_pos = 0
    st.session_state.or_queue_merged = []
    st.session_state.or_queue_seen = set()
    st.session_state.or_queue_stats = {
        "raw": 0, "after_prefilter": 0, "after_vetting": 0, "after_dedupe": 0,
        "ai_dropped": 0, "no_contact_dropped": 0, "final": 0, "used_mock_provider": False,
        "provider_error_count": 0, "provider_error_sample": None,
        "remembered_available": 0, "remembered_added": 0,
    }
    st.session_state.or_queue_drop_log = []
    st.session_state.or_queue_failures = []
    st.session_state.or_queue_stopped = False


def _process_one_queued_job():
    """Run exactly one combination's search and fold it into the running state, then advance.
    Called once per rerun while or_queue_running is True - see _init_queue_run for why."""
    queue = st.session_state.or_queue_full
    pos = st.session_state.or_queue_pos
    job = queue[pos]
    label = " · ".join(x for x in (job.get("city"), job.get("keyword")) if x) or job["country"]
    try:
        result = od.discover_suppliers(job["country"], job.get("city", ""),
                                       job.get("keyword", "") or job["country"],
                                       max_results=_PER_COMBINATION_RESULTS)
        _merge_one_job_result(st.session_state.or_queue_merged, st.session_state.or_queue_seen,
                              st.session_state.or_queue_stats, label, result,
                              drop_log=st.session_state.or_queue_drop_log)
    except Exception as e:
        st.session_state.or_queue_failures.append(f"{label}: {e}")
    st.session_state.or_queue_pos = pos + 1
    if st.session_state.or_queue_pos >= len(queue):
        st.session_state.or_queue_running = False


def _render_search():
    # A running combination search - checked FIRST, before the not-yet-started queue below,
    # so a rerun mid-run lands straight back here instead of re-showing the queue's own
    # "Run them now" button.
    if st.session_state.get("or_queue_running"):
        total = len(st.session_state.or_queue_full)
        pos = st.session_state.or_queue_pos
        st.subheader(f"Searching {total} place/theme combination(s)")
        current_job = st.session_state.or_queue_full[pos] if pos < total else None
        current_label = (" · ".join(x for x in (current_job.get("city"), current_job.get("keyword"))
                                    if x) or current_job["country"]) if current_job else ""
        st.progress(pos / total if total else 0.0,
                   text=f"⏳ {pos} of {total} searched" + (f" — now: {current_label}" if current_label else ""))
        st.caption(f"{len(st.session_state.or_queue_merged)} supplier(s) found so far.")
        # CONFIRMED RULE (product owner, 2026-08-16): "give the human one button that says
        # 'Stop the search' and give the human all results found until then." Checked BEFORE
        # processing the next job, and this run must not call _process_one_queued_job/rerun
        # again once pressed - both would keep the loop going for another combination first.
        if st.button("⏹️ Stop the search — show me what's found so far", key="or_queue_stop"):
            st.session_state.or_queue_running = False
            st.session_state.or_queue_stopped = True
        else:
            _process_one_queued_job()

        # Checked AFTER acting, not before: processing the queue's LAST job also sets
        # or_queue_running False by itself (see _process_one_queued_job), and that finish has
        # to be handled right here too - not just the Stop-button path - or the run would call
        # st.rerun() one more time and land back at the top of this function with
        # or_queue_running already False, skipping the finalize step below entirely and
        # stranding the results that were just found.
        if st.session_state.or_queue_running:
            st.rerun()

        # Either the queue ran out on its own, or Stop was just pressed - both finish the same
        # way, through the exact same dedupe/cap/report steps.
        result = _finalize_queue_result(
            st.session_state.or_queue_merged, st.session_state.or_queue_stats,
            st.session_state.or_queue_failures,
            drop_log=st.session_state.or_queue_drop_log,
            stopped_early=st.session_state.get("or_queue_stopped", False),
            searched=st.session_state.or_queue_pos, total=total,
        )
        result["suppliers"] = _mark_already_contacted(result["suppliers"])
        st.session_state.or_result = result
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-30): "the App can learn which suppliers are
        # needed" from a manual add - see outreach_learned_suppliers.py. A merged Country-Scope
        # run has no single (country, theme) to attribute a hand-added supplier to (that's
        # exactly why "keyword" below collapses to a vague summary string), so every DISTINCT
        # combination actually searched in this run is kept here instead - bounded to what was
        # really searched, not the whole country's theme list, and not a blind guess either.
        seen_combos = set()
        combinations = []
        for job in st.session_state.or_queue_full:
            combo_key = (job["country"].strip().lower(), (job.get("keyword") or "").strip().lower())
            if combo_key in seen_combos:
                continue
            seen_combos.add(combo_key)
            combinations.append({"country": job["country"], "keyword": job.get("keyword") or ""})
        st.session_state.or_session = {
            "country": st.session_state.or_queue_full[0]["country"],
            "city": "",
            "keyword": f"{total} place/theme combination(s)",
            "combinations": combinations,
        }
        st.session_state.or_template = dict(oe.DEFAULT_TEMPLATE)
        for key in ("or_queue_running", "or_queue_full", "or_queue_pos", "or_queue_merged",
                   "or_queue_seen", "or_queue_stats", "or_queue_failures", "or_queue_stopped"):
            st.session_state.pop(key, None)
        st.session_state.pop("or_queue", None)
        st.session_state[_PHASE_KEY] = "review"
        st.rerun()
        return

    # A list built on the country screen, waiting to be run.
    queue = st.session_state.get("or_queue")
    if queue:
        st.subheader(f"Searching {len(queue)} place/theme combination(s)")
        st.caption("Results are merged into one list, with duplicates removed - the same operator "
                   "often appears under several of them. Once running, a **Stop the search** "
                   "button lets you cut it short and keep whatever's been found up to that point.")
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "the human shall be able to edit the
        # list and remove single combinations. Because some combinations are not really needed
        # or double requested." A plain st.dataframe is read-only, so this is a data_editor with
        # num_rows="dynamic" instead - select a row and press the trash icon (or Delete on the
        # keyboard) to drop it before running. Nothing here touches the checkboxes back on the
        # country screen; it only edits the list that's about to run.
        st.caption("Don't need one of these? Select its row below and remove it - only what's "
                   "left in the table runs.")
        edited_df = st.data_editor(pd.DataFrame(queue), use_container_width=True, hide_index=True,
                                   num_rows="dynamic", key="or_queue_editor")
        run_queue = [row for row in edited_df.to_dict("records") if str(row.get("country", "")).strip()]
        removed = len(queue) - len(run_queue)
        if removed:
            st.caption(f"{removed} combination(s) removed from this run - {len(run_queue)} will search.")
        qcol1, qcol2 = st.columns([1, 1])
        with qcol1:
            go = st.button(f"▶️ Run them now" + (f" ({len(run_queue)})" if removed else ""),
                          type="primary", key="or_queue_run", disabled=not run_queue)
        with qcol2:
            if st.button("⬅️ Back to the country list", key="or_queue_back"):
                st.session_state.pop("or_queue", None)
                st.session_state.pop("or_queue_editor", None)
                st.session_state[_PHASE_KEY] = "scope"
                st.rerun()
        if go:
            _init_queue_run(run_queue)
            st.session_state.pop("or_queue_editor", None)
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
        result["suppliers"] = _mark_already_contacted(result["suppliers"])
        st.session_state.or_result = result
        # A plain Country/City/Keyword search maps to exactly one (country, theme) combination -
        # see the matching or_session assignment in _render_search's Country-Scope branch above
        # for why this list exists at all.
        st.session_state.or_session = {
            "country": country.strip(),
            "city": city.strip(),
            "keyword": keyword.strip(),
            "combinations": [{"country": country.strip(), "keyword": keyword.strip()}],
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


def _clean_text_cell(value):
    """Blank/whitespace-only/None all normalize to None - the shape every other field on a
    supplier record already uses for "nothing here", so a cleared cell in the table reads the
    same way a field the search never found does."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_rating_cell(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _new_supplier_from_table_row(row):
    """A row added directly in the review table's own blank row - same shape
    to_supplier_record() produces (see outreach_discovery.py), same as the "Add a supplier by
    hand" expander above the table. Returns None for a still-blank placeholder row (no name
    typed yet), not a real add."""
    name = _clean_text_cell(row.get("Name"))
    if not name:
        return None
    email = _clean_text_cell(row.get("Email"))
    return {
        "id": f"manual-{uuid.uuid4().hex[:10]}",
        "name": name,
        "email": email,
        "social": _clean_text_cell(row.get("Social")),
        "socialPlatform": None,
        "website": _clean_text_cell(row.get("Website")),
        "listingUrl": _clean_text_cell(row.get("Listing")),
        "listingSource": None,
        "selectionReason": _clean_text_cell(row.get("Why selected"))
                           or "Added by hand, not found by the automated search.",
        "reviewSummary": "Added manually.",
        "rating": _clean_rating_cell(row.get("Rating")),
        "reviewCount": None,
        "sources": [],
        "selected": bool(row.get("Send")) if row.get("Send") is not None else bool(email),
        "isMock": False,
        "addedManually": True,
    }


def _apply_review_table_edits(suppliers, diff):
    """Fold the review table's edits back onto the real supplier records.

    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "it must be possible to change all field...
    also it must be possible to add more partners to the list." Every column is now editable
    and num_rows="dynamic" lets a row be added or removed directly in the table - which means
    the OLD approach ("loop over the original list, update row i from the edited dataframe by
    position") silently breaks: an added row has no original object at that index, and a
    deleted row shifts every later index by one so "row i" no longer means the same supplier.

    Streamlit's own fix for this is to read the editor widget's diff instead of its output
    dataframe: `diff` is `st.session_state[<data_editor key>]`, shaped
    `{"edited_rows": {int_row_index: {column: new_value}}, "added_rows": [{column: value}, ...],
    "deleted_rows": [int_row_index, ...]}` - each keyed against the ORIGINAL row positions, so
    it stays correct regardless of what got added/removed elsewhere in the same edit.

    Pulled out as its own pure function (same reasoning as _merge_one_job_result/
    _finalize_queue_result above) so this can be unit tested without a running Streamlit
    script - see test_outreach_tool_queue.py's own docstring for that convention."""
    deleted = set(diff.get("deleted_rows") or [])
    edits = diff.get("edited_rows") or {}
    added = diff.get("added_rows") or []

    rebuilt = []
    for i, s in enumerate(suppliers):
        if i in deleted:
            continue
        changes = edits.get(i) or {}
        if "Send" in changes:
            s["selected"] = bool(changes["Send"])
        if "Name" in changes:
            s["name"] = _clean_text_cell(changes["Name"]) or s["name"]  # a supplier always needs a name
        if "Email" in changes:
            s["email"] = _clean_text_cell(changes["Email"])
        if "Website" in changes:
            s["website"] = _clean_text_cell(changes["Website"])
        if "Social" in changes:
            s["social"] = _clean_text_cell(changes["Social"])
        if "Listing" in changes:
            s["listingUrl"] = _clean_text_cell(changes["Listing"])
        if "Rating" in changes:
            s["rating"] = _clean_rating_cell(changes["Rating"])
        if "Why selected" in changes:
            s["selectionReason"] = _clean_text_cell(changes["Why selected"]) or s["selectionReason"]
        rebuilt.append(s)

    for row in added:
        new_supplier = _new_supplier_from_table_row(row)
        if new_supplier:
            rebuilt.append(new_supplier)

    return rebuilt


def _remember_manually_added_suppliers(suppliers, combinations):
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-08-30): "whenever the human is adding manually
    suppliers, so the App can learn which suppliers are needed and to improve the search
    results." Every supplier on this review screen that carries addedManually=True - however it
    got there, the "Add a supplier by hand" expander or a blank row typed directly into the
    table - is remembered under EVERY (country, theme) combination this review screen's search
    actually covered (see or_session["combinations"], built where the search was launched).

    Deliberately called on every rerun of the review screen, not just once at the moment of
    adding - see outreach_learned_suppliers.remember_supplier's own docstring for why that has
    to be a safe, cheap no-op for a supplier already remembered, rather than something this
    caller needs to track "did I already save this one" state for itself."""
    if not combinations:
        return
    for s in suppliers:
        if not s.get("addedManually"):
            continue
        for combo in combinations:
            oln.remember_supplier(combo["country"], combo["keyword"], s)


def _summarize_send_log(send_log):
    """Pure decision logic for the post-send banner - factored out of _render_review_and_send so
    it's testable without a Streamlit runtime.

    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "include the balloons again for a visible sign
    that mails have been sent." Balloons fire whenever at least one message genuinely went out
    (status == "sent"), independent of whether some OTHER recipient in the same batch failed or
    was skipped - a real send deserves the visible confirmation regardless of what else happened
    in the batch. Returns (sent, skipped, failed, show_balloons, message, level) - level is
    "warning" or "success", message is "" when there is nothing to report (e.g. every row was
    already-contacted and skipped, with nothing sent or failed)."""
    sent = sum(1 for e in send_log if e["status"] == "sent")
    skipped = sum(1 for e in send_log if e["status"] == "skipped")
    failed = sum(1 for e in send_log if e["status"] == "failed")
    show_balloons = sent > 0
    if failed:
        message = (f"Finished — **{sent}** sent, **{skipped}** skipped, **{failed}** failed. "
                  f"Failures are per-recipient; the rest of the batch still went out.")
        level = "warning"
    elif sent or skipped:
        message = f"🎉 Finished — **{sent}** sent, **{skipped}** skipped."
        level = "success"
    else:
        message, level = "", None
    return sent, skipped, failed, show_balloons, message, level


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

    if stats.get("stopped_early"):
        st.info(f"⏹️ Search stopped early — {stats['searched']} of {stats['total_planned']} "
                f"combination(s) were searched before you stopped it. Everything found up to "
                f"that point is below; run the rest later if you want the remaining "
                f"{stats['total_planned'] - stats['searched']}.")

    # CONFIRMED REAL INCIDENT (2026-08-25): a Morocco Country Scope run came back "0 raw
    # results" across all 40 combinations - which reads as "no suppliers exist for Morocco" but
    # can just as easily mean the search provider itself failed on every single call (expired/
    # invalid API key, rate limit, network issue). Those two situations used to be
    # indistinguishable from inside the app - see outreach_discovery._run_provider_search_with_
    # diagnostics' own docstring. This is checked and shown BEFORE the generic "no suppliers
    # survived filtering" message below, since it's the more specific, more actionable diagnosis
    # when it applies: "found nothing" (a genuine market question) vs. "the search is broken
    # right now" (a config/connectivity problem, fixable without touching any filter).
    if stats.get("provider_error_count"):
        st.error(
            f"⚠️ **{stats['provider_error_count']} search call(s) failed with an error** instead "
            f"of genuinely finding no results — this looks like a problem with the search "
            f"provider itself (an expired/invalid API key, rate limiting, or a network issue), "
            f"not a real absence of suppliers. Sample error: `{stats.get('provider_error_sample')}`. "
            f"Check `TAVILY_API_KEY`/`SERPAPI_API_KEY` and the provider's own dashboard for quota/"
            f"rate-limit status before concluding there's nothing to find here."
        )

    if not suppliers:
        st.error("No suppliers survived filtering. The breakdown below shows where they dropped out.")

    if stats.get("remembered_added"):
        st.caption(f"🧠 {stats['remembered_added']} of these were remembered from a supplier you "
                   f"added by hand in an earlier search for the same country/theme — no need to "
                   f"add them again.")

    # ---- Manual add ----
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "if the outreach is not good, the human
    # must be able to add manual searches to the list with names, Email and Links." The
    # automated chain (Tavily -> SerpAPI -> Gemini) can come back thin, wrong, or empty when a
    # provider is quota-exhausted or a search genuinely misses a good match - rather than being
    # blocked by that, a human who already knows (or just found by hand, e.g. asking ChatGPT/
    # Gemini directly) a real supplier can add it straight into this same list. It joins the
    # table below exactly like anything the search found: editable, tickable to send, and
    # blockable later - no separate manual-entries list to keep track of.
    with st.expander("➕ Add a supplier by hand", expanded=not suppliers):
        st.caption("Know a supplier the search missed, or found one faster yourself? Add it here - "
                   "Name is required; Email and Link are both optional but a row needs an email "
                   "before it can actually be sent.")
        mcol1, mcol2, mcol3, mcol4 = st.columns([2, 2, 2, 1])
        with mcol1:
            manual_name = st.text_input("Name", key="or_manual_name", placeholder="Supplier name")
        with mcol2:
            manual_email = st.text_input("Email", key="or_manual_email", placeholder="name@example.com")
        with mcol3:
            manual_link = st.text_input("Link", key="or_manual_link",
                                        placeholder="Website, listing, or social link")
        with mcol4:
            st.markdown("<div style='height: 1.7rem'></div>", unsafe_allow_html=True)
            add_manual = st.button("Add", key="or_manual_add", disabled=not manual_name.strip())
        if add_manual:
            # Same builder the review table's own "add a row directly" path uses (see
            # _apply_review_table_edits below) - one shape for a hand-added supplier, however
            # it was entered.
            new_supplier = _new_supplier_from_table_row(
                {"Name": manual_name, "Email": manual_email, "Website": manual_link})
            if new_supplier:
                suppliers.append(new_supplier)
                result["suppliers"] = suppliers
                st.session_state.or_result = result
            for k in ("or_manual_name", "or_manual_email", "or_manual_link"):
                st.session_state.pop(k, None)
            st.rerun()

    # ---- Results table ----
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "it must be possible to change all field
    # ... also it must be possible to add more partners to the list." Previously only Send/
    # Name/Email were editable and rows could only be added via the "Add a supplier by hand"
    # expander above. Now every column is editable, and num_rows="dynamic" lets a row be added
    # (or removed) directly in the table too - the expander stays as the guided, one-field-at-
    # a-time alternative for anyone who prefers it; both paths end up in the same list.
    st.caption("Untick anyone you don't want to contact. Every field here is editable — corrections "
               "are saved back to the supplier list. Add a new partner directly by filling in the "
               "blank row at the bottom, or remove one by selecting its row and pressing the trash "
               "icon.")
    if any(s.get("alreadyContacted") for s in suppliers):
        st.caption("🔁 Rows marked **Contacted before** were already emailed in an earlier session and have "
                   "been pre-unticked, per \"we can contact each supplier only once\" — re-tick one only if "
                   "you deliberately want to reach out again.")

    if suppliers:
        df = pd.DataFrame([{
            "Send": s["selected"],
            "Name": s["name"],
            "Email": s["email"] or "",
            "Website": s["website"] or "",
            "Social": s["social"] or "",
            "Listing": s["listingUrl"] or "",
            "Rating": s["rating"],
            "Contacted before": "🔁" if s.get("alreadyContacted") else "",
            "Why selected": s["selectionReason"],
        } for s in suppliers])

        st.data_editor(
            df, use_container_width=True, hide_index=True, key="or_editor",
            num_rows="dynamic",
            column_config={
                "Send": st.column_config.CheckboxColumn("Send", help="Rows ticked here will be emailed."),
                "Name": st.column_config.TextColumn("Name", help="Editable — correct the supplier name if needed."),
                "Email": st.column_config.TextColumn("Email", help="Editable — add one the search missed."),
                "Rating": st.column_config.NumberColumn("Rating", format="%.1f"),
                "Website": st.column_config.LinkColumn("Website", help="Editable."),
                "Social": st.column_config.LinkColumn("Social", help="Editable."),
                "Listing": st.column_config.LinkColumn("Listing", help="Editable."),
                "Contacted before": st.column_config.TextColumn(
                    "Contacted before", help="Already emailed in an earlier session — pre-unticked. "
                                             "Computed by the platform, not something to hand-edit."),
                "Why selected": st.column_config.TextColumn("Why selected", width="large", help="Editable."),
            },
            # "Contacted before" is a computed marker (see the docstring above it), not real
            # supplier data - everything else is editable.
            disabled=["Contacted before"],
        )

        # Fold the operator's edits back onto the real records - see _apply_review_table_edits'
        # own docstring for why this reads the editor's diff rather than comparing dataframes.
        suppliers = _apply_review_table_edits(suppliers, st.session_state.get("or_editor") or {})
        result["suppliers"] = suppliers

        # ---- LEARNING: remember anyone added by hand, for next time ----
        _remember_manually_added_suppliers(suppliers, session.get("combinations"))

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

        # ---- Optional: Show what the tool has learned from manual adds ----
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-30): symmetric to "Show current blocklist"
        # above - the same reasoning applies (see that section's own comment): a list every
        # future search reads from needs to be visible and correctable, not just a black box
        # that silently changes what shows up.
        with st.expander("🧠 Show what's been learned from manual adds"):
            learned = oln.list_all()
            if st.session_state.get("or_learned_result"):
                st.success(st.session_state.pop("or_learned_result"))
            if not learned:
                st.caption("Nothing remembered yet — add a supplier by hand above and it will show "
                           "up here, tagged with the country/theme it was added for.")
            else:
                st.caption(f"{len(learned)} supplier(s) remembered from manual adds. Each one "
                           f"automatically appears again in a future search for the exact same "
                           f"country + theme it was added for.")
                for entry in learned:
                    lc1, lc2 = st.columns([4, 1])
                    with lc1:
                        where = " · ".join(x for x in (entry["country"], entry["theme"]) if x)
                        contact = entry["email"] or entry["website"] or "no contact saved"
                        st.write(f"**{entry['name']}** — {where}")
                        st.caption(contact)
                    with lc2:
                        if st.button("🗑️ Forget", key=f"or_forget_{entry['id']}"):
                            if oln.forget_supplier(entry["country"], entry["theme"], entry["id"]):
                                st.session_state["or_learned_result"] = (
                                    f"Forgot {entry['name']} for {where} — it won't be "
                                    f"auto-resurfaced any more.")
                            else:
                                st.session_state["or_learned_result"] = (
                                    f"⚠️ Could not remove {entry['name']} — check the Memory line "
                                    f"at the bottom of the page.")
                            st.rerun()

    if stats.get("dropped_over_cap"):
        st.caption(f"ℹ️ {stats['dropped_over_cap']} additional supplier(s) were found across these "
                   f"combinations but not shown — capped at the top {stats['capped_at']} (by email, "
                   f"then website, then rating). Run fewer combinations at once to see the rest.")

    # A combination/queue run merges several searches' own stats together (see
    # _merge_one_job_result/_finalize_queue_result) and has no single drop_log or per-stage
    # breakdown of its own - only a single search (this screen's other entry point) produces
    # those. Every metric here is read with .get() so the expander degrades to "not available
    # for a combined run" instead of a KeyError, and the same for result.get("drop_log") below.
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
            if stats.get("provider_error_count"):
                scol3.metric("Search calls that errored", stats["provider_error_count"])
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

    if send_clicked:
        progress_box = st.empty()
        live = []

        def on_progress(entry):
            live.append(entry)
            progress_box.caption(f"📤 {len(live)}/{len(selected)} — {entry['supplierName']}: {entry['status']}")

        with st.spinner("Sending…"):
            st.session_state.or_send_log = oe.dispatch_batch(selected, session, template,
                                                              on_progress=on_progress, dry_run=False)
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): durable send history, so a "sent N+
        # days ago, no reply logged" follow-up list has something to work from - see
        # outreach_followups.py's module docstring for why this couldn't exist before (a send
        # log used to live only in this browser session and a downloaded CSV).
        ofw.record_sends_from_log(selected, session, st.session_state.or_send_log)

        # CONFIRMED FIX (2026-08-19 audit): previously nothing disarmed the send button after a
        # successful send - the confirm checkbox and the "Send" ticks stayed exactly as they
        # were, so a refresh, a double-click, or an accidental re-click of "Send" resent the
        # identical batch to the same recipients. Every row that actually got a message out
        # (status == "sent") is now un-ticked and flagged "Contacted before" so re-sending
        # requires a deliberate re-tick, and the confirmation checkbox resets to unchecked.
        sent_emails = {e["email"] for e in st.session_state.or_send_log
                      if e.get("status") == "sent" and e.get("email")}
        for s in suppliers:
            if (s.get("email") or "") in sent_emails:
                s["selected"] = False
                s["alreadyContacted"] = True
        st.session_state.pop("or_confirm_real", None)

        progress_box.empty()
        st.rerun()

    send_log = st.session_state.get("or_send_log")
    if send_log:
        sent, skipped, failed, show_balloons, message, level = _summarize_send_log(send_log)
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "include the balloons again for a visible
        # sign that mails have been sent." Previously gated behind a FULLY clean batch (only in
        # the `else` of `if failed:`), so a batch with even one per-recipient failure among many
        # genuine sends showed no balloons at all, even though real mail went out to everyone
        # else - the exact case a busy outreach batch hits often. Balloons are now the visible
        # "yes, mail was actually sent" signal whenever at least one message actually went out,
        # independent of whether some other recipient in the same batch failed or was skipped.
        # Decision logic factored into _summarize_send_log so it's testable without Streamlit.
        if show_balloons:
            st.balloons()
        if message:
            (st.warning if level == "warning" else st.success)(message)
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
# SCREEN — FOLLOW-UPS DUE (manual-confirm reply tracking)
# ============================================================================
def _render_followup_row(row, show_replied_button=True, show_reminder_button=True,
                         show_external_button=True):
    """One card, shared by the 'due' and 'cold' sections below - the actions available differ
    (a cold row already used its one reminder, so only 'mark replied' remains useful there)."""
    with st.container(border=True):
        title = row.get("supplier_name") or row.get("email")
        st.markdown(f"**{title}** — {row.get('email', '')}")
        meta_bits = []
        if row.get("country"):
            meta_bits.append(row["country"])
        if row.get("keyword"):
            meta_bits.append(row["keyword"])
        if meta_bits:
            st.caption(" · ".join(meta_bits))
        reminder_note = ""
        if row.get("reminder_sent_at"):
            channel = "logged externally" if row.get("reminder_channel") == "external" else "reminder sent"
            reminder_note = f" · {channel} {row.get('days_since_reminder', '?')} day(s) ago"
        st.caption(f"Sent {row.get('days_since_sent')} day(s) ago{reminder_note} · "
                  f"subject: \"{row.get('subject', '')}\"")

        cols = st.columns([1, 1, 1])
        with cols[0]:
            if show_replied_button and st.button("✅ Mark as replied", key=f"or_followup_replied_{row['key']}"):
                ofw.mark_replied(row["email"], row["sent_at"])
                st.rerun()
        with cols[1]:
            if show_reminder_button and st.button("📨 Send reminder", key=f"or_followup_remind_{row['key']}"):
                supplier = {
                    "name": row.get("supplier_name") or "",
                    "email": row.get("email") or "",
                    "website": row.get("website") or "",
                }
                session = {
                    "country": row.get("country") or "",
                    "keyword": row.get("keyword") or "",
                }
                try:
                    oe.send_supplier_email(supplier, session, oe.DEFAULT_REMINDER_TEMPLATE)
                except Exception as exc:
                    st.error(f"Reminder failed to send: {exc}")
                else:
                    ofw.mark_reminder_sent(row["email"], row["sent_at"], channel="tool")
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-31): "once send mails to supplier,
                    # I dont see any balloons - but we should display it, if the outreach mail was
                    # successfully send." The batch "review and send" screen already fires
                    # st.balloons() on any successful send (2026-08-26 fix), but this individual
                    # reminder-send button - a real email dispatch via send_supplier_email, exactly
                    # like a batch send - never got the same treatment; it only ever showed a small
                    # text success message. Balloons now fire here too, so every path that actually
                    # sends mail through the tool gives the same visible confirmation.
                    st.balloons()
                    st.success("Reminder sent.")
                    st.rerun()
        with cols[2]:
            if show_external_button and st.button("📞 Log external contact",
                                                   key=f"or_followup_external_{row['key']}",
                                                   help="I already followed up with this supplier myself, "
                                                        "outside this tool."):
                ofw.log_external_contact(row["email"], row["sent_at"])
                st.success("Logged — this won't nag again unless you mark it replied.")
                st.rerun()


def _render_followups():
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16): a worklist of suppliers who were emailed a
    while ago with nothing logged as a reply yet - see outreach_followups.py's module docstring
    for why this is manual-confirm rather than automatic reply detection (the platform can send
    mail, it has no access to any inbox to read replies from).

    CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): reminders are capped at one, and a row
    can also be settled by logging an external (outside-the-tool) contact - both move a row from
    the "due" list below into the "cold" list, which never nags but stays visible in case a very
    late reply shows up and the operator wants to mark it replied."""
    st.subheader("📋 Follow-ups")
    st.caption(f"Suppliers emailed **{ofw.FOLLOWUP_DUE_DAYS}+ days** ago with no reply logged yet. "
               "This is not automatic reply detection - the platform can't read your inbox, so "
               "please check it yourself before marking a row as replied.")

    if st.button("⬅️ Back", key="or_followups_back"):
        st.session_state[_PHASE_KEY] = "scope"
        st.rerun()

    due = ofw.pending_followups()
    if not due:
        st.success("Nothing due right now — either everything's been replied to, or it's too "
                   "soon since the last send.")
    else:
        st.markdown(f"##### {len(due)} due — never followed up on yet")
        for row in due:
            _render_followup_row(row)

    cold = ofw.cold_followups()
    if cold:
        with st.expander(f"🧊 {len(cold)} already followed up once, still no reply"):
            st.caption("Reminders are capped at one, so these won't come back onto the list above "
                      "on their own. Mark one replied if it eventually responds.")
            for row in cold:
                _render_followup_row(row, show_reminder_button=False, show_external_button=False)


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
    elif st.session_state[_PHASE_KEY] == "followups":
        _render_followups()
    else:
        _render_review_and_send()