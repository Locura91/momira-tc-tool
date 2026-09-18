"""Regression tests for a real product-owner-reported publish failure (2026-09-18, verbatim
Travel Compositor error, hit via the "New Transport - duplicate an existing one & swap
destinations" flow):

    Couldn't publish transport Alexandria - Cairo by Train (return): modalityAvailableWhenActive:
    You must add at least one modality!

Root cause: Travel Compositor rejects a Transport parent CREATE call when active=true and it has
zero Options (Modalities) - which every brand-new Transport genuinely has at the exact moment of
its first create call, since its Options can only be created afterward, against the parent's real
id. flows/duplicate_transport.py's publish handler already zeroed out optionCodes on that first
create call (an earlier, separate fix for a "null PK" error) but still sent active=true (inherited
from builder.build_transport_swap_payload, which always sets active=True) - so the exact same
create call that avoided the null-PK problem walked straight into this second, different Travel
Compositor validation rule instead.

Fix (same two-step shape as the earlier optionCodes fix, and the same shape Hotel's own two-phase
build already uses for its analogous forward-reference problem): create the parent with
optionCodes=[] AND active=False, create every Option under the new id, then a follow-up PUT links
the real optionCodes and flips active back to True - by then the Transport genuinely has at least
one modality, so Travel Compositor accepts it.

The exact same root cause applies to flows/multi_transport.py's own Transport CREATE path (a
brand-new Transport, not a duplicate) - that path had simply never been exercised for a genuinely
new Transport before (per this file's own long-standing comment: "Transport has had NO working
create path in this app... only assumed to mirror the update case"), so it's fixed the same way
here, proactively, before it can be hit for real.
"""
import os


def _read(rel_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *rel_path.split("/"))
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# flows/duplicate_transport.py
# ---------------------------------------------------------------------------------------------

def _dtp_publish_block():
    src = _read("flows/duplicate_transport.py")
    start = src.index('if st.button("🚀 Publish — CREATE new transport"')
    return src[start:]


def test_duplicate_transport_creates_parent_inactive_with_no_option_codes():
    block = _dtp_publish_block()
    create_idx = block.index('create_payload = dict(payload)')
    create_snippet = block[create_idx:create_idx + 250]
    assert 'create_payload["optionCodes"] = []' in create_snippet
    assert 'create_payload["active"] = False' in create_snippet
    # The active=False assignment must land on create_payload BEFORE it's sent, not after.
    call_idx = block.index("client.create_transport(supplier_id, create_payload)")
    assert create_idx < call_idx


def test_duplicate_transport_link_payload_explicitly_reactivates():
    block = _dtp_publish_block()
    link_idx = block.index('link_payload = dict(payload)')
    link_snippet = block[link_idx:link_idx + 900]
    assert 'link_payload["optionCodes"] = created_codes' in link_snippet
    assert 'link_payload["active"] = True' in link_snippet


def test_duplicate_transport_warns_when_every_bracket_fails_leaving_it_inactive():
    block = _dtp_publish_block()
    assert "left **inactive**" in block
    assert "be active with no modalities" in block


def test_duplicate_transport_success_message_only_fires_when_something_was_actually_created():
    block = _dtp_publish_block()
    success_idx = block.index("st.success(f\"✅ Published successfully")
    preceding = block[:success_idx]
    # Must be gated behind created_codes (not just "no failures", which used to be true even
    # when there were zero options to begin with or all of them failed before this fix).
    assert "elif created_codes:" in preceding[-60:]


# ---------------------------------------------------------------------------------------------
# flows/multi_transport.py
# ---------------------------------------------------------------------------------------------

def _xtp_publish_block():
    src = _read("flows/multi_transport.py")
    start = src.index("# STAGE 1 - the parent transport record.")
    return src[start:]


def test_multi_transport_fresh_create_uses_inactive_no_option_codes_payload():
    block = _xtp_publish_block()
    else_idx = block.index("else:\n", block.index("if chosen_existing_id:"))
    create_snippet = block[else_idx:else_idx + 1950]
    assert 'create_payload = dict(build_result["transport_payload"])' in create_snippet
    assert 'create_payload["optionCodes"] = []' in create_snippet
    assert 'create_payload["active"] = False' in create_snippet
    assert "client.create_transport(supplier_id, create_payload)" in create_snippet


def test_multi_transport_update_path_is_unchanged_by_the_fix():
    block = _xtp_publish_block()
    if_idx = block.index("if chosen_existing_id:")
    else_idx = block.index("else:", if_idx)
    if_snippet = block[if_idx:else_idx]
    # The UPDATE branch (an existing Transport) must keep sending the plain transport_payload
    # straight through - this fix is only for the fresh-CREATE branch.
    assert 'client.update_transport(supplier_id, build_result["transport_payload"])' in if_snippet


def test_multi_transport_stage3_links_and_reactivates_only_for_fresh_creates():
    block = _xtp_publish_block()
    stage3_idx = block.index("if not chosen_existing_id:")
    stage3_snippet = block[stage3_idx:stage3_idx + 1300]
    assert 'link_payload = dict(build_result["transport_payload"])' in stage3_snippet
    assert 'link_payload["optionCodes"] = created_codes' in stage3_snippet
    assert 'link_payload["active"] = True' in stage3_snippet


def test_multi_transport_tracks_created_codes_separately_from_failures():
    block = _xtp_publish_block()
    stage2_idx = block.index("option_failures = []")
    stage2_snippet = block[stage2_idx:stage2_idx + 950]
    assert "created_codes = []" in stage2_snippet
    assert "created_codes.append(a[\"code\"])" in stage2_snippet


def test_multi_transport_warns_when_every_bracket_fails_on_fresh_create():
    block = _xtp_publish_block()
    assert "left **inactive**" in block
    assert "be active with no modalities" in block
