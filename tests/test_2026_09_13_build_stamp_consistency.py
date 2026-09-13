"""Guards the invariant behind app.py's own "Partial deploy" warning banner: every module that
carries a MODULE_BUILD stamp carries THE SAME stamp as app.py's BUILD_VERSION.

WHY THIS EXISTS (2026-09-13): app.py's _module_build_mismatches() already detects a partial
deploy at runtime - but only at runtime, in front of whoever opens the app, as a warning banner
listing every file left behind. Today that banner sat on the live app for hours after a
four-round manual sync, because nothing in the test suite had any opinion about stamp
consistency: the suite went green with ~30 modules still on the previous build string.

This test moves that check earlier - a restamp that misses files now fails here, before the
commit, instead of showing up as a banner on the deployed app. It deliberately mirrors app.py's
own discovery rules (skip app.py itself and test_* modules; a module with NO stamp is skipped
rather than failed) so the two can't disagree about what counts as stale.

Deliberately does NOT require every module to HAVE a stamp: several modules (sync_*.py,
translator.py, widget_state.py and others) have never carried one, and app.py's own check
silently skips those. Making that an error here would be inventing a rule the app itself does
not enforce, and would fail this suite on files nobody touched.
"""
import os
import re

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PY = os.path.join(_REPO_DIR, "app.py")

_STAMP_RE = re.compile(r'^MODULE_BUILD = "([^"]+)"', flags=re.M)
_APP_VERSION_RE = re.compile(r'^BUILD_VERSION = "([^"]+)"', flags=re.M)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _app_build_version() -> str:
    match = _APP_VERSION_RE.search(_read(_APP_PY))
    assert match, "app.py no longer declares a BUILD_VERSION - the stale-module check depends on it"
    return match.group(1)


def _stamped_modules() -> dict:
    """Every root-level module carrying a MODULE_BUILD, read from source text rather than by
    importing - importing every module in the repo would drag in network clients and Streamlit
    session state, and the stamp is a plain literal that never depends on runtime state."""
    out = {}
    for name in sorted(os.listdir(_REPO_DIR)):
        if not name.endswith(".py") or name == "app.py" or name.startswith("test_"):
            continue
        match = _STAMP_RE.search(_read(os.path.join(_REPO_DIR, name)))
        if match:
            out[name] = match.group(1)
    return out


def test_app_declares_a_build_version():
    assert _app_build_version()


def test_at_least_most_modules_carry_a_stamp():
    """Sanity floor - if this drops off a cliff, the discovery above has silently stopped
    finding stamps and the real assertion below would pass vacuously."""
    assert len(_stamped_modules()) >= 30


def test_every_stamped_module_matches_the_app_build_version():
    """THE ACTUAL GUARD: a restamp that misses files fails here rather than reaching the
    deployed app as a 'Partial deploy' banner."""
    expected = _app_build_version()
    stale = {name: stamp for name, stamp in _stamped_modules().items() if stamp != expected}
    assert not stale, (
        f"{len(stale)} module(s) still carry an old build stamp while app.py is on "
        f"'{expected}'. app.py will show its 'Partial deploy' banner for these on the live "
        f"app until they are restamped: " + ", ".join(f"{n} ({s})" for n, s in sorted(stale.items()))
    )
