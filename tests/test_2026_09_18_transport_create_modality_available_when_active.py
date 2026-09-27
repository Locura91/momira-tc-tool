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

The exact same root cause applied to flows/multi_transport.py's own Transport CREATE path (a
brand-new Transport, not a duplicate) - that path had simply never been exercised for a genuinely
new Transport before (per this file's own long-standing comment: "Transport has had NO working
create path in this app... only assumed to mirror the update case"), so it was fixed the same way
there too, proactively, before it could be hit for real. flows/multi_transport.py (render_
multi_transport_flow) was itself removed 2026-09-27 as a dead entry point - imported into app.py
but never actually called, superseded by flows/transport_duplicate_and_create.py - taking the
5 tests that verified this fix inside it with it. The still-live flows/duplicate_transport.py
tests below are unaffected.
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

# flows/multi_transport.py and the 5 tests that verified this same fix inside it were removed
# 2026-09-27 - see this file's own top docstring.
