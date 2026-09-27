"""
Social kit — the Streamlit screen.

Drop-in: one function, `render_social_kit()`, with no dependency on anything
else in the platform. Wire it up wherever the product-type wizard is chosen
(see README_SOCIAL_KIT.md) and it behaves like any other step.

State is kept in st.session_state under the "sk_" prefix so it cannot collide
with the wizard's own keys, and the package is cached per ID so changing the
photograph or the framing does not go back to Travel Compositor.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-27, verbatim): "in the app we use only momira travel
with english and euro. No need to mention multiwander.com there." This screen is hard-wired to
`sk.BRANDS["momira"]` - no brand picker, no PLN-rate input, no MultiWander wording anywhere in
its captions or help text. social_kit.py itself still carries both brands in `BRANDS` (removing
MultiWander there would break the module's own "use it as a library" story for a future
scheduled job, per README_SOCIAL_KIT.md) - only this app screen is Momira-only.
"""

from __future__ import annotations

MODULE_BUILD = "2026-09-27-social-kit-momira-only-and-image-fallback"

import streamlit as st

import social_kit as sk


_BRAND = sk.BRANDS["momira"]


def _package(package_id: str, brand: sk.Brand) -> sk.Package:
    """
    One fetch per ID per session.

    Kept keyed by brand (as social_kit.py's own caching contract expects)
    even though this screen only ever passes _BRAND, so the cache dict has
    the same shape whether one brand or several are ever fetched through it.
    """
    cache = st.session_state.setdefault("sk_cache", {})
    key = f"{brand.key}:{package_id}"

    if key not in cache:
        with st.spinner("Reading the package from Travel Compositor…"):
            cache[key] = sk.fetch(sk.TCClient(), package_id, brand)

    return cache[key]


def render_social_kit() -> None:
    brand = _BRAND

    st.header("Social kit")
    st.caption(
        "A Holiday Package ID in, a finished post out. The captions are written from the "
        "package's own facts — route, length, price and the real departure dates — and the "
        "images come out at the exact size each network wants."
    )

    package_id = st.text_input("Holiday Package ID", key="sk_id", placeholder="63989764")

    url = st.text_input(
        "Link to put in the post",
        key="sk_url",
        value=brand.url,
        help="The package's permanent page. This is the only thing in the post that has to "
             "survive being copied, so it is worth checking.",
    )

    if not package_id:
        st.info("Enter an ID to begin.")
        return

    try:
        pack = _package(package_id, brand)
    except sk.TCError as error:
        st.error(str(error))
        return

    if not pack.title:
        st.warning("Travel Compositor returned no title for that ID — check it is a published package.")
        return

    st.subheader(pack.title)

    # ---------------------------------------------------------------- text
    st.markdown("### The captions")

    labels = {
        "inspiracja": "Version 1 — Inspiration (feed)",
        "konkret": "Version 2 — The offer (converts)",
        "gbp": "Google Business Profile",
    }

    texts = sk.captions(pack, url, brand, pln_rate=None)

    for key, tab in zip(labels, st.tabs(list(labels.values()))):
        with tab:
            st.text_area(labels[key], texts[key], height=380, key=f"sk_text_{key}")
            st.caption(f"{len(texts[key])} characters")
            st.download_button(
                "Download as .txt",
                texts[key].encode("utf-8"),
                file_name=f"{brand.key}-{pack.id}-{key}.txt",
                mime="text/plain",
                key=f"sk_dl_{key}",
            )

    # --------------------------------------------------------------- image
    st.markdown("### The post")

    if not pack.gallery:
        st.warning(
            "No photographs were found for this package. Travel Compositor's exact field name "
            "for Holiday Package images was never confirmed against a live response (see "
            "README_SOCIAL_KIT.md) - open 'Raw package data' below and check for a field that "
            "looks like a photo URL; if you find one, send it over so the lookup can be fixed."
        )
        with st.expander("🔍 Raw package data (for diagnosing the missing photos)"):
            st.caption(
                "The exact JSON Travel Compositor returned for this package. Look for anything "
                "that looks like an image URL (ends in .jpg/.png/.webp) and note which field "
                "it's under."
            )
            st.json(pack.raw.get("info") or {})
            if pack.raw.get("detail"):
                st.caption("Detail response:")
                st.json(pack.raw["detail"])
        return

    choice = st.selectbox(
        "Photograph",
        range(len(pack.gallery[:6])),
        format_func=lambda i: f"Photo {i + 1}",
        key="sk_photo",
    )

    frame_col, focus_col, zoom_col = st.columns(3)

    fmt = frame_col.selectbox(
        "Format", list(sk.FORMATS), format_func=lambda k: f"{sk.FORMATS[k][2]} ({sk.FORMATS[k][0]}×{sk.FORMATS[k][1]})",
        key="sk_format",
    )
    focus = focus_col.radio("Keep", ["top", "center", "bottom"], index=1, horizontal=True, key="sk_focus")
    zoom = zoom_col.select_slider("Zoom", [1.0, 1.25, 1.5, 2.0], value=1.0, key="sk_zoom")

    st.caption(
        "Travel Compositor supplies these photographs at roughly 900 × 720 pixels and there is no "
        "larger version behind them. Where a frame cannot be filled without stretching the picture "
        "too far — a vertical story, usually — the photograph is shown whole over a blurred copy of "
        "itself rather than enlarged past what it can carry."
    )

    try:
        photo = sk.load_photo(pack.gallery[choice])
    except Exception as error:  # noqa: BLE001 — a bad image URL must not take the page down
        st.error(f"That photograph could not be loaded: {error}")
        return

    columns = st.columns(len(sk.STYLES))

    for column, (style, style_label) in zip(columns, sk.STYLES.items()):
        with column:
            st.markdown(f"**{style_label}**")
            image = sk.render(pack, fmt, style, photo, focus, float(zoom), brand, pln_rate=None)
            st.image(image, use_container_width=True)
            st.download_button(
                "Download JPG",
                sk.to_jpeg(image),
                file_name=f"{brand.key}-{pack.id}-{fmt}-{style}.jpg",
                mime="image/jpeg",
                key=f"sk_img_{style}",
                type="primary",
            )
