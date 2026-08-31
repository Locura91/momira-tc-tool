"""
supplier_images.py — per-supplier "mass upload" images for Transfer/Transport, applied
automatically by route DIRECTION instead of per individual product.

WHY THIS EXISTS: Transfer and Transport never had any image-setting capability in this tool
at all - `images` on both payloads was always an empty list on create, and only ever
preserved (never set) from the live record on update. Typing a per-route photo by hand for
every single Transfer/Transport a supplier has would be needless repetition, because
real-world routes only ever run one of two directions:

  * Airport/Harbor -> Hotel ("arriving" - the guest is being collected and taken to their hotel)
  * Hotel -> Airport/Harbor ("departing" - the guest is being taken from their hotel)

CONFIRMED PRODUCT-OWNER RULE (2026-08-28): a supplier only needs ONE photo per direction (not
a gallery) - e.g. one shot of the shuttle at the airport arrivals hall, one of it outside a
hotel lobby. Uploading a new one for a direction REPLACES whatever was there before, and
REPLACES whatever image is already live on an existing Transfer/Transport too when a fresh
build resolves one for it (see the matching comment in builder.py's build_transfer_payload/
build_transport_payloads) - this is a deliberate choice a human just made, not a
"the document happened to mention a photo" extraction that should defer to what's already
live.

DIRECTION IS DECIDED BY THE ARRIVAL (confirmed product-owner rule): if the ARRIVAL name
mentions "Airport"/"Harbor", the guest is being taken TO the airport/harbor, so this is a
"hotel -> airport/harbor" route (DIRECTION_HOTEL_TO_AIRPORT). If the arrival does NOT mention
either (i.e. the arrival is presumed to be the Hotel), this is an "airport/harbor -> hotel"
route (DIRECTION_AIRPORT_TO_HOTEL). A route where BOTH or NEITHER endpoint mentions
Airport/Harbor can't be classified - confirmed behavior is to warn a human rather than guess
(see classify_direction's docstring), never to silently default to one side.

WHY RAW BYTES, NOT A STORED URL: r2_client.py's own setup instructions recommend an R2
lifecycle rule that auto-expires every uploaded object after ~2 days, on the (correct, for
every OTHER use of that bucket) assumption that an image is only ever needed once, long
enough for Travel Compositor's own fetch during a single publish. A mass-uploaded supplier
image is different - it's meant to be reused across many separate publishes over weeks or
months, so a URL minted once and stored here would silently 404 once that lifecycle rule
fires, breaking every future publish that reused it without any error visible at upload time.
Storing the raw bytes (base64) instead and minting a FRESH R2 URL at every build (see
resolve_and_host_image) sidesteps this entirely - the durable copy never depends on
whatever expiry policy this or any future bucket happens to have.

Storage lives in platform_store (same durability story as Standing Notes/Cancellation Links -
Postgres when DATABASE_URL is set, otherwise a local SQLite file that does NOT survive a
redeploy).
"""

import base64
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import platform_store

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-08-31-child-discount-percentage-cap"

_NAMESPACE = "supplier_images"

PRODUCT_TYPES = ["Transfer", "Transport"]

DIRECTION_AIRPORT_TO_HOTEL = "airport_to_hotel"
DIRECTION_HOTEL_TO_AIRPORT = "hotel_to_airport"
DIRECTIONS = [DIRECTION_AIRPORT_TO_HOTEL, DIRECTION_HOTEL_TO_AIRPORT]

DIRECTION_LABELS = {
    DIRECTION_AIRPORT_TO_HOTEL: "Arriving at the Hotel (from Airport/Harbor)",
    DIRECTION_HOTEL_TO_AIRPORT: "Departing to the Airport/Harbor (from the Hotel)",
}

# Word-boundary so "Harborview Hotel" or "Airporter Lodge" don't false-positive - same
# technique as EDITORIAL_PUBLISHER_PATTERNS in outreach_discovery.py. Matches "harbor" and
# the British "harbour" spelling.
_AIRPORT_HARBOR_PATTERN = re.compile(r"\b(airport|harbou?r)\b", re.IGNORECASE)

_EXT_TO_CONTENT_TYPE_KEYS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


def classify_direction(departure_name: str, arrival_name: str) -> Optional[str]:
    """Returns DIRECTION_AIRPORT_TO_HOTEL, DIRECTION_HOTEL_TO_AIRPORT, or None when the route
    can't be classified (both or neither endpoint mentions Airport/Harbor).

    CONFIRMED RULE (product owner, 2026-08-28): decided by the ARRIVAL. Checking the
    departure side too is what lets this return None for a genuinely ambiguous route instead
    of guessing - a real route should have Airport/Harbor on exactly one side."""
    dep_hit = bool(_AIRPORT_HARBOR_PATTERN.search(departure_name or ""))
    arr_hit = bool(_AIRPORT_HARBOR_PATTERN.search(arrival_name or ""))
    if dep_hit == arr_hit:
        return None
    return DIRECTION_HOTEL_TO_AIRPORT if arr_hit else DIRECTION_AIRPORT_TO_HOTEL


def _key(supplier_id: str, product_type: str, direction: str) -> str:
    return f"{supplier_id}|{product_type}|{direction}"


def get_supplier_image(supplier_id: str, product_type: str, direction: str) -> Optional[Dict[str, Any]]:
    """Returns {"bytes_b64", "ext", "updated_at"} for this supplier + product type +
    direction, or None if nothing is saved."""
    if not (supplier_id and product_type and direction):
        return None
    return platform_store.get(_NAMESPACE, _key(str(supplier_id), product_type, direction))


def set_supplier_image(supplier_id: str, product_type: str, direction: str,
                        image_bytes: bytes, ext: str = "jpg") -> bool:
    """Saves (replacing any previous) the ONE image for this supplier + product type +
    direction. Returns False if the write failed, so the UI can say so rather than implying
    it's safely stored."""
    if not (supplier_id and product_type and direction and image_bytes):
        return False
    ext = (ext or "jpg").strip(".").lower()
    if ext not in _EXT_TO_CONTENT_TYPE_KEYS:
        ext = "jpg"
    return platform_store.set(_NAMESPACE, _key(str(supplier_id), product_type, direction), {
        "bytes_b64": base64.b64encode(image_bytes).decode("ascii"),
        "ext": ext,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def delete_supplier_image(supplier_id: str, product_type: str, direction: str) -> bool:
    if not (supplier_id and product_type and direction):
        return False
    return platform_store.delete(_NAMESPACE, _key(str(supplier_id), product_type, direction))


def resolve_and_host_image(supplier_id: str, product_type: str,
                            departure_name: str, arrival_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Classifies the route's direction, and if a supplier image is saved for it, uploads a
    FRESH copy to R2 (see this module's docstring for why it's never a reused/cached URL) and
    returns its new public URL.

    Returns (url_or_None, direction_or_None):
      * (url, direction)  - classified AND an image is saved for that direction. Use `url`.
      * (None, direction) - classified, but no image saved yet for that direction - the
        caller should say which direction was detected and point at Setup to upload one.
      * (None, None)      - couldn't classify the route at all (see classify_direction) - the
        caller should warn and let a human set an image manually. CONFIRMED (product owner,
        2026-08-28): never guess in this case.

    Imports r2_client lazily so callers that only need classify_direction (e.g. tests) don't
    need R2 credentials configured."""
    direction = classify_direction(departure_name, arrival_name)
    if direction is None:
        return None, None
    saved = get_supplier_image(supplier_id, product_type, direction)
    if not saved or not saved.get("bytes_b64"):
        return None, direction
    try:
        from r2_client import upload_image
        image_bytes = base64.b64decode(saved["bytes_b64"])
        url = upload_image(image_bytes, filename=f"supplier_image.{saved.get('ext', 'jpg')}")
        return url, direction
    except Exception:
        return None, direction


def list_supplier_images() -> list:
    """Every saved supplier image across both product types and both directions, for an
    overview screen. Never includes the raw bytes - just enough to show what's set."""
    rows = []
    for key, value in platform_store.get_namespace(_NAMESPACE).items():
        supplier_id, _, rest = key.partition("|")
        product_type, _, direction = rest.partition("|")
        rows.append({
            "supplier_id": supplier_id,
            "product_type": product_type,
            "direction": direction,
            "direction_label": DIRECTION_LABELS.get(direction, direction),
            "updated_at": (value or {}).get("updated_at", ""),
        })
    return sorted(rows, key=lambda r: (r["product_type"], r["supplier_id"], r["direction"]))


def render_supplier_image_editor(supplier_id: str, product_type: str, key_suffix: str = "") -> None:
    """Setup-screen widget (mirrors service_notes.render_standing_note_editor's / cancellation_
    links.render_cancellation_link_editor's placement) letting a human upload/replace/view the
    one image for each direction, for this supplier + product type."""
    import streamlit as st

    any_set = any(get_supplier_image(supplier_id, product_type, d) for d in DIRECTIONS)

    with st.expander(
        f"🖼️ {product_type} images for this supplier" + ("  ·  currently set" if any_set else ""),
        expanded=any_set,
    ):
        st.caption(
            f"One photo per direction, reused automatically for EVERY {product_type} from this "
            f"supplier - the tool tells the two directions apart by whether \"Airport\"/\"Harbor\" "
            f"appears in the route's ARRIVAL name. Uploading a new photo replaces the old one, "
            f"including on {product_type}s that are already live."
        )
        for direction in DIRECTIONS:
            existing = get_supplier_image(supplier_id, product_type, direction)
            st.markdown(f"**{DIRECTION_LABELS[direction]}**")
            if existing and existing.get("bytes_b64"):
                col_img, col_actions = st.columns([1, 3])
                with col_img:
                    st.image(base64.b64decode(existing["bytes_b64"]), width=160)
                with col_actions:
                    st.caption(f"Last updated {existing.get('updated_at', '')[:16].replace('T', ' ')} UTC")
                    if st.button("🗑️ Remove", key=f"si_remove_{supplier_id}_{product_type}_{direction}{key_suffix}"):
                        if delete_supplier_image(supplier_id, product_type, direction):
                            st.success("Removed.")
                            st.rerun()
                        else:
                            st.error("Could not remove this image.")
            uploaded = st.file_uploader(
                f"Upload/replace — {DIRECTION_LABELS[direction]}", type=["jpg", "jpeg", "png", "webp"],
                key=f"si_upload_{supplier_id}_{product_type}_{direction}{key_suffix}",
            )
            if uploaded is not None:
                ext = uploaded.name.rsplit(".", 1)[-1] if "." in uploaded.name else "jpg"
                if st.button("💾 Save this image", key=f"si_save_{supplier_id}_{product_type}_{direction}{key_suffix}"):
                    if set_supplier_image(supplier_id, product_type, direction, uploaded.getvalue(), ext):
                        st.success("Saved.")
                        st.rerun()
                    else:
                        st.error("Could not save this image - it will NOT apply to future uploads.")
        if not platform_store.is_durable():
            st.warning("⚠️ No `DATABASE_URL` configured — an image saved here is lost on the next redeploy.")
