"""
flows/image_bulk.py — combined "Bulk-change images" entry point.

CONFIRMED PRODUCT-OWNER REQUEST (2026-10-18): merge the separate Transfer and Transport
bulk-image flows into one. The human picks the product type first (Transfer or Transport),
then the rest of the flow is handed off to the existing per-product flow module.
"""
import streamlit as st

MODULE_BUILD = "2026-10-24-rollover-ext"


def render_image_bulk_flow(client):
    from flows.transfer_image_bulk import render_transfer_image_bulk_flow
    from flows.transport_image_bulk import render_transport_image_bulk_flow
    from flows.product_image_bulk import render_product_image_bulk_flow

    st.header("🖼️ Bulk-change images")

    if "image_bulk_product" not in st.session_state:
        st.session_state.image_bulk_product = None

    if st.session_state.image_bulk_product is None:
        if st.button("🔙 Back", key="ibulk_back"):
            st.session_state.product_type = None
            st.rerun()
        st.caption("Replace the photo on many already-live products at once. "
                   "Pick which product type to update:")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚌 Transfer", key="ibulk_transfer", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Transfer"
                st.rerun()
            st.caption("By supplier, by Service Type (Private / Shuttle / Shared), or both.")
            if st.button("🎟️ Ticket", key="ibulk_ticket", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Ticket"
                st.rerun()
            st.caption("By supplier. Writes the Ticket's `imageUrls` field.")
            if st.button("🏨 Hotel", key="ibulk_hotel", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Hotel"
                st.rerun()
            st.caption("By supplier. Writes the Hotel's `images` field.")
        with col2:
            if st.button("✈️ Transport", key="ibulk_transport", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "Transport"
                st.rerun()
            st.caption("By supplier, by Transport Type (Car / Plane / Van / Combined), or both.")
            if st.button("🗺️ Closed Tour", key="ibulk_closedtour", use_container_width=True,
                         type="primary"):
                st.session_state.image_bulk_product = "ClosedTour"
                st.rerun()
            st.caption("By supplier. Writes the Closed Tour's `images` field.")
        return

    chosen = st.session_state.image_bulk_product

    # Transfer and Transport keep their own screens: both carry filters that exist only on
    # those types (Service Type, Vehicle Type, Transport Type) plus behaviour earned from
    # live bug reports. The other three filter by supplier alone and share one engine.
    if chosen != "Transfer" and chosen != "Transport":
        if st.button("🔙 Pick a different product type", key="ibulk_change_type"):
            for _k in list(st.session_state.keys()):
                if _k.startswith(f"pib_{chosen}"):
                    del st.session_state[_k]
            st.session_state.image_bulk_product = None
            st.rerun()
        render_product_image_bulk_flow(client, chosen)
    elif chosen == "Transfer":
        render_transfer_image_bulk_flow(client)
    else:
        render_transport_image_bulk_flow(client)
