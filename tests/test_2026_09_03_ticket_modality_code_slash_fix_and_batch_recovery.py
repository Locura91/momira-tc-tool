"""Regression tests for a real production failure (product owner, 2026-09-03):

    "following error is new within the ticket creation: [...] 1 validation error for
    TicketHumanPreConfig modality_code Value error, Modality Code cannot contain '/' or '\\' -
    it becomes part of a URL and breaks lookups [...] input_value='Turtles/Tortoises: Three
    Island Cruise (Praslin)'"

Root cause: a same-day (2026-09-03) product-owner rule made the multi-ticket batch flow default
a blank Modality Code to the excursion's own name (render_multi_ticket_flow's PHASE 1 detection
and PHASE 2 auto-sync). That default fed the raw excursion label straight into modality_code
without running it through the SAME sanitizer (_clean_modality_code) every other AI-suggested
Modality Code call site in this file already uses - so any excursion name containing "/" (e.g.
"Turtles/Tortoises: Three Island Cruise (Praslin)") broke Travel Compositor's real API validation
the moment "Publish all" tried to build a TicketHumanPreConfig for it.

Second, compounding bug reported in the same message: "i have an error here, but I can not go
back to change the error - this is very bad, the human must be able to go back and solve the
error and not start completely new over." The PHASE 4 "Publish all (one by one)" loop's only
recovery mechanism (mt_failed_items) covered exactly one failure mode - Ticket created, but its
Modality option POST then failed. A TicketHumanPreConfig(...) validation error (exactly the bug
above) happens BEFORE the Ticket is created at all, so it fell into a generic `except Exception`
with no recovery captured - the item just vanished, and "Start a new batch" (wiping every ticket
in the whole batch) was the only way forward.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
logic is verified by reading its own source text and by testing the shared `_clean_modality_code`
helper directly via a standalone re-implementation (matching the file's own definition), per this
suite's established pattern for testing app.py-only functions.
"""
import os

MODULE_BUILD = "2026-09-03-ticket-modality-code-slash-fix-and-batch-recovery"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _clean_modality_code(raw_code):
    # Exact same logic as app.py's _clean_modality_code - re-implemented here since app.py
    # cannot be imported directly in a test process.
    return "".join(c for c in (raw_code or "") if c not in "/\\+-.")


# ======================================================================
# _clean_modality_code itself (already existed, sanity-checked against the real reported values)
# ======================================================================
def test_clean_modality_code_strips_forward_slash():
    assert "/" not in _clean_modality_code("Turtles/Tortoises: Three Island Cruise (Praslin)")


def test_clean_modality_code_strips_the_second_reported_value():
    cleaned = _clean_modality_code("Island Duo: Praslin and La Digue B/B (Mahe)")
    assert "/" not in cleaned
    assert "\\" not in cleaned


# ======================================================================
# app.py wiring - the default-to-excursion-name Modality Code now goes through the sanitizer
# ======================================================================
def test_phase1_default_modality_code_is_sanitized():
    src = _read_app_py()
    assert "_clean_modality_code(_excursion_label) or _base_modality_name" in src


def test_phase2_autosync_modality_code_is_sanitized():
    src = _read_app_py()
    assert "_clean_modality_code(cand[\"label\"].strip())" in src


def test_phase2_touched_detection_compares_against_the_cleaned_label():
    # Guards a second-order bug this fix could otherwise introduce: comparing the (now
    # sanitized) modality_code against the RAW label would make _modcode_touched become True on
    # the very first render for any label containing "/" or similar, permanently breaking the
    # "stay in sync while the human hasn't edited it" behavior the 2026-09-03 rule promised.
    src = _read_app_py()
    assert "_clean_label = _clean_modality_code((cand.get(\"label\") or \"\").strip())" in src
    assert 'if cand["modality_code"].strip() != _clean_label:' in src


# ======================================================================
# Batch "Publish all" recovery - a Ticket that failed before it was even created is now
# recoverable, not silently dropped
# ======================================================================
def test_precreate_failed_items_list_exists():
    src = _read_app_py()
    assert "mt_precreate_failed_items" in src


def test_precreate_failure_is_parked_for_recovery_on_payload_error():
    src = _read_app_py()
    assert "_park_for_recovery()" in src


def test_precreate_failure_is_parked_when_ticket_creation_itself_fails():
    src = _read_app_py()
    # The exact API-error branch (result = client.create_ticket(...); if "error" in result: ...)
    # must also park for recovery, not just silently continue.
    assert 'result = client.create_ticket(supplier_id, creation_payload)\n                        if "error" in result:\n                            show_publish_error(f"create **{q[\'ticket_code\']}**", result)\n                            _park_for_recovery()' in src


def test_generic_exception_handler_parks_for_recovery_unless_ticket_already_created():
    # This is the exact spot the reported pydantic ValidationError landed - a
    # TicketHumanPreConfig(...) construction failure happens before _ticket_was_created is ever
    # set True, so it must be parked; a failure AFTER the ticket exists (e.g. the deactivate
    # call) must NOT be re-parked, since retrying that would create a duplicate ticket.
    src = _read_app_py()
    assert "_ticket_was_created = False" in src
    assert "_ticket_was_created = True" in src
    assert "if not _ticket_was_created:\n                            _park_for_recovery()" in src


def test_recovery_box_lets_the_human_edit_the_modality_code():
    src = _read_app_py()
    assert 'st.subheader(f"⚠️ {len(st.session_state.mt_precreate_failed_items)} ticket(s) couldn' in src
    assert 'pf["modality_code"] = st.text_input(\n                            "Modality Code"' in src


def test_recovery_box_has_its_own_retry_button_that_recreates_the_ticket():
    src = _read_app_py()
    assert 'f"🔄 Retry creating `{pf[\'ticket_code\']}`"' in src
    assert "client.create_ticket(supplier_id, retry_creation_payload)" in src


def test_start_a_new_batch_also_clears_the_new_recovery_list():
    src = _read_app_py()
    assert '"mt_failed_items",\n                       "mt_precreate_failed_items"' in src
