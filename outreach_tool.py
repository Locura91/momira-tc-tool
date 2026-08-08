"""
outreach_tool.py — the Supplier Discovery & Outreach tool, as it lives inside
the Momira Travel Platform.

Originally a React SPA (5-step wizard) talking to an Express API. The platform
runs on Streamlit, which is Python-only, so the backend services were ported
(see outreach_discovery.py and outreach_email.py, both differential-tested
against the original JavaScript) and this file replaces the whole React client.

WHAT COLLAPSED IN THE REWRITE: the original's steps 1 and 2 were separate
screens only because discovery ran asynchronously on a server - POST /api/search
returned a sessionId immediately and the browser polled every 2 seconds until it
flipped to "completed". Streamlit runs the search inline behind a live progress
readout, so the polling architecture, the session id, and the whole api.js REST
client have no equivalent here and simply don't exist. The remaining steps map
one to one:

    original Step 1 + 2  ->  "Search" phase (input + live progress)
    original Step 3 + 4  ->  "Review" phase (results table + template editor)
    original Step 5      ->  "Dispatch" phase (send + live log)

NOT PORTED: `server/services/db.js`, the JSON-file store that persisted
sessions/suppliers/campaigns server-side. Streamlit's session state holds the
working set for the run, and the send log is offered as a CSV download at the
end, which is more portable than a db.json only reachable over SSH. If a
durable campaign history is wanted later, state_store.py (already in the
platform, already SQLite) is the natural place for it rather than a new file.

SENDING SAFETY: this is the only tool in the platform that takes an
irreversible, externally-visible action - mail going to real companies under
Momira's name. So the dispatch step defaults to a dry run, and a real send
requires an explicit opt-in plus a confirmation that states the recipient count
and lists the addresses. See _render_dispatch() for the specifics.
"""
import io
import os
import csv

import pandas as pd
import streamlit as st

import outreach_discovery as od
import outreach_email as oe

_PHASE_KEY = "or_phase"


def _reset_run():
    for key in list(st.session_state.keys()):
        if key.startswith("or_"):
            del st.session_state[key]


def _provider_banner():
    """Always visible on the review and dispatch screens: which provider is live, whether
    test-mode redirection is on, and whether a PDF will be attached. All three change what
    actually happens on send, and all three are invisible from the UI otherwise."""
    status = oe.verify_transport()
    provider = status["provider"]

    if provider == "demo":
        st.warning("📭 **Demo mode** — no email provider is configured, so nothing will actually be "
                   "delivered. Messages are still fully built, so the whole workflow is testable. "
                   "Set `RESEND_API_KEY` (recommended) or the `SMTP_*` values to send for real.")
    elif not status["ok"]:
        st.error(f"❌ **{provider.upper()} is configured but not working:** {status.get('error')}. "
                 f"Fix this before sending — a real send would fail for every recipient.")
    else:
        st.success(f"✅ Sending via **{provider.upper()}** from **{oe.get_from_address()}**")

    if status["testMode"]:
        st.info(f"🧪 **Test mode is ON** — every email will be redirected to "
                f"{', '.join(status['testRecipients'])} instead of the real supplier, with a `[TEST]` "
                f"subject prefix. Clear `TEST_MODE_RECIPIENTS` to send to suppliers for real.")

    pdf = oe.get_pdf_status()
    if pdf["attached"]:
        st.caption(f"📎 Company profile PDF will be attached ({pdf['sizeKb']} KB).")
    else:
        st.caption(f"📎 No company profile PDF found — emails will send without an attachment. "
                   f"Place one at `{pdf['path']}` or set `PDF_ATTACHMENT_PATH`.")
    return status


# ============================================================================
# PHASE 1 — SEARCH
# ============================================================================
def _render_search():
    st.header("Outreach — Step 2: Who are you looking for?")
    st.caption("Searches Google, Tripadvisor, Trustpilot, Viator and GetYourGuide for well-reviewed "
              "local operators, filters out articles, listicles, forums and booking marketplaces, then "
              "tries to find a direct email for each one.")

    col1, col2 = st.columns(2)
    with col1:
        country = st.text_input("Country / region", placeholder="e.g. Egypt", key="or_country")
    with col2:
        keyword = st.text_input("What they should offer", placeholder="e.g. Nile Cruise", key="or_keyword")

    if not (os.getenv("TAVILY_API_KEY") or os.getenv("SERPAPI_API_KEY")):
        st.warning("🔍 No search API key configured (`TAVILY_API_KEY` or `SERPAPI_API_KEY`), so this will "
                   "run against clearly-labelled **mock data**. Useful for trying the workflow end to end, "
                   "but the suppliers won't be real.")

    if st.button("🔎 Find suppliers", type="primary", disabled=not (country.strip() and keyword.strip())):
        progress_box = st.empty()
        with st.spinner("Searching…"):
            try:
                result = od.discover_suppliers(
                    country.strip(), keyword.strip(),
                    progress=lambda msg: progress_box.caption(f"⏳ {msg}"),
                )
            except Exception as e:
                progress_box.empty()
                st.error(f"Search failed: {e}")
                return
        progress_box.empty()
        st.session_state.or_result = result
        st.session_state.or_session = {"country": country.strip(), "keyword": keyword.strip()}
        st.session_state.or_template = dict(oe.DEFAULT_TEMPLATE)
        st.session_state[_PHASE_KEY] = "review"
        st.rerun()


# ============================================================================
# PHASE 2 — REVIEW + TEMPLATE
# ============================================================================
def _render_review():
    result = st.session_state.or_result
    session = st.session_state.or_session
    suppliers = result["suppliers"]
    stats = result["stats"]

    st.header(f"Outreach — Step 3: Review {len(suppliers)} supplier(s)")
    st.caption(f"Found for **{session['keyword']}** in **{session['country']}**. Untick anyone you don't "
              "want to contact. You can also correct a name or type in an email the search couldn't find — "
              "a row with no email is skipped at send time unless you add one.")

    if stats["used_mock_provider"]:
        st.warning("⚠️ These are **mock results** — no search API key is configured. Don't email them.")

    if not suppliers:
        st.error("No suppliers survived filtering. The breakdown below shows where they dropped out.")

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
                "Email": st.column_config.TextColumn("Email", help="Editable — add one the search missed."),
                "Rating": st.column_config.NumberColumn("Rating", format="%.1f"),
                "Website": st.column_config.LinkColumn("Website"),
                "Social": st.column_config.LinkColumn("Social"),
                "Listing": st.column_config.LinkColumn("Listing"),
                "Why selected": st.column_config.TextColumn("Why selected", width="large"),
            },
            disabled=["Name", "Website", "Social", "Listing", "Rating", "Why selected"],
        )

        # Fold the operator's edits back onto the real supplier records, matched by row
        # order (the table is never sorted or filtered, so position is stable).
        for i, s in enumerate(suppliers):
            if i < len(edited):
                s["selected"] = bool(edited.iloc[i]["Send"])
                s["email"] = (str(edited.iloc[i]["Email"]).strip() or None)

    st.markdown("#### Email template")
    st.caption("Tags in square brackets are filled in per supplier: `[SupplierName]`, `[ContactName]`, "
              "`[Country]`, `[FocusKeyword]`, `[Website]`, `[SenderName]`. A tag with no value is left "
              "visible rather than silently blanked, so a typo is obvious. The HTML version is generated "
              "from this text at send time, so there's only ever one body to edit.")
    template = st.session_state.or_template
    template["subject"] = st.text_input("Subject", value=template["subject"], key="or_subject")
    template["textBody"] = st.text_area("Body", value=template["textBody"], height=380, key="or_body")

    selected = [s for s in suppliers if s.get("selected")]
    if selected:
        with st.expander(f"👀 Preview as {selected[0]['name']} would receive it"):
            msg = oe.build_message(selected[0], session, template)
            st.text_input("To", value=", ".join(msg["to"]) or "(no address)", disabled=True, key="or_prev_to")
            st.text_input("Subject", value=msg["subject"], disabled=True, key="or_prev_subj")
            st.text(msg["text"])

    st.divider()
    with_email = [s for s in selected if s.get("email")]
    st.markdown(f"**{len(selected)}** selected · **{len(with_email)}** with an email address "
                f"· **{len(selected) - len(with_email)}** would be skipped")

    ncol1, ncol2 = st.columns([1, 3])
    with ncol1:
        if st.button("🔙 New search"):
            _reset_run()
            st.rerun()
    with ncol2:
        if st.button("➡️ Continue to sending", type="primary", disabled=not selected):
            st.session_state[_PHASE_KEY] = "dispatch"
            st.rerun()


# ============================================================================
# PHASE 3 — DISPATCH
# ============================================================================
def _log_dataframe(log):
    return pd.DataFrame([{
        "Status": e["status"],
        "Supplier": e["supplierName"],
        "Email": e.get("email") or "",
        "To": ", ".join(e["to"]) if e.get("to") else "",
        "Detail": e.get("reason") or e.get("subject") or e.get("messageId") or "",
        "Time": e["timestamp"],
    } for e in log])


def _render_dispatch():
    session = st.session_state.or_session
    template = st.session_state.or_template
    suppliers = st.session_state.or_result["suppliers"]
    selected = [s for s in suppliers if s.get("selected")]
    sendable = [s for s in selected if s.get("email")]

    st.header("Outreach — Step 4: Send")
    status = _provider_banner()

    st.markdown(f"**{len(selected)}** supplier(s) selected. **{len(sendable)}** have an email address and "
                f"would be contacted; **{len(selected) - len(sendable)}** would be skipped.")

    if not oe.is_test_mode_enabled() and sendable:
        with st.expander(f"📋 The {len(sendable)} address(es) that would receive this"):
            st.dataframe(pd.DataFrame([{"Supplier": s["name"], "Email": s["email"]} for s in sendable]),
                         use_container_width=True, hide_index=True)

    st.divider()

    # ---- Dry run: always available, always safe, never touches a provider ----
    st.markdown("#### 1. Dry run")
    st.caption("Builds every message exactly as a real send would — same rendering, same recipients, "
              "same skip rules — without contacting any email provider. Nothing leaves this app.")
    if st.button("🧪 Run dry run", type="primary" if not st.session_state.get("or_dry_log") else "secondary"):
        progress_box = st.empty()
        live = []

        def on_progress(entry):
            live.append(entry)
            progress_box.caption(f"⏳ {len(live)}/{len(selected)} — {entry['supplierName']}: {entry['status']}")

        st.session_state.or_dry_log = oe.dispatch_batch(selected, session, template,
                                                         on_progress=on_progress, dry_run=True)
        progress_box.empty()
        st.rerun()

    dry_log = st.session_state.get("or_dry_log")
    if dry_log:
        would_send = sum(1 for e in dry_log if e["status"] == "would_send")
        skipped = sum(1 for e in dry_log if e["status"] == "skipped")
        failed = sum(1 for e in dry_log if e["status"] == "failed")
        st.success(f"Dry run complete — **{would_send}** would send, **{skipped}** skipped, **{failed}** failed to build.")
        st.dataframe(_log_dataframe(dry_log), use_container_width=True, hide_index=True)

    st.divider()

    # ---- Real send: opt-in, gated behind an explicit confirmation ----
    st.markdown("#### 2. Send for real")
    if not dry_log:
        st.info("Run the dry run first — it's the only way to see exactly what would go out.")
        return

    if not sendable:
        st.warning("No selected supplier has an email address, so there's nothing to send.")
        return

    if status["provider"] == "demo":
        st.caption("In demo mode this still won't deliver anything — it exercises the send path only.")
    elif not status["ok"]:
        st.error("The email provider isn't working (see above). Sending now would fail for every recipient.")
        return

    destination = ("your test inbox" if oe.is_test_mode_enabled()
                   else f"**{len(sendable)} real supplier(s)**")
    confirmed = st.checkbox(
        f"I want to actually send this to {destination}.", key="or_confirm_real",
        help="Sending cannot be undone. Leave this unticked to keep working in dry-run mode.",
    )

    if oe.is_test_mode_enabled():
        st.caption(f"Test mode is on, so these go to {', '.join(oe.get_test_mode_recipients())}, not to suppliers.")
    else:
        st.warning(f"⚠️ This sends real email from **{oe.get_from_address()}** to **{len(sendable)}** external "
                   f"companies. It cannot be recalled.")

    if st.button(f"📨 Send to {len(sendable)} supplier(s) now", type="primary", disabled=not confirmed):
        progress_box = st.empty()
        live = []

        def on_progress(entry):
            live.append(entry)
            progress_box.caption(f"📤 {len(live)}/{len(selected)} — {entry['supplierName']}: {entry['status']}")

        with st.spinner("Sending…"):
            st.session_state.or_send_log = oe.dispatch_batch(selected, session, template,
                                                              on_progress=on_progress, dry_run=False)
        progress_box.empty()
        st.rerun()

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

        # The original persisted this server-side in db.json. A download is more useful
        # here and doesn't need a database - see this module's header.
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["status", "supplierName", "email", "reason",
                                                  "messageId", "timestamp"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(send_log)
        st.download_button("⬇️ Download send log (CSV)", buf.getvalue(),
                           file_name=f"outreach-{session['country']}-{session['keyword']}.csv".replace(" ", "-"),
                           mime="text/csv")

    st.divider()
    bcol1, bcol2 = st.columns([1, 3])
    with bcol1:
        if st.button("🔙 Back to review"):
            st.session_state[_PHASE_KEY] = "review"
            st.rerun()
    with bcol2:
        if st.button("🆕 Start a new search"):
            _reset_run()
            st.rerun()


# ============================================================================
# ENTRY POINT
# ============================================================================
def render_outreach_tool():
    """Supplier discovery, vetting and outreach. Finds well-reviewed local operators,
    lets a human curate the list, and emails the approved ones."""
    if _PHASE_KEY not in st.session_state:
        st.session_state[_PHASE_KEY] = "search"

    phase = st.session_state[_PHASE_KEY]
    if phase == "search":
        _render_search()
    elif phase == "review":
        _render_review()
    else:
        _render_dispatch()
