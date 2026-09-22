"""Regression test for a real product-owner request (2026-09-22, verbatim):

    "the contracted before button on the outreach shall be on the second column as this is
    important to know."

The outreach review table's "Contacted before" marker (a computed "was this supplier already
emailed in an earlier session" flag - see outreach_tool.py's own "Contacted before" column
docstring/help text) used to sit second-to-last in the table, right before "Why selected". An
operator scanning left to right could easily tick "Send" on a repeat-contact row before ever
noticing the flag that far over.

Fixed by moving "Contacted before" to be the second column - immediately after "Send" - so it is
one of the very first things an operator sees for every row, not the second-to-last.

outreach_tool.py can be imported directly (it only touches Streamlit inside function bodies, not
at module import time), but the results table itself is built and rendered inside a function that
calls real st.* widgets, so - matching this suite's own established convention (see
test_2026_09_01_high_batch4_outreach.py's docstring) - this reads the module's own source text
for the column order rather than exercising the live Streamlit render.
"""
import inspect

import outreach_tool as ot


def _read_outreach_tool_py():
    return inspect.getsource(ot)


def test_contacted_before_is_the_second_column_in_the_review_table():
    source = _read_outreach_tool_py()
    marker = 'df = pd.DataFrame([{'
    assert marker in source
    idx = source.index(marker)
    window = source[idx:idx + 600]
    # Pull out the dict keys in the order they're assigned, in the same style the table's own
    # column_config block later declares them.
    import re
    keys = re.findall(r'"([^"]+)":\s', window)
    assert keys[0] == "Send", keys
    assert keys[1] == "Contacted before", (
        "Contacted before must be the second column per the 2026-09-22 product-owner request - "
        f"got column order {keys}"
    )


def test_contacted_before_marker_logic_itself_is_unchanged():
    # Not touching WHAT gets flagged, only WHERE the flag column sits in the table.
    source = _read_outreach_tool_py()
    assert '"Contacted before": "🔁" if s.get("alreadyContacted") else ""' in source
