"""
outreach_memory.py — remembers which domains to skip in supplier searches.

The blocklist lives in the platform's durable key/value store (platform_store), so it
survives redeploys and is shared by everyone using the tool. outreach_discovery.py reads
it once per search and drops any candidate whose domain is on it.

SINGLE OWNER, DELIBERATELY. This module is the only place the blocklist's namespace, key
and domain-extraction rule are defined. They were briefly defined twice - here and again
inside outreach_discovery.py - with two different extraction rules, which meant the domain
the UI showed you and the domain the search actually filtered on could disagree. Anything
that needs the blocklist imports it from here.

Functions:
    extract_domain(url) -> str
    get_blocklist() -> List[str]
    add_domain_to_blocklist(domain_or_url) -> bool
    remove_domain_from_blocklist(domain) -> bool
    clear_blocklist() -> None
    is_blocked(domain_or_url) -> bool
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-02-hotel-extraction-rules-1-9"

import re
from typing import List

import platform_store

_NAMESPACE = "outreach_blocklist"
_BLOCKLIST_KEY = "global_domains"

# Suffixes that are themselves public registries, so the registrable name is one label
# FURTHER LEFT than usual. Without this, "example.co.uk" reduces to "co.uk" - and blocking
# one British listings site would silently blacklist EVERY .co.uk supplier from every future
# search, for everyone, with nothing on screen to explain where they went. The same applies
# to .com.eg, .com.sa and .com.jo, which is most of the region Momira actually sources from.
#
# This is a curated list rather than the full public suffix list: that would mean a new
# dependency fetching a file over the network at import time, inside an app that already has
# to work when the network is unhelpful. The entries below cover the country forms a travel
# supplier realistically uses; an unlisted one degrades to the old two-label behaviour for
# that country only, which is visible and fixable rather than silent.
_TWO_LABEL_SUFFIXES = {
    # Europe
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "net.uk", "ltd.uk", "plc.uk",
    "com.tr", "com.ua", "com.gr", "com.cy", "com.mt", "com.pl", "com.ro", "com.hr",
    "com.es", "com.pt", "com.de", "com.fr", "com.it", "com.ru", "com.ge",
    # Middle East & North Africa - the core sourcing region
    "com.eg", "com.sa", "com.jo", "com.lb", "com.kw", "com.qa", "com.bh", "com.om",
    "com.ae", "com.ye", "com.ly", "com.tn", "com.ma", "com.dz", "org.eg", "net.eg",
    # Africa
    "co.za", "org.za", "net.za", "co.ke", "co.tz", "co.ug", "com.ng", "com.gh",
    # Asia-Pacific
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "net.nz", "org.nz",
    "co.jp", "ne.jp", "or.jp", "co.kr", "co.in", "net.in", "org.in", "co.th",
    "com.cn", "com.hk", "com.sg", "com.my", "com.ph", "com.vn", "com.tw", "com.pk",
    "com.bd", "co.id", "com.np", "com.lk", "com.kh", "com.mm",
    # Americas
    "com.br", "com.ar", "com.mx", "com.co", "com.pe", "com.ec", "com.uy", "com.py",
    "com.bo", "com.ve", "com.do", "com.gt", "com.pa", "com.cr", "com.ni", "com.sv",
    # Other
    "co.il", "com.ru",
}

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def extract_domain(url: str) -> str:
    """The registrable domain for a URL or bare hostname, e.g. 'fyndtravel.com'.

    Written by hand rather than with urlparse because the inputs are not reliably URLs -
    a supplier's "website" field routinely holds a bare 'example.com', which urlparse puts
    in .path and every naive reading then treats as empty. An empty domain silently means
    "never blocked", so that failure mode is invisible from the screen.

    Returns "" only when there is genuinely nothing to work with."""
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)   # scheme, if any
    text = text.split("/")[0].split("?")[0].split("#")[0]     # path/query/fragment
    text = text.split("@")[-1]                                # credentials
    text = text.split(":")[0]                                 # port
    text = text.strip().strip(".").lower()
    if not text:
        return ""
    text = re.sub(r"^www\d*\.", "", text)                     # ANCHORED - 'wwwhotels.com' is a real name
    if _IPV4_RE.match(text) or "." not in text:
        return text                                           # an IP or a bare host stands alone
    parts = [p for p in text.split(".") if p]
    if len(parts) < 2:
        return text
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def get_blocklist() -> List[str]:
    """The blocked domains, oldest first. Never raises - a store that is down means an
    unfiltered search, which is visibly wrong, rather than a crashed one."""
    try:
        stored = platform_store.get(_NAMESPACE, _BLOCKLIST_KEY)
    except Exception:
        return []
    return [d for d in (stored or []) if isinstance(d, str) and d]


def _set_blocklist(domains: List[str]) -> bool:
    return platform_store.set(_NAMESPACE, _BLOCKLIST_KEY, domains)


def add_domain_to_blocklist(domain_or_url: str) -> bool:
    """Adds one domain. Returns True only if the list actually changed AND the write
    succeeded - the caller needs to be able to say "blocked" honestly."""
    domain = extract_domain(domain_or_url)
    if not domain:
        return False
    current = get_blocklist()
    if domain in current:
        return False
    return bool(_set_blocklist(current + [domain]))


def remove_domain_from_blocklist(domain: str) -> bool:
    """Removes a domain. Matches what get_blocklist() returned, and also tolerates being
    handed a full URL for the same entry."""
    current = get_blocklist()
    target = domain if domain in current else extract_domain(domain)
    if target not in current:
        return False
    return bool(_set_blocklist([d for d in current if d != target]))


def clear_blocklist() -> None:
    _set_blocklist([])


def is_blocked(domain_or_url: str) -> bool:
    domain = extract_domain(domain_or_url)
    return bool(domain) and domain in get_blocklist()
