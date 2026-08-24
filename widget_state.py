"""Widget-key generations: the defence against a Streamlit widget showing a PREVIOUS item's value.

THE BUG CLASS (confirmed repeatedly - see the 2026-08-24 audit):
Streamlit ignores a widget's `value=`/`index=`/`default=` argument once st.session_state already
holds an entry for that widget's `key`. So a widget whose key is reused across two DIFFERENT items
(two tours, two tickets, two price-refresh runs) shows the FIRST item's value when the second is
rendered - and because these widgets assign straight back into the item's data dict
(`data["x"] = st.number_input(..., key="fixed_key")`), the stale value is silently written into
the new item and published.

WHY NOT SWEEP SESSION STATE (the older defence, still in app.py as _clear_batch_widget_state):
Sweeping means "delete every key under prefix P, except the control keys in `keep`". It works, but
it is the thing that keeps drifting - every new boundary must remember to sweep, with the right
prefix AND the right keep-list, and the audit found five boundaries where one or the other was
missed. The keep-list is the dangerous half: a flow whose prefix covers control state, saved
config AND widget keys (the `tk_` flow does) will have a review destroyed mid-edit by a sweep that
forgot one control key - a worse failure than the staleness being fixed.

WHAT THIS DOES INSTEAD:
Don't delete the old keys - stop reading them. Every flow has a "generation" token. Widget keys are
built through key_for(), which folds the generation in. Replacing the data behind a screen (a fresh
extraction, a re-extraction, prefilling from the live record, a new batch) calls bump(), and every
key changes at once. A widget rendered under a fresh generation has NO session_state entry, so
Streamlit honours its `value=` and reads the real data.

Nothing can be forgotten, because there is no list to maintain: a widget that gets its key from
key_for() is correct by construction. The abandoned keys linger unread (tens of bytes each, bounded
by how many extractions one session does) and die with the session.

The `state` parameter is st.session_state in the app and a plain dict in tests - session_state
behaves as a MutableMapping, which is all this needs.
"""

_SEQ_KEY = "_widget_token_seq"
_GEN_PREFIX = "_widget_gen_"
_DEFAULT_GENERATION = "g0"


def new_token(state):
    """A token no widget key in this session has used before.

    Monotonic rather than random so it is reproducible in tests and readable when debugging a
    live session's keys."""
    seq = state.get(_SEQ_KEY, 0) + 1
    state[_SEQ_KEY] = seq
    return f"g{seq}"


def bump(state, flow):
    """Start a new widget generation for `flow`.

    Call wherever a flow REPLACES the data behind its review screen. Not on ordinary reruns: the
    generation must stay stable while a human is editing, or their half-typed edits would vanish
    on every interaction."""
    state[_GEN_PREFIX + flow] = new_token(state)
    return state[_GEN_PREFIX + flow]


def generation(state, flow):
    """`flow`'s current generation. Stable until bump() - so edits made DURING a review survive
    reruns exactly as they did before generations existed."""
    return state.get(_GEN_PREFIX + flow, _DEFAULT_GENERATION)


def key_for(state, flow, name):
    """The generation-scoped widget key for `name` within `flow`.

    Use this for the widget's own `key=` AND for every other reference to that key by name - some
    AI-clarify handlers deliberately pop widget keys to force a re-read. Deriving both from one
    expression is the point: a generation-scoped widget whose matching pop still used the old bare
    literal would silently stop being cleared, which is precisely how this bug class returns."""
    return f"{flow}_{name}_{generation(state, flow)}"
