"""
Uploads image bytes to Catbox.moe, returning a real public URL.
Used for images extracted from documents (PDF/Word/Excel), which only
exist as raw bytes until hosted somewhere Travel Compositor can reference.

Replaces imgur_client.py (Imgur's registration page has been broken since
Aug 2025) and imgbb_client.py (ImgBB's API now requires a paid plan).

Catbox.moe requires NO signup and NO API key at all for anonymous uploads -
it's a donation-supported free service (donate at patreon.com/catbox if you
find it useful). Files are hosted indefinitely, up to 200MB each.
"""
import requests

CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"


def upload_image(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """Uploads raw image bytes to Catbox.moe anonymously, returns the hosted URL."""
    files = {"fileToUpload": (filename, image_bytes)}
    data = {"reqtype": "fileupload"}
    res = requests.post(CATBOX_UPLOAD_URL, data=data, files=files, timeout=30)
    if res.status_code != 200:
        raise RuntimeError(f"Catbox upload failed ({res.status_code}): {res.text[:200]}")
    url = res.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Catbox upload failed: {url}")
    return url


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
