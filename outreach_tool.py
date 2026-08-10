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
import csv
import io
import os
import streamlit as st
import pandas as pd

import outreach_discovery as od
import outreach_email as oe
import outreach_memory as om  # new learning module

_PHASE_KEY = "or_phase"


def _reset_run():
    for key in list(st.session_state.keys()):
        if key.startswith("or_"):
            del st.session_state[key]


# ============================================================================
# SCREEN 1 — SEARCH
# ============================================================================
def _render_search():
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

        col_learn1, col_learn2 = st.columns([3, 1])
        with col_learn1:
            # Count unticked suppliers that have a website or listing URL
            unticked_domains = set()
            for s in suppliers:
                if not s["selected"]:
                    url = s.get("website") or s.get("listingUrl")
                    if url:
                        domain = om.extract_domain(url)  # <-- FIXED: now uses public function
                        if domain:
                            unticked_domains.add(domain)
            st.caption(f"📌 {len(unticked_domains)} unique domain(s) from unticked suppliers will be blocked.")
        with col_learn2:
            if st.button("🧠 Block them", key="or_block_unticked", disabled=not unticked_domains):
                blocked_count = 0
                for domain in unticked_domains:
                    om.add_domain_to_blocklist(domain)
                    blocked_count += 1
                st.success(f"✅ Blocked {blocked_count} domain(s) from future searches.")
                st.rerun()

        # ---- Optional: Show current blocklist ----
        with st.expander("🔍 Show current blocklist"):
            blocked = om.get_blocklist()
            if not blocked:
                st.caption("No domains blocked yet.")
            else:
                for domain in blocked:
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.write(f"`{domain}`")
                    with c2:
                        if st.button("🗑️ Remove", key=f"or_unblock_{domain}"):
                            om.remove_domain_from_blocklist(domain)
                            st.success(f"Removed `{domain}` from blocklist.")
                            st.rerun()

    with st.expander(f"🔬 How the {stats['raw']} raw results became {stats['final']}"):
        st.caption("The original tool wrote this to a server console. It's here instead because the "
                   "distinction matters: a known operator never appearing at all is a search problem, "
                   "whereas appearing and being dropped is a filter problem.")
        scol1, scol2, scol3 = st.columns(3)
        scol1.metric("Raw results", stats["raw"])
        scol1.metric("Passed pre-filter", stats["after_prefilter"])
        scol2.metric("Passed vetting", stats["after_vetting"])
        scol2.metric("After merging duplicates", stats["after_dedupe"])
        scol3.metric("Dropped by AI check", stats["ai_dropped"])
        scol3.metric("Dropped: no way to contact", stats["no_contact_dropped"])
        if result["drop_log"]:
            st.dataframe(pd.DataFrame(result["drop_log"]), use_container_width=True, hide_index=True)
        else:
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
        st.session_state[_PHASE_KEY] = "search"

    if st.session_state[_PHASE_KEY] == "search":
        _render_search()
    else:
        _render_review_and_send()