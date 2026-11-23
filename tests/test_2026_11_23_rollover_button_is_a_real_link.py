"""The rollover button must be a real link, never an iframe + window.open().

CONFIRMED REAL SYMPTOM (operator, 2026-11-23): "I click start rollover in Travel
Compositor and nothing happened... this worked already this afternoon."

Root cause: the button was a hand-built <button> inside a streamlit components.html
IFRAME whose click handler called window.open(). Chrome treats window.open() from a
sandboxed component iframe as a popup. When it is blocked the call simply returns null -
the click fires and nothing visible happens at all. No new tab, no error, nothing the
operator can react to. Whether it is blocked depends on the browser's popup settings and
on the sandbox flags Streamlit puts on the component iframe, which is exactly why it
could work one afternoon and silently stop working the next.

st.link_button renders a real <a href target="_blank"> in the MAIN document. A
user-initiated anchor navigation is not a popup, so neither the popup blocker nor the
iframe sandbox is involved.
"""
import inspect

import package_rollover_tool


SOURCE = inspect.getsource(package_rollover_tool._render_rollover_body)


def test_the_rollover_body_never_calls_window_open():
    code = "\n".join(
        line for line in SOURCE.splitlines() if not line.strip().startswith("#")
    )
    assert "window.open" not in code, (
        "window.open() from a components.html iframe is popup-blocked by Chrome and fails "
        "SILENTLY - the operator clicks and nothing happens. Use st.link_button, which "
        "renders a real anchor in the main document."
    )


def test_the_rollover_body_renders_no_component_iframe():
    code = "\n".join(
        line for line in SOURCE.splitlines() if not line.strip().startswith("#")
    )
    assert "components.html" not in code, (
        "A component iframe reintroduces the sandbox/popup-blocker failure mode."
    )


def test_the_rollover_body_uses_link_button():
    assert "st.link_button" in SOURCE


def test_the_url_is_never_pasted_into_a_javascript_literal():
    """An href attribute cannot be broken by a stray quote in a package title the way a
    JavaScript string literal can - a broken literal would throw and never attach the
    click handler, giving the same silent no-op."""
    code = "\n".join(
        line for line in SOURCE.splitlines() if not line.strip().startswith("#")
    )
    assert "<script>" not in code
    assert "addEventListener" not in code
