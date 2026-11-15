"""
flows/supplement_bulk.py — combined "Bulk supplements" entry point.

CONFIRMED PRODUCT-OWNER REQUEST (2026-11-15): the four per-product supplement flows
(Transfer, Ticket, ClosedTour, Hotel) are one menu entry with the product type picked
inside, exactly as the two image flows were merged into "Bulk-change images" on
2026-10-18. Rationale, verbatim from that earlier decision: "make the tool less complex
and more intuitive for humans" — four near-identical buttons in the menu said nothing a
selector inside the flow doesn't say better.

Each product type's supplement lives in a different place in Travel Compositor's schema,
so the four flows stay separate modules and this only routes between them:
  Transfer   — supplements[] on ContractTransferVO;        PUT the whole transfer.
  Ticket     — supplements[] on ContractTicketModalityVO;  PUT per modality.
  ClosedTour — supplements[] on ContractClosedTourVO;      PUT the whole tour.
  Hotel      — dedicated POST /hotel/supplement/{supplierId}/{providerCode}.
"""
import streamlit as st

MODULE_BUILD = "2026-11-15-supplement-bulk"

_TYPES = [
    ("Transfer", "🚌 Transfer",
     "Night, weekend and holiday surcharges. Update the times on existing "
     "surcharges, or add a new one across a supplier's transfers."),
    ("Ticket", "🎟️ Ticket",
     "Adult / children / infant price supplements on each ticket modality, "
     "with their own validity dates."),
    ("ClosedTour", "🗺️ Closed Tour",
     "Supplements priced per occupancy (single / double / triple / quadruple), "
     "scoped to travel windows and modality codes."),
    ("Hotel", "🏨 Hotel",
     "Supplements scoped to room codes and meal plans, with apply type "
     "(per night / per stay / per person) and stay constraints."),
]


def render_supplement_bulk_flow(client):
    from flows.transfer_surcharge_bulk import render_transfer_surcharge_bulk_flow
    from flows.ticket_supplement_bulk import render_ticket_supplement_bulk_flow
    from flows.closedtour_supplement_bulk import render_closedtour_supplement_bulk_flow
    from flows.hotel_supplement_bulk import render_hotel_supplement_bulk_flow

    st.header("➕ Bulk supplements")

    if "supplement_bulk_product" not in st.session_state:
        st.session_state.supplement_bulk_product = None

    if st.session_state.supplement_bulk_product is None:
        if st.button("🔙 Back", key="supbulk_back"):
            st.session_state.product_type = None
            st.rerun()
        st.caption("Add or update supplements across many already-live products at once. "
                   "Pick which product type to work on:")
        cols = st.columns(2)
        for i, (key, label, blurb) in enumerate(_TYPES):
            with cols[i % 2]:
                if st.button(label, key=f"supbulk_{key}", use_container_width=True,
                             type="primary"):
                    st.session_state.supplement_bulk_product = key
                    st.rerun()
                st.caption(blurb)
        return

    # A second Back, so the human can change product type without leaving the tool.
    if st.button("🔙 Pick a different product type", key="supbulk_change_type"):
        st.session_state.supplement_bulk_product = None
        st.rerun()

    chosen = st.session_state.supplement_bulk_product
    if chosen == "Transfer":
        render_transfer_surcharge_bulk_flow(client)
    elif chosen == "Ticket":
        render_ticket_supplement_bulk_flow(client)
    elif chosen == "ClosedTour":
        render_closedtour_supplement_bulk_flow(client)
    else:
        render_hotel_supplement_bulk_flow(client)
