"""Regression test for a real product-owner request (2026-09-15): "When multiple Service been
detected by the app, we must give the option 'select all' 'select none', on default select all."

app_helpers.render_candidate_filter already existed (2026-09-something, see its own docstring)
and was already wired into flows/multi_transfer.py and flows/multi_transport.py - it renders a
filter box plus "Keep only these" / "Select all" / "Clear all" buttons above a checkbox-per-row
candidate list, and every candidate list already defaulted "selected" to True at every one of
these sites.

What was MISSING was render_candidate_filter itself at four other detected-list screens (so a
human had no bulk way to select/deselect there, only one checkbox at a time), plus one outright
wrong default:
  - flows/multi_ticket.py: the excursion batch-create list (`mt_`) and the ticket batch-update
    list (`mtu_`)
  - flows/multi_tour.py: the ClosedTour Modality candidate list (`mct_modcand_`)
  - flows/multi_modality.py: the standalone Modality batch list (`mm_`)
  - flows/ticket.py: the single-Ticket "multiple excursions detected" list (`tkpv_`) - this one
    ALSO defaulted every row to "selected": False, the opposite of "on default select all" and
    the one outlier among every other detected-list screen in the app.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
wiring - and every flows/*.py module's - is verified by reading their own source text, per this
suite's established pattern.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """Returns app.py's source concatenated with every module under flows/ and app_helpers.py."""
    with open(_APP_PY, "r", encoding="utf-8") as f:
        src = f.read()
    app_helpers_path = os.path.join(os.path.dirname(_APP_PY), "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        with open(app_helpers_path, "r", encoding="utf-8") as f:
            src += chr(10) + f.read()
    flows_dir = os.path.join(os.path.dirname(_APP_PY), "flows")
    if os.path.isdir(flows_dir):
        for _name in sorted(os.listdir(flows_dir)):
            if _name.endswith(".py") and _name != "__init__.py":
                with open(os.path.join(flows_dir, _name), "r", encoding="utf-8") as f:
                    src += chr(10) + f.read()
    return src


def _flow_source(src, def_line):
    start = src.index(def_line)
    next_def = src.find("\ndef ", start + len(def_line))
    end = next_def if next_def != -1 else len(src)
    return src[start:end]


# ======================================================================
# render_candidate_filter now wired into every detected-service list
# ======================================================================
def test_ticket_batch_create_excursion_list_has_bulk_select():
    src = _read_app_py()
    window = _flow_source(src, "def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, "
                          "tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=\"\"):")
    assert 'render_candidate_filter(candidates, "mt", "excursion")' in window


def test_ticket_batch_update_list_has_bulk_select():
    src = _read_app_py()
    idx = src.index('render_candidate_filter(candidates, "mtu", "ticket")')
    assert idx > 0


def test_closed_tour_modality_candidate_list_has_bulk_select():
    src = _read_app_py()
    idx = src.index('render_candidate_filter(candidates, "mct_modcand", "modality")')
    assert idx > 0


def test_standalone_modality_batch_list_has_bulk_select():
    src = _read_app_py()
    idx = src.index('render_candidate_filter(candidates, "mm", "modality")')
    assert idx > 0


def test_single_ticket_multiple_excursions_detected_list_has_bulk_select():
    src = _read_app_py()
    idx = src.index('render_candidate_filter(tkpv_selection, "tkpv", "excursion")')
    assert idx > 0


# ======================================================================
# The one wrong default is fixed
# ======================================================================
def test_single_ticket_pending_variant_selection_now_defaults_to_selected():
    src = _read_app_py()
    marker = 'st.session_state.tk_pending_variant_selection = ['
    idx = src.index(marker)
    window = src[idx:idx + 400]
    assert '"selected": True' in window
    assert '"selected": False' not in window


# ======================================================================
# Sites that were ALREADY correct (already wired + already defaulting True) stay that way
# ======================================================================
def test_multi_transfer_and_multi_transport_still_have_bulk_select():
    src = _read_app_py()
    assert 'render_candidate_filter(candidates, "xtf", "transfer")' in src
    assert 'render_candidate_filter(candidates, "xtp", "transport")' in src
