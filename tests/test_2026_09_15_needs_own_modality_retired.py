"""Regression tests for a real product-owner decision (2026-09-15): "When Ticket creation and
Supplement says: Needs own Modality, we can ignore that information - we want to make the app
simple and handy for humans in the future."

This retires the "Needs own Modality?" checkbox / is_priced_choice flag introduced on 2026-08-25
(see test_2026_08_25_ticket_priced_choice_extras.py's original docstring for that history): a row
flagged is_priced_choice=True used to be EXCLUDED from what publishes on a Ticket's one Modality
and reported back separately (excluded_language_choice_extras) for a human to set up as its own
Modality by hand. Per this decision, that distinction and the exclusion behavior it drove are gone
- every modality_supplements row now always publishes onto the one Modality a Ticket creates,
whatever kind of extra it is (a dated seasonal surcharge, a foreign-language guide, a vehicle
upgrade - all the same going forward), and is_priced_choice (if still present on an old saved
draft) is simply ignored, never read.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
UI wiring is verified by reading its own source text, per this suite's established pattern.
"""
import os

import builder
from test_builder_ticket import make_pre_config, minimal_ticket_data

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """Returns app.py's source concatenated with every module under flows/ and app_helpers.py -
    Phase 1 (2026-09-15) started splitting render_*_flow functions out of app.py into flows/*.py
    and app_helpers.py, verbatim/zero-behaviour-change, so source-text assertions that used to
    find their target inside app.py alone now need to see the split-out modules too.
    """
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


# ======================================================================
# builder.build_ticket_payloads - is_priced_choice is a pure no-op now
# ======================================================================
def test_an_is_priced_choice_true_row_now_publishes_like_any_other(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    names = [s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]]
    assert "French-speaking guide" in names


def test_excluded_language_choice_extras_is_always_empty_now(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "German-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    assert result["excluded_language_choice_extras"] == []


def test_a_mix_of_flagged_and_unflagged_rows_all_publish(fake_api_client):
    data = minimal_ticket_data(start_date="2026-01-01", end_date="2026-12-31", modality_supplements=[
        {"name": "French-speaking guide", "adult_price_supplement": 15, "children_price_supplement": 15,
         "infant_price_supplement": 0, "is_priced_choice": True},
        {"name": "Holiday Season Surcharge", "adult_price_supplement": 22.5, "children_price_supplement": 22.5,
         "infant_price_supplement": 0, "start_date": "2026-12-24", "end_date": "2027-01-07",
         "is_priced_choice": False},
    ])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    names = {s["translations"]["EN"]["name"] for s in result["ticket_option_payload"]["supplements"]}
    assert names == {"French-speaking guide", "Holiday Season Surcharge"}


# ======================================================================
# UI wiring - the checkbox/column/warning are gone
# ======================================================================
def test_the_needs_own_modality_checkbox_column_is_gone_from_ui_components():
    # A retirement note in this function's own docstring is expected to name the old feature by
    # its label for context - what must actually be gone is the FUNCTIONAL wiring: the checkbox
    # column itself, and any read/write of is_priced_choice.
    import inspect
    import ui_components
    source = inspect.getsource(ui_components.render_ticket_modality_supplements_editor)
    assert "is_priced_choice" not in source
    assert "CheckboxColumn" not in source
    assert '"Needs own Modality?"' not in source  # would only appear as a column/key label


def test_app_py_no_longer_mentions_the_retired_flag():
    src = _read_app_py()
    assert "is_priced_choice" not in src
