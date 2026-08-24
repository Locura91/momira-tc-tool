"""Unit tests for widget_state - the defence against a Streamlit widget showing a PREVIOUSLY
reviewed item's value (and silently writing it into the new item's data).

See widget_state.py's module docstring for the full bug class. These tests use a plain dict in
place of st.session_state, which is all the module needs.

Confirmed real instances this mechanism exists to close (2026-08-24 audit):
  - price refresh publishing a re-read route's OLD price to a live product
  - the legacy Ticket flow showing ticket #1's price/images/dates while reviewing ticket #2
  - the "I've checked this location on the map" tick surviving a change of coordinates
"""
import widget_state as ws


def test_tokens_are_never_reused():
    state = {}
    seen = {ws.new_token(state) for _ in range(100)}
    assert len(seen) == 100


def test_generation_defaults_before_any_bump():
    """A flow that has never bumped still needs a usable, stable key - the very first extraction
    of a session renders before anything has replaced data."""
    state = {}
    assert ws.generation(state, "tk") == ws.generation(state, "tk")
    assert ws.key_for(state, "tk", "price") == "tk_price_g0"


def test_generation_is_stable_between_bumps():
    """Crucial: the key must NOT change on ordinary reruns, or a human's half-typed edit would
    vanish every time they touched another widget."""
    state = {}
    ws.bump(state, "tk")
    first = ws.key_for(state, "tk", "price")
    for _ in range(10):
        assert ws.key_for(state, "tk", "price") == first


def test_bump_changes_every_key_for_that_flow():
    state = {}
    before = [ws.key_for(state, "tk", n) for n in ("price", "start_date", "images")]
    ws.bump(state, "tk")
    after = [ws.key_for(state, "tk", n) for n in ("price", "start_date", "images")]
    assert all(b != a for b, a in zip(before, after))


def test_bumping_one_flow_leaves_other_flows_alone():
    """Reviewing a ticket must not reset the half-finished ClosedTour on another tab."""
    state = {}
    ws.bump(state, "ct")
    ct_key = ws.key_for(state, "ct", "min_child_age")
    ws.bump(state, "tk")
    ws.bump(state, "tk")
    assert ws.key_for(state, "ct", "min_child_age") == ct_key


def test_a_key_from_a_previous_generation_is_never_produced_again():
    """The whole point: after a bump, the OLD key still sits in session_state holding the previous
    item's value, but nothing can ask for it again - so Streamlit honours value= and reads the
    freshly extracted data."""
    state = {}
    produced = set()
    for _ in range(50):
        produced.add(ws.key_for(state, "tk", "price"))
        ws.bump(state, "tk")
    assert len(produced) == 50


def test_stale_value_scenario_end_to_end():
    """The actual reported symptom, simulated: session_state holds ticket #1's price under its
    key; after a fresh extraction, the widget asks for a key that isn't there, so the real
    (extracted) value is what renders."""
    session = {}
    key1 = ws.key_for(session, "tk", "service_price")
    session[key1] = 250.0                      # what the human saw/typed on ticket #1

    ws.bump(session, "tk")                     # ticket #2 extracted - data says 90.0
    key2 = ws.key_for(session, "tk", "service_price")

    assert key2 != key1
    assert key2 not in session                 # nothing stale to fall back on
    assert session[key1] == 250.0              # the old entry survives, simply unread


def test_different_names_within_a_flow_never_collide():
    state = {}
    ws.bump(state, "tk")
    assert ws.key_for(state, "tk", "price") != ws.key_for(state, "tk", "start_date")


def test_bump_returns_the_new_generation():
    state = {}
    assert ws.bump(state, "tk") == ws.generation(state, "tk")
