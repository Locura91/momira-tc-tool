"""
Uploads image bytes to freeimage.host, returning a real public URL.
Used for images extracted from documents (PDF/Word/Excel), which only
exist as raw bytes until hosted somewhere Travel Compositor can reference.

Confirmed via official current API docs (freeimage.host/api) - actively
maintained service (45M+ images hosted as of last check), free account
required for an API key (unlike ImgBB, the API itself isn't paywalled).

Requires FREEIMAGE_API_KEY in .env / Streamlit secrets - create a free
account at https://freeimage.host/signup, then find your API key at
https://freeimage.host/page/api while logged in.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

FREEIMAGE_UPLOAD_URL = "https://freeimage.host/api/1/upload"


def upload_image(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """Uploads raw image bytes to freeimage.host, returns the hosted display URL."""
    api_key = os.getenv("FREEIMAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "FREEIMAGE_API_KEY is not set. Create a free account at "
            "https://freeimage.host/signup, then find your API key at "
            "https://freeimage.host/page/api (while logged in) and add it "
            "to your .env (locally) or Streamlit Secrets (when deployed)."
        )
    files = {"source": (filename, image_bytes)}
    data = {"key": api_key, "action": "upload", "format": "json"}
    res = requests.post(FREEIMAGE_UPLOAD_URL, data=data, files=files, timeout=30)

    if res.status_code != 200:
        raise RuntimeError(f"freeimage.host upload failed ({res.status_code}): {res.text[:300]}")
    result = res.json()
    if result.get("status_code") != 200:
        raise RuntimeError(f"freeimage.host upload failed: {result}")
    # Prefer 'url' (original size) over 'display_url' (may be a resized medium version)
    return result["image"].get("url") or result["image"].get("display_url")


def upload_images(image_list: list) -> list:
    """
    image_list: list of (image_bytes, extension) tuples.
    Returns list of successfully uploaded URLs - skips (doesn't crash on)
    any individual image that fails to upload, since one bad image
    shouldn't block the whole extraction.

    CONFIRMED REAL GAP (product owner, "I can't integrate the images from the document, I get an
    error" - the error was invisible): every per-image failure here was swallowed down to a bare
    print(), which goes nowhere useful on Streamlit Cloud (not shown to the user, easy to miss
    even in logs). A human clicking "Upload & Add" on a perfectly good photo saw only "Upload
    returned no URL." with zero indication of WHY - missing/invalid FREEIMAGE_API_KEY, the
    service being down, a rate limit, etc. are all indistinguishable from each other and from a
    genuinely corrupt image. This function's own behavior is UNCHANGED (still silently skips, for
    every existing bulk-upload call site that shouldn't halt on one bad image) - see
    upload_images_with_errors() below for the same upload with the reasons preserved, used by the
    single-image "Upload & Add" button where a human is watching and can act on the real reason.
    """
    urls, _ = upload_images_with_errors(image_list)
    return urls


def upload_images_with_errors(image_list: list) -> tuple:
    """
    Same upload as upload_images(), but returns (urls, errors) instead of discarding the reason
    for each failure - errors is a list of "filename: message" strings, one per failed image, in
    the order they were attempted. Use this wherever a human is watching the result and needs to
    know WHY an upload didn't produce a URL, rather than just that it didn't.
    """
    urls, errors = [], []
    for img_bytes, ext in image_list:
        filename = f"image.{ext or 'jpg'}"
        try:
            urls.append(upload_image(img_bytes, filename=filename))
        except Exception as e:
            errors.append(f"{filename}: {e}")
    return urls, errors
