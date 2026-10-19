"""
Searches Pixabay for free, commercially-usable stock photos.
Requires PIXABAY_API_KEY in .env / Streamlit secrets - get one free at
https://pixabay.com/api/docs/ (create a free account, key is shown right
on that page while logged in).

Confirmed via official current docs: unlimited free requests (rate-limited
to ~100/60sec), CC0-licensed content - no attribution legally required
(though crediting is appreciated). Simple key-based auth, no OAuth.

Pixabay photos are already hosted, so the returned URLs can go directly
into Travel Compositor's 'images' field - no re-uploading needed.
"""
import requests
from dotenv import load_dotenv
from env_helpers import check_http_response, require_env_key

load_dotenv()

PIXABAY_BASE_URL = "https://pixabay.com/api/"


def search_images(query: str, per_page: int = 6) -> list:
    """
    Returns a list of dicts: {"url", "thumbnail", "photographer", "pixabay_page"}
    Matches pexels_client.search_images()'s shape exactly, so it's a drop-in
    second source in the UI.
    Raises RuntimeError with a clear message if the API key is missing or the request fails.
    """
    api_key = require_env_key(
        "PIXABAY_API_KEY",
        "https://pixabay.com/api/docs/",
        key_url="https://pixabay.com/api/docs/",
    )

    # Pixabay requires per_page >= 3
    params = {"key": api_key, "q": query, "image_type": "photo", "per_page": max(per_page, 3)}

    res = requests.get(PIXABAY_BASE_URL, params=params, timeout=10)
    check_http_response(res, "Pixabay search")

    data = res.json()
    results = []
    for photo in data.get("hits", [])[:per_page]:
        results.append({
            "url": photo.get("largeImageURL") or photo.get("webformatURL"),
            "thumbnail": photo.get("previewURL") or photo.get("webformatURL"),
            "photographer": photo.get("user", "Unknown"),
            "pixabay_page": photo.get("pageURL", "")
        })
    return results
