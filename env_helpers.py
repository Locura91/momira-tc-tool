"""
Shared helpers for environment-variable validation and HTTP response checking.
Used by pexels_client, pixabay_client, and freeimage_client to avoid
repeating the same guard and error-formatting code in every API client.
"""
import os
import requests

MODULE_BUILD = "2026-10-20-fix-ticket-opdates"


def require_env_key(var_name: str, signup_url: str, key_url: str | None = None) -> str:
    """
    Returns the value of *var_name* from the environment, or raises RuntimeError
    with a clear setup message if it is missing or empty.

    signup_url: where to create an account / get a key
    key_url:    where to find the key once logged in (omit if same as signup_url)
    """
    value = os.getenv(var_name, "")
    if not value:
        parts = [
            f"{var_name} is not set. Get a free key at {signup_url}",
        ]
        if key_url:
            parts.append(f"(your key is shown at {key_url} while logged in)")
        parts.append("and add it to your .env (locally) or Streamlit Secrets (when deployed).")
        raise RuntimeError(" ".join(parts))
    return value


def check_http_response(res: requests.Response, service_name: str) -> None:
    """Raises RuntimeError with the status code and first 200 chars of body if not 200."""
    if res.status_code != 200:
        raise RuntimeError(
            f"{service_name} request failed ({res.status_code}): {res.text[:200]}"
        )
