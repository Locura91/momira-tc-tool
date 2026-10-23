"""
rollover_executor.py — Package rollover URL builder.

The TC API has no endpoint to change a package's departure date, and
server-side form POST to momira.travel is blocked (Cloudflare + requires
user session). The correct flow: open the package page in the user's
browser (they are already logged in) and let them click the one button.

This module builds the URL to open and is kept as a module so callers
don't need to know about slug construction.
"""
MODULE_BUILD = "2026-10-23-rollover-open-in-browser"


def build_rollover_url(idea_url: str) -> str:
    """Return the momira.travel URL to open in a new tab for this package."""
    return idea_url
