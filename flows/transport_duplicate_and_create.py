"""Combined Transport create screen - merges the automated missing-reverse-direction scan
(flows/missing_transports.py) with the manual duplicate-by-id flow (flows/duplicate_transport.py)
behind ONE shared supplier picker.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-22, verbatim): "when duplicating transfer, I can select
the supplier and then direct I can scan this supplier for missing transfers - that should be
exactly the same for transport. currently I can only dubilicate one transpport at the time, but
that is not practical."

Exact Transport counterpart to flows/transfer_duplicate_and_create.py (2026-09-17) - see that
file's own docstring for the full history of why this combined shape exists (one supplier pick
shared by the scan and the manual fallback, rather than two separate pickers stacked on one
screen). The automated scan is the primary section here; the manual "paste a known Transport id"
path from flows/duplicate_transport.py is kept as a secondary option underneath, for a specific
record the automated scan doesn't catch.

Both underlying modules keep their own standalone render_*_flow() entry points (each still picks
its own supplier independently) for backward compatibility with their existing test suites and for
any future standalone use - this file is only a thin combined wrapper around the two "already know
the supplier" body functions (_render_missing_transports_body / _render_duplicate_transport_body),
sharing ONE supplier pick across both.
"""
import streamlit as st

from app_helpers import _ur_pick_momira_supplier
from flows.missing_transports import _render_missing_transports_body
from flows.duplicate_transport import _render_duplicate_transport_body


def render_transport_duplicate_and_create_flow(client):
    st.header("🧬🔍 Transport — duplicate & create missing reverse-direction transports")
    if st.button("🔙 Back to Step 1", key="tpdc_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption(
        "Pick a supplier once. The app scans every live Transport for that supplier and lists "
        "every route with no reverse-direction pair yet - tick the ones to create (or Select "
        "all / Select none) and publish them as a batch. For a specific Transport the automatic "
        "scan doesn't catch, duplicate it directly by id below instead."
    )

    supplier_id = _ur_pick_momira_supplier(client, "tpdc")
    if not supplier_id:
        return

    st.markdown("### 🔍 Scan for missing reverse-direction transports")
    _render_missing_transports_body(client, supplier_id)

    st.markdown("---")
    with st.expander("🧬 Or: duplicate one specific Transport by id"):
        _render_duplicate_transport_body(client, supplier_id)
