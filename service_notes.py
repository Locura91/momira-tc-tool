"""
service_notes.py — human-written notes that get attached to published services.

Two kinds, because two genuinely different needs came up:

  1. A ONE-OFF note on a single service. "This particular excursion now meets at
     the side entrance." Typed while reviewing that service, used once, gone.

  2. A STANDING note for every service of one type from one supplier. "The pickup
     spot for all Masons Travel transfers moved to the new terminal." Written
     once and automatically attached to every Transfer for that supplier from
     then on - including ones uploaded next month, by someone who never heard
     about the change.

The second is the one that actually needed building. Without it, a change
affecting forty transfers means either editing forty services by hand or
remembering to retype the same sentence forty times - and whoever does the next
upload has no way of knowing the note should be there at all. Standing notes
live in platform_store (Postgres when DATABASE_URL is set), so they survive
redeploys and are shared by everyone using the platform.

Both kinds end up in the same place in the payload: appended to the service's
Voucher Remarks, which is the field staff and customers actually read. Transport
is the exception - it has no voucher-remarks field of its own, so its notes go
into the description, the same fallback its cancellation text already uses.

Notes are additive and never overwrite extracted content. A note is extra
context a human knows and the document doesn't, not a correction to it.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import platform_store

_NAMESPACE = "standing_notes"

# The product types a standing note can be scoped to. These match the labels the
# Upload & Update tool uses, so a note written against "Transfer" applies to exactly
# what the operator saw on screen.
PRODUCT_TYPES = ["ClosedTour", "Ticket", "Transfer", "Transport", "Hotel"]


def _key(supplier_id: str, product_type: str) -> str:
    return f"{supplier_id}|{product_type}"


def get_standing_note(supplier_id: str, product_type: str) -> Optional[Dict[str, Any]]:
    """Returns {"text", "updated_at"} for this supplier + product type, or None."""
    if not (supplier_id and product_type):
        return None
    return platform_store.get(_NAMESPACE, _key(str(supplier_id), product_type))


def get_standing_note_text(supplier_id: str, product_type: str) -> str:
    note = get_standing_note(supplier_id, product_type)
    return (note or {}).get("text", "") or ""


def set_standing_note(supplier_id: str, product_type: str, text: str) -> bool:
    """Saves (or clears, when text is blank) the standing note for this supplier and
    product type. Returns False if the write failed, so the UI can say so rather than
    implying the note is safely stored."""
    if not (supplier_id and product_type):
        return False
    clean = (text or "").strip()
    if not clean:
        return platform_store.delete(_NAMESPACE, _key(str(supplier_id), product_type))
    return platform_store.set(_NAMESPACE, _key(str(supplier_id), product_type), {
        "text": clean,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def list_standing_notes() -> List[Dict[str, Any]]:
    """Every standing note across all suppliers, for an overview screen. Sorted so the
    listing is stable rather than dependent on storage order."""
    rows = []
    for key, value in platform_store.get_namespace(_NAMESPACE).items():
        supplier_id, _, product_type = key.partition("|")
        rows.append({
            "supplier_id": supplier_id,
            "product_type": product_type,
            "text": (value or {}).get("text", ""),
            "updated_at": (value or {}).get("updated_at", ""),
        })
    return sorted(rows, key=lambda r: (r["supplier_id"], r["product_type"]))


def render_notes_editor(supplier_id: str, product_type: str, data: dict, key_suffix: str = "") -> str:
    """Renders the manual-notes block and writes the composed result onto `data` under
    'manual_notes', which is where builder._with_manual_notes() picks it up.

    Called by every Upload & Update flow, so the two note types look and behave the same
    everywhere. Returns the composed text as well, for a caller that wants to show it.

    Imported inside the function so this module stays importable without Streamlit -
    the builders and tests use compose_manual_notes() with no UI involved."""
    import streamlit as st

    st.markdown("##### Manual notes")
    st.caption("Anything a person knows that the supplier's document doesn't say. Notes are "
              "**added to** the Voucher Remarks — they never replace the cancellation policy or "
              "anything else extracted from the document.")

    standing_key = f"sn_standing_{supplier_id}_{product_type}{key_suffix}"
    existing = get_standing_note(supplier_id, product_type)
    existing_text = (existing or {}).get("text", "")

    with st.expander(
        f"📌 Standing note — applies to EVERY {product_type} from this supplier"
        + ("  ·  currently set" if existing_text else ""),
        expanded=bool(existing_text),
    ):
        st.caption(f"Use this when something changed for all of them at once — a moved pickup "
                  f"point, revised cancellation terms. Saved against supplier {supplier_id} and "
                  f"applied automatically to every {product_type} you upload from now on, "
                  f"including by someone who wasn't told about the change.")
        new_standing = st.text_area("Standing note", value=existing_text, height=90,
                                     key=standing_key, label_visibility="collapsed")
        scol1, scol2 = st.columns([1, 3])
        with scol1:
            if st.button("💾 Save", key=f"{standing_key}_save"):
                if set_standing_note(supplier_id, product_type, new_standing):
                    st.success("Saved." if new_standing.strip() else "Standing note cleared.")
                else:
                    # Never imply it's stored when the write failed - the whole value of a
                    # standing note is that it's still there on the next upload.
                    st.error("Could not save the standing note — it will NOT apply to future uploads.")
        with scol2:
            if existing and existing.get("updated_at"):
                st.caption(f"Last updated {existing['updated_at'][:16].replace('T', ' ')} UTC")
        if not platform_store.is_durable():
            st.warning("⚠️ No `DATABASE_URL` configured — a standing note saved here is lost on "
                       "the next redeploy.")

    one_off = st.text_area(
        "Note for this service only", value=data.get("one_off_note", ""), height=80,
        key=f"sn_oneoff_{product_type}{key_suffix}",
        placeholder="e.g. Meets at the side entrance until the works finish",
    )
    data["one_off_note"] = one_off

    composed = compose_manual_notes(supplier_id, product_type, one_off)
    data["manual_notes"] = composed
    if composed:
        st.caption("**Will be added to the voucher:**")
        st.info(composed)
    return composed


def compose_manual_notes(supplier_id: str, product_type: str, one_off_note: str = "") -> str:
    """The text to attach to one service: the supplier-wide standing note plus any
    one-off note typed for this service.

    Standing note first, because it's the more general statement ("pickup moved") and
    the one-off is usually a refinement of the specific case. Deduplicated so that
    pasting the standing note into the one-off box - an easy thing to do - doesn't
    produce the same sentence twice on the voucher."""
    parts = []
    standing = get_standing_note_text(supplier_id, product_type)
    if standing:
        parts.append(standing)
    one_off = (one_off_note or "").strip()
    if one_off and one_off != standing:
        parts.append(one_off)
    return "\n\n".join(parts)
