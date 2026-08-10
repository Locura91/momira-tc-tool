"""
outreach_memory.py — remembers which domains/names to block in supplier searches.

The blocklist is stored in the platform's durable key/value store (platform_store),
so it survives redeploys and is shared across all users.

Functions:
    get_blocklist() -> List[str]
    add_domain_to_blocklist(domain_or_url: str)
    remove_domain_from_blocklist(domain: str)
    clear_blocklist()
    is_blocked(domain_or_url: str) -> bool
    extract_domain(url: str) -> str
"""
import re
from typing import List
from urllib.parse import urlparse

import platform_store

_NAMESPACE = "outreach_blocklist"
_BLOCKLIST_KEY = "global_domains"


def extract_domain(url: str) -> str:
    """
    Returns the second-level domain (e.g. 'fyndtravel.com') from a URL.
    Handles URLs without scheme by adding a dummy one.
    """
    if not url:
        return ""
    # Ensure scheme for urlparse
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path  # fallback
    # Remove 'www.'
    host = re.sub(r'^www\.', '', host)
    # Keep only the first two levels (e.g. 'fyndtravel.com')
    parts = host.split('.')
    if len(parts) >= 2:
        return '.'.join(parts[-2:]).lower()
    return host.lower()


def get_blocklist() -> List[str]:
    """Returns the current list of blocked domains."""
    return platform_store.get(_NAMESPACE, _BLOCKLIST_KEY) or []


def _set_blocklist(domains: List[str]) -> None:
    """Replaces the entire blocklist (internal use)."""
    platform_store.set(_NAMESPACE, _BLOCKLIST_KEY, domains)


def add_domain_to_blocklist(domain_or_url: str) -> None:
    """
    Adds a domain (or a full URL) to the permanent blocklist.
    If the domain is already present, nothing changes.
    """
    domain = extract_domain(domain_or_url)
    if not domain:
        return
    current = get_blocklist()
    if domain not in current:
        current.append(domain)
        _set_blocklist(current)


def remove_domain_from_blocklist(domain: str) -> None:
    """Removes a domain from the blocklist."""
    current = get_blocklist()
    if domain in current:
        current.remove(domain)
        _set_blocklist(current)


def clear_blocklist() -> None:
    """Removes all blocked domains."""
    _set_blocklist([])


def is_blocked(domain_or_url: str) -> bool:
    """Returns True if the domain is blocked."""
    domain = extract_domain(domain_or_url)
    return domain in get_blocklist()