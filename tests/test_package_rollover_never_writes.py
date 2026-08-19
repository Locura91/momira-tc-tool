"""Guardrail test: the Package Rollover prototype must NEVER call anything that writes to
Travel Compositor.

CONFIRMED PRODUCT-OWNER BOUNDARY (2026-08-19): "I need to make sure that nothing will be
booked and only accept the new departure time after the human review the package... The best
case is, that the new holiday package departure price is seen in the summary of Travel
Compositor, so the human can then click automatically on save - as the human would do it
regullarly within Travel Compositor." I.e. this tool only ever proposes a change; applying it
is a manual step the human does themselves inside Travel Compositor's own edit screen. This
tool's own code must never call update_holiday_package (the one write method the Packages API
exposes - see travelcompositor_api.py, already used elsewhere for the legitimate Holiday
Package translation sync) or any raw PUT/POST call.

Same enforcement pattern as tests/test_trip_idea_never_books.py: scans SOURCE TEXT so this
survives into whenever more package_rollover_*.py files get added, rather than relying on a
comment or a human's memory. Deliberately scoped to package_rollover_*.py only, NOT the whole
of travelcompositor_api.py - that file correctly DOES call update_holiday_package for the
translation sync, and this guardrail is about the rollover feature never doing so, not about
banning writes from the codebase entirely.
"""
import glob
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROLLOVER_MODULE_PATHS = sorted(glob.glob(os.path.join(_REPO_ROOT, "package_rollover_*.py")))

_FORBIDDEN_PATTERNS = [
    # The one real write method the Packages API exposes - see travelcompositor_api.py.
    re.compile(r"update_holiday_package\s*\(", re.IGNORECASE),
    # A direct PUT/POST call, whether through TravelCompositorAPI._request or raw requests -
    # quoted so prose like "no PUT call anywhere" in a docstring/comment doesn't trip this.
    re.compile(r'["\']PUT["\']'),
    re.compile(r'["\']POST["\']'),
    re.compile(r"requests\.(put|post)\s*\(", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z_])\.put\s*\(", re.IGNORECASE),
]


def test_at_least_one_package_rollover_module_exists_so_this_guardrail_isnt_silently_vacuous():
    assert _ROLLOVER_MODULE_PATHS, "expected package_rollover_*.py files in the repo root"


@pytest.mark.parametrize("path", _ROLLOVER_MODULE_PATHS, ids=[os.path.basename(p) for p in _ROLLOVER_MODULE_PATHS])
def test_package_rollover_module_never_references_a_write_call(path):
    with open(path, encoding="utf-8") as f:
        source = f.read()

    hits = []
    for pattern in _FORBIDDEN_PATTERNS:
        for m in pattern.finditer(source):
            line_no = source[:m.start()].count("\n") + 1
            hits.append(f"line {line_no}: {m.group(0)!r}")

    assert not hits, (
        f"{os.path.basename(path)} appears to reference a write/PUT/POST call, which the "
        f"product owner explicitly ruled out (2026-08-19): the human must apply any proposed "
        f"change themselves inside Travel Compositor's own edit screen, never automatically "
        f"from this tool. Found: {hits}. If this is genuinely wanted now, that's a product "
        f"decision to confirm explicitly, not a test to quietly adjust."
    )
