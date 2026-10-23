"""
Shared UI helpers for duplicate-and-swap flows (transfer, transport).
Keeps the description/field editing pattern in one place so both flows
stay in sync without copy-pasting.
"""
import streamlit as st

MODULE_BUILD = "2026-10-22-multi-file-tickets-fix"


def render_html_field_with_ai_swap_status(
    label: str,
    field_key: str,
    swap_report: dict,
    current_value: str,
    html_to_plain,
    plain_to_html,
    session_key: str,
    field_label_for_warning: str | None = None,
) -> str:
    """
    Shows the AI-swap status banner for *field_key*, then renders a text_area
    for human editing of the HTML-backed field.

    swap_report[field_key] == "ai"  → info banner (AI rewrote it automatically)
    swap_report[field_key] is False → warning banner (AI made no change either)
    anything else                   → no banner

    Returns the new HTML value after the human edits it.

    html_to_plain / plain_to_html: conversion callables (same ones used everywhere
    else in these flows - _html_to_plain_for_editing / _plain_to_html_for_saving).
    field_label_for_warning: human-readable field name for the warning message;
        defaults to label.lower() when not given.
    """
    human_label = field_label_for_warning or label.lower()
    status = swap_report.get(field_key)
    if status == "ai":
        st.info(
            "✨ Rewritten automatically by AI for the new direction - double-check it "
            "reads correctly before publishing."
        )
    elif status is False:
        st.warning(
            f"⚠️ Couldn't auto-swap the {human_label} (even the AI rewrite made no "
            "change) - check it reads correctly for the new direction before publishing."
        )
    st.caption(
        "Formatting (paragraphs, bullet points) is handled automatically - just write "
        "plain text, with a blank line between paragraphs, and a bullet list as a blank "
        "line before and after it with each item on its own line starting with \"- \"."
    )
    new_plain = st.text_area(label, value=html_to_plain(current_value), key=session_key)
    return plain_to_html(new_plain)
