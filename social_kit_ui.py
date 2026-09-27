"""
Social kit — the Streamlit screen.

Drop-in: one function, `render_social_kit()`, with no dependency on anything
else in the platform. Wire it up wherever the product-type wizard is chosen
(see README_SOCIAL_KIT.md) and it behaves like any other step.

State is kept in st.session_state under the "sk_" prefix so it cannot collide
with the wizard's own keys, and the package is cached per ID so changing the
photograph or the framing does not go back to Travel Compositor.
"""

from __future__ import annotations

MODULE_BUILD = "2026-09-27-social-kit-wired-in"

import streamlit as st

import social_kit as sk


def _package(package_id: str, brand: sk.Brand) -> sk.Package:
    """
    One fetch per ID PER BRAND per session.

    The brand is part of the key because it decides the language Travel
    Compositor is asked for: the same ID is a different package in English
    and in Polish, right down to the destination names. Framing and photo
    changes must never go back to the API.
    """
    cache = st.session_state.setdefault("sk_cache", {})
    key = f"{brand.key}:{package_id}"

    if key not in cache:
        with st.spinner(f"Reading the package from Travel Compositor in {brand.tc_lang}…"):
            cache[key] = sk.fetch(sk.TCClient(), package_id, brand)

    return cache[key]


def render_social_kit() -> None:
    st.header("Social kit")
    st.caption(
        "A Holiday Package ID in, a finished post out. Pick the brand and everything follows: "
        "Momira Travel posts in English and quotes euro, MultiWander posts in Polish and quotes "
        "złoty. The captions are written from the package's own facts — route, length, price and "
        "the real departure dates — and the images come out at the exact size each network wants."
    )

    col_brand, col_id = st.columns([1, 1])

    brand_key = col_brand.selectbox(
        "Brand",
        list(sk.BRANDS),
        format_func=lambda k: f"{sk.BRANDS[k].name} — {sk.BRANDS[k].lang.upper()}, {sk.BRANDS[k].currency}",
        key="sk_brand",
        help="Decides the language Travel Compositor is asked for, the language the caption is "
             "written in, the currency, the wordmark on the image and the hashtag set.",
    )
    brand = sk.BRANDS[brand_key]

    package_id = col_id.text_input("Holiday Package ID", key="sk_id", placeholder="63989764")

    url = st.text_input(
        "Link to put in the post",
        key=f"sk_url_{brand.key}",
        value=brand.url,
        help="The package's permanent page. This is the only thing in the post that has to "
             "survive being copied, so it is worth checking.",
    )

    rate = None
    if brand.converts_to_pln:
        rate = st.number_input(
            "EUR → PLN rate", min_value=0.0, value=4.3, step=0.05, key="sk_rate",
            help="Travel Compositor prices in euro and this brand quotes złoty. Set 0 to quote "
                 "the euro figure instead — a wrong rate is worse than no conversion.",
        )
        rate = rate if rate > 0 else None

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
    st.caption(f"{brand.name} · {brand.lang.upper()} · {brand.currency}")

    pln = rate

    # ---------------------------------------------------------------- text
    st.markdown("### The captions")

    labels = {
        "inspiracja": "Version 1 — Inspiration (feed)",
        "konkret": "Version 2 — The offer (converts)",
        "gbp": "Google Business Profile",
    }

    texts = sk.captions(pack, url, brand, pln)

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
        st.warning("This package has no photographs in Travel Compositor, so no image can be built.")
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
            image = sk.render(pack, fmt, style, photo, focus, float(zoom), brand, pln)
            st.image(image, use_container_width=True)
            st.download_button(
                "Download JPG",
                sk.to_jpeg(image),
                file_name=f"{brand.key}-{pack.id}-{fmt}-{style}.jpg",
                mime="image/jpeg",
                key=f"sk_img_{style}",
                type="primary",
            )
