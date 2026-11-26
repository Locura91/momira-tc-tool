"""
flows/price_audit.py

Price Audit entry point — the user picks a product type and is routed into the
existing price refresh flow for that type, which already does everything the
audit needs: load TC products, upload a document, extract prices, show a diff,
and optionally apply changes in bulk.

Transfer/Transport → render_price_refresh_flow (flows/price_refresh.py)
Ticket → render_ticket_price_refresh_flow (flows/price_refresh.py)
ClosedTour → dedicated ClosedTour price audit (below, since no refresh flow exists yet)
"""

MODULE_BUILD = "2026-11-17-price-audit-router"

import streamlit as st
import price_refresh


_KEY = "pa_"


def render_price_audit_flow(client):
    st.header("Price Audit")
    st.caption("Check a supplier's current TC prices against a new rate sheet. "
               "If prices differ, you can apply the corrections in bulk.")

    if st.button("🔙 Back", key=f"{_KEY}back"):
        st.session_state.product_type = None
        st.rerun()

    product_type = st.selectbox(
        "What do you want to audit?",
        ["Transfer", "Transport", "Ticket", "ClosedTour"],
        key=f"{_KEY}ptype")

    st.divider()

    if product_type in ("Transfer", "Transport"):
        from flows.price_refresh import render_price_refresh_flow
        kind = price_refresh.KIND_TRANSFER if product_type == "Transfer" else price_refresh.KIND_TRANSPORT
        render_price_refresh_flow(client, preselected_kind=kind)

    elif product_type == "Ticket":
        from flows.price_refresh import render_ticket_price_refresh_flow
        render_ticket_price_refresh_flow(client)

    elif product_type == "ClosedTour":
        from flows.closedtour_audit import render_closedtour_audit
        render_closedtour_audit(client)
