"""Combined Transfer create screen - merges the automated missing-reverse-direction scan
(flows/missing_transfers.py) with the manual duplicate-by-id flow (flows/duplicate_transfer.py)
behind ONE shared supplier picker.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "the transfer duplicate section shall be combined
with transfer find & create. We must put them together. Long term Goal for this section is, that
human selects the correct supplier, the app checks all transfers and identifies missing
duplicates in a list for example. Then the human reviews all possible duplicates and can say
'select all' or 'select none' for auto creation."

That long-term goal is exactly what flows/missing_transfers.py already does (2026-09-16, its own
docstring has the full history) - pick supplier -> scan -> list of gaps -> select all/select
none -> batch "Create selected" with a progress bar and a created/failed summary. It's the
primary section here. The manual "paste a known Transfer id" path from flows/duplicate_transfer.py
is kept as a secondary option underneath, for a specific record the automated scan doesn't catch
(its own source Transfer isn't itself missing a reverse pair, or the human just wants to
duplicate one particular record without waiting on a full-supplier scan).

Both underlying modules keep their own standalone render_*_flow() entry points (each still picks
its own supplier independently) for backward compatibility with their existing test suites and
for any future standalone use - this file is only a thin combined wrapper around the two
"already know the supplier" body functions (_render_missing_transfers_body /
_render_duplicate_transfer_body), sharing ONE supplier pick across both instead of showing the
human two separate pickers stacked on the same screen.
"""
import streamlit as st

from app_helpers import _ur_pick_momira_supplier
from flows.missing_transfers import _render_missing_transfers_body
from flows.duplicate_transfer import _render_duplicate_transfer_body


def render_transfer_duplicate_and_create_flow(client):
    st.header("🧬🔍 Transfer — duplicate & create missing reverse-direction transfers")
    if st.button("🔙 Back to Step 1", key="tdc_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption(
        "Pick a supplier once. The app scans every live Transfer for that supplier and lists "
        "every route with no reverse-direction pair yet - tick the ones to create (or Select "
        "all / Select none) and publish them as a batch. For a specific Transfer the automatic "
        "scan doesn't catch, duplicate it directly by id below instead."
    )

    supplier_id = _ur_pick_momira_supplier(client, "tdc")
    if not supplier_id:
        return

    st.markdown("### 🔍 Scan for missing reverse-direction transfers")
    _render_missing_transfers_body(client, supplier_id)

    st.markdown("---")
    with st.expander("🧬 Or: duplicate one specific Transfer by id"):
        _render_duplicate_transfer_body(client, supplier_id)
