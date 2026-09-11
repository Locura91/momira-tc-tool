"""
Checks the real pixel dimensions of a hosted image URL before it's submitted to Travel
Compositor, which enforces a hard minimum of 500x400 on every image and rejects the ENTIRE
publish outright if even one image in the list is smaller - "Minimum size of 500x400 required,
WxH found" (confirmed real production failure, product owner, 2026-09-06: HRG-T1's Pexels pick
came back 433x650 - narrower than the 940px width the URL asked for, because Pexels fits a
resize request to whichever dimension the source photo's aspect ratio actually constrains, so a
portrait-oriented photo can come back much narrower than requested).

Per the product-owner instruction ("if an image is not big enough, take the default image ...
and skip the error - same as tickets and closedtours"), every product's builder now filters its
final image list down to ones that measure at least MIN_WIDTH x MIN_HEIGHT, falling back to the
shared FALLBACK_IMAGE placeholder (the same one already used app-wide whenever no real image was
picked at all) if filtering leaves nothing - never blocking or erroring at publish time over a
too-small image.

Every dimension lookup is cached by URL for the life of the process: build_*_payload functions
re-run on every Streamlit interaction (any widget edit reruns the whole review screen), so an
unbounded per-render network fetch for every image in the list would make the screen
progressively slower with each keystroke.
"""
import io
from typing import List, Optional, Tuple

import requests
from PIL import Image, UnidentifiedImageError

MODULE_BUILD = "2026-09-11-fts-transport-force-base-occupancy-and-matrix-parser"

MIN_WIDTH = 500
MIN_HEIGHT = 400
_TIMEOUT = 10

# Single source of truth for the app-wide "no real image" placeholder - app.py imports this
# rather than defining its own copy, so the two can never drift apart.
FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"

_dimension_cache = {}  # url -> (width, height) or None (fetch/parse failed)


def get_image_dimensions(url: str) -> Optional[Tuple[int, int]]:
    """Returns (width, height) for a hosted image URL, or None if it couldn't be fetched or
    parsed as an image (a network hiccup, a dead link, or an unrecognised format). A None result
    is treated as "unknown", not "too small" - see image_meets_minimum_size - so a transient
    failure here never silently drops an otherwise-fine image; Travel Compositor's own check at
    publish time is still the final word for anything this couldn't verify in advance."""
    if not url:
        return None
    if url in _dimension_cache:
        return _dimension_cache[url]
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        dims = img.size  # (width, height) - PIL reads this from the header, no full decode needed
    except (requests.RequestException, UnidentifiedImageError, OSError, ValueError):
        dims = None
    _dimension_cache[url] = dims
    return dims


def image_meets_minimum_size(url: str, min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT) -> bool:
    """True unless the image is CONFIRMED smaller than min_width x min_height. Fails open on an
    unknown size (see get_image_dimensions) - only a dimension pair actually measured and found
    too small is ever treated as a rejection here."""
    dims = get_image_dimensions(url)
    if dims is None:
        return True
    width, height = dims
    return width >= min_width and height >= min_height


def filter_images_by_minimum_size(
    urls: List[str], min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT
) -> Tuple[List[str], List[str]]:
    """Splits urls into (kept, dropped), preserving order within each. `dropped` holds only URLs
    CONFIRMED smaller than the minimum - never one whose size simply couldn't be determined."""
    kept, dropped = [], []
    for u in urls or []:
        if not u:
            continue
        if image_meets_minimum_size(u, min_width, min_height):
            kept.append(u)
        else:
            dropped.append(u)
    return kept, dropped


def ensure_images_meet_minimum_size(
    urls: List[str], min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT
) -> Tuple[List[str], List[str]]:
    """Convenience wrapper for every build_*_payload call site: filters `urls` down to ones that
    meet the minimum size, and falls back to [FALLBACK_IMAGE] if that leaves nothing at all - the
    same placeholder already used app-wide whenever no real image was ever picked, so a hotel/
    ticket/tour with only undersized photos publishes with a generic placeholder instead of
    failing outright. Returns (final_images, dropped_urls)."""
    kept, dropped = filter_images_by_minimum_size(urls, min_width, min_height)
    return (kept or [FALLBACK_IMAGE]), dropped
