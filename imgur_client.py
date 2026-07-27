"""
Uploads image bytes to Imgur anonymously, returning a real public URL.
Used for images extracted from documents (PDF/Word/Excel), which only
exist as raw bytes until hosted somewhere Travel Compositor can reference.

Requires IMGUR_CLIENT_ID in .env / Streamlit secrets - register a free app
at https://api.imgur.com/oauth2/addclient (choose "Anonymous usage without
user authorization"), no cost, no OAuth login needed for uploads.
"""
import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

IMGUR_UPLOAD_URL = "https://api.imgur.com/3/image"


def upload_image(image_bytes: bytes) -> str:
    """Uploads raw image bytes to Imgur anonymously, returns the hosted URL."""
    client_id = os.getenv("IMGUR_CLIENT_ID", "")
    if not client_id:
        raise RuntimeError(
            "IMGUR_CLIENT_ID is not set. Register a free app at "
            "https://api.imgur.com/oauth2/addclient (select 'Anonymous usage without "
            "user authorization') and add the Client ID to your .env (locally) or "
            "Streamlit Secrets (when deployed)."
        )
    headers = {"Authorization": f"Client-ID {client_id}"}
    encoded = base64.b64encode(image_bytes)
    res = requests.post(IMGUR_UPLOAD_URL, headers=headers, data={"image": encoded, "type": "base64"}, timeout=20)
    if res.status_code != 200:
        raise RuntimeError(f"Imgur upload failed ({res.status_code}): {res.text[:200]}")
    data = res.json()
    return data["data"]["link"]


def upload_images(image_list: list) -> list:
    """
    image_list: list of (image_bytes, extension) tuples.
    Returns list of successfully uploaded URLs - skips (doesn't crash on)
    any individual image that fails to upload, since one bad image
    shouldn't block the whole extraction.
    """
    urls = []
    for img_bytes, _ext in image_list:
        try:
            urls.append(upload_image(img_bytes))
        except Exception as e:
            print(f"⚠️ Skipped one image upload: {e}")
    return urls
