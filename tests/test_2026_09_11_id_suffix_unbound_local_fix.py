"""Regression test for a real production crash on the bulk Transport price-refresh review screen
(reported directly by the product owner, with the live Streamlit traceback):

    UnboundLocalError ... app.py, line 13278, in render_price_refresh_flow
        st.markdown(f"- **{route.get('name') or route.get('id')}**{_id_suffix(route)} · "

CONFIRMED REAL MECHANISM: `_id_suffix` is a `def` nested inside `render_price_refresh_flow`.
Because Python decides a name is local to a function by scanning the WHOLE function body for any
assignment (a `def` counts as one), `_id_suffix` was local to `render_price_refresh_flow` from the
top of the function - even though the `def _id_suffix(...)` line itself appeared much later, right
before the "changed" routes loop. The new flat-price-modality warning block (2026-09-11) called
`_id_suffix(route)` earlier in the same function, before that `def` line had executed, which raised
UnboundLocalError instead of falling back to the outer/global scope. This is a real Python gotcha,
not a typo - the fix is to move the `def _id_suffix` above every call site within the function.

This test reads app.py's own source and asserts the `def _id_suffix` line appears before every
`_id_suffix(` call site inside render_price_refresh_flow, so a future edit that reintroduces a call
above the definition (or moves the definition back down) fails loudly here instead of only in
production.
"""
import re


def _read_render_price_refresh_flow_source():
    with open("app.py", encoding="utf-8") as f:
        lines = f.readlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("def render_price_refresh_flow("))
    # Function ends at the next top-level ("def " with no leading whitespace) after start.
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("def "))
    return lines[start:end], start


def test_id_suffix_is_defined_before_every_call_site_within_the_function():
    body, _ = _read_render_price_refresh_flow_source()
    def_line = next((i for i, line in enumerate(body) if re.match(r"\s*def _id_suffix\(", line)), None)
    assert def_line is not None, "expected a nested def _id_suffix(...) inside render_price_refresh_flow"

    call_lines = [i for i, line in enumerate(body)
                  if "_id_suffix(" in line and not re.match(r"\s*def _id_suffix\(", line)]
    assert call_lines, "expected at least one call site to _id_suffix in render_price_refresh_flow"

    offending = [i for i in call_lines if i < def_line]
    assert not offending, (
        f"_id_suffix is called on line offset(s) {offending} before its own def at offset {def_line} "
        "within render_price_refresh_flow - this is the exact UnboundLocalError reported in "
        "production on 2026-09-11 (real traceback pointed at the flat-price-modality warning block)."
    )


def test_the_flat_price_warning_block_is_one_of_the_call_sites_covered():
    # Guards against silently deleting the flat-price block's own call to _id_suffix and having
    # this test pass vacuously because no call site exists above the def anymore.
    body, _ = _read_render_price_refresh_flow_source()
    joined = "".join(body)
    assert "show identical prices across modalities" in joined
    # The flat-price block's per-route line must itself use _id_suffix(route).
    flat_block_start = joined.index("show identical prices across modalities")
    flat_block = joined[flat_block_start:flat_block_start + 2000]
    assert "_id_suffix(route)" in flat_block
