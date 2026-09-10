"""Tests for the Create/Update navigation split (product owner, 2026-09-10), from a screenshot
of "Ticket -- Step 2: What do you want to do?" showing all 5 actions under "Create a new
product":

    "we must change the order of the whole structure ... in Step 1, When creating a new product
    the app shall only allow 'Create new SERVICE + 1 Modality' and 'Add new Modality to existing
    SERVICE'. Nr. 3 and 4 and 5 must be removed from this points, as this is more Updating
    existing product. We must define between create new service and Update existing Service."

Root cause: render_ticket_flow (reachable ONLY via "Create a new product -> Ticket") and the
generic ClosedTour Step 2 (reachable ONLY via "Create a new product -> ClosedTour") both built
their action radio from the FULL action-labels dict, which mixes create actions (1/2) with
update-only ones (3/4/5 for Ticket, 3/4 for ClosedTour) - so a screen entered specifically to
CREATE something also offered to update an existing one, and vice versa nowhere else offered
action 5 (Ticket's batch-update) at all except through this create-only screen.

Fix: TICKET_CREATE_ACTION_KEYS / CLOSEDTOUR_CREATE_ACTION_KEYS restrict each screen's own radio
to create-only actions; the update-only actions remain fully defined (still needed for the
post-pick summary label, and for "Update existing Service", which now reaches ALL of them -
update_ticket/update_option/update_tickets_batch for Ticket, update_tour/update_option for
ClosedTour - through its own entry points instead.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup) - see
test_2026_09_01_medium_batch1_app_py.py's own docstring for the established source-shape-check
pattern this suite follows.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line):
    start = src.index(def_line)
    rest = src[start + len(def_line):]
    end_offset = rest.index("\ndef ")
    return src[start:start + len(def_line) + end_offset]


# ---------------------------------------------------------------------------
# The create-only action key sets themselves
# ---------------------------------------------------------------------------

def test_ticket_create_action_keys_is_exactly_create_and_add_option():
    src = _read_app_py()
    assert 'TICKET_CREATE_ACTION_KEYS = ("create", "add_option")' in src


def test_closedtour_create_action_keys_is_exactly_create_and_add_option():
    src = _read_app_py()
    assert 'CLOSEDTOUR_CREATE_ACTION_KEYS = ("create", "add_option")' in src


def test_ticket_action_labels_dict_itself_still_has_all_five_actions():
    # The update-only actions must stay DEFINED (still used elsewhere - the post-pick summary
    # label, and "Update existing Service") - only the Ticket wizard's OWN radio is trimmed.
    src = _read_app_py()
    labels_block = src.split("TICKET_ACTION_LABELS = {")[1].split("\n}")[0]
    for key in ("create", "add_option", "update_ticket", "update_option", "update_tickets_batch"):
        assert f'"{key}":' in labels_block


def test_action_labels_dict_itself_still_has_all_four_closedtour_actions():
    src = _read_app_py()
    labels_block = src.split("\nACTION_LABELS = {")[1].split("\n}")[0]
    for key in ("create", "add_option", "update_tour", "update_option"):
        assert f'"{key}":' in labels_block


# ---------------------------------------------------------------------------
# render_ticket_flow's own Step 2 radio - create-only now
# ---------------------------------------------------------------------------

def test_ticket_wizard_step2_radio_uses_the_create_only_key_set():
    src = _read_app_py()
    fn = _function_source(src, "def render_ticket_flow(client):")
    assert '"Choose one:", list(TICKET_CREATE_ACTION_KEYS),' in fn
    assert "list(TICKET_ACTION_LABELS.keys())" not in fn


# ---------------------------------------------------------------------------
# The generic (ClosedTour-only) Step 2 radio - create-only now
# ---------------------------------------------------------------------------

def test_closedtour_step2_radio_uses_the_create_only_key_set():
    src = _read_app_py()
    assert "list(CLOSEDTOUR_CREATE_ACTION_KEYS)," in src
    assert "list(ACTION_LABELS.keys())" not in src


# ---------------------------------------------------------------------------
# "Update existing Service" now offers Ticket's batch-content-update (action 5) as its own
# mode, not exclusively through the (now create-only) Ticket wizard
# ---------------------------------------------------------------------------

def test_update_existing_service_offers_a_bulk_ticket_content_update_mode():
    src = _read_app_py()
    fn = _function_source(src, "def render_update_refresh_flow(client):")
    assert "Bulk update multiple Tickets' content from one document" in fn
    assert 'ticket_mode.startswith("Bulk update multiple Tickets")' in fn
    # It hands off through the same pre-set-action mechanism update_ticket/update_option
    # already use (see _render_update_refresh_coded_service), just without a per-code pick.
    assert 'st.session_state.tk_cfg_action = "update_tickets_batch"' in fn


def test_coded_service_screen_no_longer_claims_batch_update_is_reached_via_the_ticket_wizard():
    # The stale comment used to say "Reach it via the normal Ticket action menu (Step 2)
    # instead" - that's no longer true once Step 2 is create-only; the comment must be updated,
    # not just the code, so a future reader isn't misled back to a screen that no longer offers it.
    src = _read_app_py()
    assert "Reach it via the normal Ticket action menu (Step 2) instead" not in src


# ---------------------------------------------------------------------------
# Top-level naming: "Upload" -> "Create" (product owner: "Ceate (better than upload)")
# ---------------------------------------------------------------------------

def test_top_level_tool_renamed_from_upload_to_create():
    src = _read_app_py()
    assert 'TOOL_UPLOAD = "📤 Create & Update Products"' in src
    assert "Upload & Update Products" not in src
