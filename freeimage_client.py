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
    """
    urls = []
    for img_bytes, ext in image_list:
        try:
            urls.append(upload_image(img_bytes, filename=f"image.{ext or 'jpg'}"))
        except Exception as e:
            print(f"⚠️ Skipped one image upload: {e}")
    return urls
