"""
Searches Pexels for free, commercially-usable stock photos.
Requires PEXELS_API_KEY in .env / Streamlit secrets - get one free at
https://www.pexels.com/api/ (no cost, generous free tier).

Pexels photos are already hosted, so the returned URLs can go directly
into Travel Compositor's 'images' field - no re-uploading needed.
Pexels' license permits commercial use without requiring attribution,
though crediting the photographer is good practice.
"""
import requests
from dotenv import load_dotenv
from env_helpers import check_http_response, require_env_key

load_dotenv()

PEXELS_BASE_URL = "https://api.pexels.com/v1"


def search_images(query: str, per_page: int = 6) -> list:
    """
    Returns a list of dicts: {"url", "thumbnail", "photographer", "pexels_page"}
    Raises RuntimeError with a clear message if the API key is missing or the request fails.
    """
    api_key = require_env_key("PEXELS_API_KEY", "pexels.com/api")

    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": per_page}

    res = requests.get(f"{PEXELS_BASE_URL}/search", headers=headers, params=params, timeout=10)
    check_http_response(res, "Pexels search")

    data = res.json()
    results = []
    for photo in data.get("photos", []):
        results.append({
            "url": photo["src"]["large"],
            "thumbnail": photo["src"]["medium"],
            "photographer": photo.get("photographer", "Unknown"),
            "pexels_page": photo.get("url", "")
        })
    return results
