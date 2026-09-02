"""
cancellation_links.py — reusable "linked" cancellation policies.

WHY THIS EXISTS: today, the cancellation fee tiers a human sees on the review
screen come from exactly one place — whatever the AI just extracted from THIS
document. If the document doesn't state its own cancellation terms (common —
many rate sheets only cover prices/dates and leave cancellation to a standing
contract clause), the table starts empty and a human has to know and re-type
the same tiers by hand, for every single product, every time.

In practice the same cancellation terms are usually shared across many
products — sometimes because one supplier's whole contract uses one policy
("all of Masons Travel's Transfers: free 30+ days, 25% inside 30, 100% inside
7"), sometimes because Momira applies its own default to a whole product type
regardless of supplier. A LINK captures one of those and gets applied
automatically wherever it fits, the same way a Standing Note (service_notes.py)
already does for voucher text.

TWO SCOPES, both allowed, most-specific-wins when both exist:

  * Supplier-scoped — "this supplier's Transfers" — same shape as Standing
    Notes' (supplier_id, product_type) key.
  * Type-scoped — "all Transfers, any supplier" — a company-wide default
    with no supplier attached at all.

THE DOCUMENT ALWAYS WINS WHEN IT SAYS SOMETHING (confirmed product-owner rule,
2026-08-28): a link only fills in when the freshly extracted/baseline data has
NO cancellation tiers of its own. It is a fallback for silence, never an
override of what the supplier's own document actually states. See
apply_cancellation_link_default()'s docstring for exactly when to call it.

Storage lives in platform_store (same durability story as Standing Notes —
Postgres when DATABASE_URL is set, otherwise a local SQLite file that does
NOT survive a redeploy).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import platform_store

# Stamped on every delivery — see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-01-audit-medium-batch3-builder"

_NAMESPACE = "cancellation_links"

# Matches service_notes.PRODUCT_TYPES / the Upload & Update tool's own labels.
PRODUCT_TYPES = ["ClosedTour", "Ticket", "Transfer", "Transport", "Hotel"]


def _supplier_key(supplier_id: str, product_type: str) -> str:
    return f"supplier|{supplier_id}|{product_type}"


def _type_key(product_type: str) -> str:
    return f"type|{product_type}"


def _clean_tiers(tiers) -> List[Dict[str, Any]]:
    """Same validation/shape as render_cancellation_policy_editor's own save
    handler (ui_components.py) - days as a non-negative int, fee clamped to
    0-100, sorted furthest-out-first. Kept in sync deliberately: a link is
    stored in exactly the shape it'll be dropped into data['cancellation_
    policy_tiers'], so no translation is needed at apply time."""
    clean = []
    for t in (tiers or []):
        if not isinstance(t, dict):
            continue
        days = t.get("days")
        if days is None or (isinstance(days, str) and not days.strip()):
            continue
        try:
            days_int = max(0, int(float(days)))
        except (TypeError, ValueError):
            continue
        try:
            fee = float(t.get("fee_percentage") or 0)
        except (TypeError, ValueError):
            fee = 0.0
        clean.append({"days": days_int, "fee_percentage": max(0.0, min(100.0, fee))})
    clean.sort(key=lambda t: t["days"], reverse=True)
    return clean


def get_supplier_link(supplier_id: str, product_type: str) -> Optional[Dict[str, Any]]:
    """Returns {"tiers", "updated_at"} for this supplier + product type, or None."""
    if not (supplier_id and product_type):
        return None
    return platform_store.get(_NAMESPACE, _supplier_key(str(supplier_id), product_type))


def set_supplier_link(supplier_id: str, product_type: str, tiers) -> bool:
    """Saves (or clears, when tiers is empty) the supplier-scoped link. Returns False if the
    write failed, so the UI can say so rather than implying it's safely stored."""
    if not (supplier_id and product_type):
        return False
    clean = _clean_tiers(tiers)
    key = _supplier_key(str(supplier_id), product_type)
    if not clean:
        return platform_store.delete(_NAMESPACE, key)
    return platform_store.set(_NAMESPACE, key, {
        "tiers": clean,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_type_link(product_type: str) -> Optional[Dict[str, Any]]:
    """Returns {"tiers", "updated_at"} for this product type company-wide, or None."""
    if not product_type:
        return None
    return platform_store.get(_NAMESPACE, _type_key(product_type))


def set_type_link(product_type: str, tiers) -> bool:
    """Saves (or clears) the company-wide, any-supplier link for this product type."""
    if not product_type:
        return False
    clean = _clean_tiers(tiers)
    key = _type_key(product_type)
    if not clean:
        return platform_store.delete(_NAMESPACE, key)
    return platform_store.set(_NAMESPACE, key, {
        "tiers": clean,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def resolve_cancellation_link(supplier_id: str, product_type: str):
    """Returns (tiers, scope_label) for the most specific link that applies, or (None, None)
    when neither scope has one saved.

    CONFIRMED RULE (product owner, 2026-08-28): a link is allowed at EITHER scope, and when
    both exist for the same supplier + product type, the supplier-specific one wins - it's
    the more specific statement about this actual contract, the type-wide one is only meant
    as a fallback for suppliers/products that never got their own."""
    supplier_link = get_supplier_link(supplier_id, product_type)
    if supplier_link and supplier_link.get("tiers"):
        return supplier_link["tiers"], f"this supplier's linked {product_type} cancellation policy"
    type_link = get_type_link(product_type)
    if type_link and type_link.get("tiers"):
        return type_link["tiers"], f"the company-wide {product_type} cancellation policy"
    return None, None


def apply_cancellation_link_default(data: dict, supplier_id: str, product_type: str) -> Optional[str]:
    """Fills data['cancellation_policy_tiers'] from a saved link, but ONLY when the tiers
    already on `data` are empty - a document/live-record value that's already there always
    wins, this never overwrites it.

    CALL THIS EXACTLY ONCE, right after `data` is first populated for this extraction/fetch
    (the same moment floor_start_date_for_new_data() or bump_widget_generation() is normally
    called) - NOT from inside the render loop. render_cancellation_policy_editor() reruns on
    every Streamlit interaction; calling this there would silently re-inject the link every
    time a human deliberately clears the table to say "no policy for this one", making that
    impossible to record.

    Returns the human-readable scope label if a link was applied (for a caption explaining
    where the numbers came from), or None if nothing was applied - either because data already
    had its own tiers, or because no link is saved for this supplier/product type."""
    if not isinstance(data, dict):
        return None
    if data.get("cancellation_policy_tiers"):
        return None
    tiers, scope_label = resolve_cancellation_link(supplier_id, product_type)
    if not tiers:
        return None
    data["cancellation_policy_tiers"] = [dict(t) for t in tiers]
    data["_cancellation_link_scope"] = scope_label
    return scope_label


def list_links() -> List[Dict[str, Any]]:
    """Every saved link across both scopes, for an overview screen. Sorted so the listing is
    stable rather than dependent on storage order."""
    rows = []
    for key, value in platform_store.get_namespace(_NAMESPACE).items():
        scope, _, rest = key.partition("|")
        if scope == "supplier":
            supplier_id, _, product_type = rest.partition("|")
        else:
            supplier_id, product_type = "", rest
        rows.append({
            "scope": "Supplier" if scope == "supplier" else "All suppliers (product type)",
            "supplier_id": supplier_id,
            "product_type": product_type,
            "tiers": (value or {}).get("tiers", []),
            "updated_at": (value or {}).get("updated_at", ""),
        })
    return sorted(rows, key=lambda r: (r["product_type"], r["scope"], r["supplier_id"]))


def render_cancellation_link_editor(supplier_id: str, product_type: str, key_suffix: str = "") -> None:
    """Setup-screen widget (mirrors service_notes.render_standing_note_editor's placement and
    shape) letting a human view/edit BOTH the supplier-scoped and the company-wide link for
    this product type, in one place. Reuses the same Days/Fee% table shape as
    render_cancellation_policy_editor (ui_components.py) so what's entered here matches what
    a human would type directly on a product's own review screen."""
    import pandas as pd
    import streamlit as st

    from ui_components import editable_table, _safe_int, _safe_float

    def _tier_table(tiers):
        rows = [{"Days before arrival (or more)": t.get("days"), "Cancellation Fee %": t.get("fee_percentage")}
                for t in (tiers or []) if isinstance(t, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Days before arrival (or more)", "Cancellation Fee %"])

    def _df_to_tiers(edited_df):
        new_tiers = []
        for _, row in edited_df.iterrows():
            days_val = row.get("Days before arrival (or more)")
            if days_val is None or (isinstance(days_val, float) and pd.isna(days_val)):
                continue
            new_tiers.append({
                "days": _safe_int(days_val, fallback=0),
                "fee_percentage": max(0.0, min(100.0, _safe_float(row.get("Cancellation Fee %"), fallback=0.0))),
            })
        return new_tiers

    supplier_existing = get_supplier_link(supplier_id, product_type)
    type_existing = get_type_link(product_type)
    any_set = bool((supplier_existing or {}).get("tiers")) or bool((type_existing or {}).get("tiers"))

    with st.expander(
        f"🔗 Linked cancellation policy for {product_type}" + ("  ·  currently set" if any_set else ""),
        expanded=any_set,
    ):
        st.caption(
            f"Fills in the Cancellation Policy table automatically whenever a {product_type} "
            f"document/record doesn't state its own terms - the document's own stated policy "
            f"always wins over these when it says something. Two scopes, both optional: a "
            f"supplier-specific one (only this supplier's {product_type}s) and a company-wide "
            f"one (every {product_type}, any supplier, used when a supplier has no link of its "
            f"own). If both are set for the same supplier, the supplier-specific one wins."
        )

        st.markdown(f"**This supplier's {product_type}s** (supplier {supplier_id})")
        supplier_df = _tier_table((supplier_existing or {}).get("tiers"))
        col_config = {
            "Days before arrival (or more)": st.column_config.NumberColumn(min_value=0, step=1),
            "Cancellation Fee %": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
        }

        def _save_supplier(edited_df):
            if set_supplier_link(supplier_id, product_type, _df_to_tiers(edited_df)):
                st.success("Saved.")
            else:
                st.error("Could not save this link - it will NOT apply to future uploads.")

        editable_table(f"Supplier-specific {product_type} policy", supplier_df,
                       f"cxl_supplier_{supplier_id}_{product_type}{key_suffix}",
                       on_save=_save_supplier, column_config=col_config)
        if supplier_existing and supplier_existing.get("updated_at"):
            st.caption(f"Last updated {supplier_existing['updated_at'][:16].replace('T', ' ')} UTC")

        st.markdown(f"**All {product_type}s, any supplier (company-wide default)**")
        type_df = _tier_table((type_existing or {}).get("tiers"))

        def _save_type(edited_df):
            if set_type_link(product_type, _df_to_tiers(edited_df)):
                st.success("Saved.")
            else:
                st.error("Could not save this link - it will NOT apply to future uploads.")

        editable_table(f"Company-wide {product_type} policy", type_df,
                       f"cxl_type_{product_type}{key_suffix}",
                       on_save=_save_type, column_config=col_config)
        if type_existing and type_existing.get("updated_at"):
            st.caption(f"Last updated {type_existing['updated_at'][:16].replace('T', ' ')} UTC")

        if not platform_store.is_durable():
            st.warning("⚠️ No `DATABASE_URL` configured — a link saved here is lost on the next redeploy.")
