"""
flows/image_bulk.py — combined "Bulk-change images" entry point.

CONFIRMED PRODUCT-OWNER REQUEST (2026-10-18): merge the separate Transfer and Transport
bulk-image flows into one. The human picks the product type first (Transfer or Transport),
then the rest of the flow is handed off to the existing per-product flow module.
"""
import streamlit as st

MODULE_BUILD = "2026-10-19-rollover-start-update"


def render_image_bulk_flow(client):
    from flows.transfer_image_bulk import render_transfer_image_bulk_flow
    from flows.transport_image_bulk import render_transport_image_bulk_flow

    st.header("🖼️ Bulk-change images")

    if "image_bulk_product" not in st.session_state:
        st.session_state.image_bulk_product = None

    if st.session_state.image_bulk_product is None:
        st.caption("Replace the photo on many already-live products at once. "
                   "Pick which product type to update:")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚌 Transfer", key="ibulk_transfer", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Transfer"
                st.rerun()
            st.caption("By supplier, by Service Type (Private / Shuttle / Shared), or both.")
        with col2:
            if st.button("✈️ Transport", key="ibulk_transport", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Transport"
                st.rerun()
            st.caption("By supplier, by Transport Type (Car / Plane / Van / Combined), or both.")
        return

    if st.session_state.image_bulk_product == "Transfer":
        render_transfer_image_bulk_flow(client)
    else:
        render_transport_image_bulk_flow(client)
