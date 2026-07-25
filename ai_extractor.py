"""
Uses the Claude API to turn raw, messy DMC document text (any language)
into structured English tour data, matching the shape builder.py expects.

Requires ANTHROPIC_API_KEY in .env (get one at console.anthropic.com).
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

EXTRACTION_SYSTEM_PROMPT = """You are extracting structured travel product data from a DMC (Destination Management Company) supplier document for Momira Travel.

Rules:
- Translate ALL content into English, regardless of the source document's original language.
- Output ONLY valid JSON. No markdown code fences, no explanation, no preamble.
- Never fabricate information that isn't present in the source document. Use empty string "" or empty list [] for anything you can't determine.
- itinerary_destinations must be a list of plain place names in the order they're visited (e.g. "Aswan", "Luxor", "Kom Ombo") - NOT codes. Codes get resolved separately against the live destination database.
- price_list: only populate this if the document contains an actual pricing table (dates + per-occupancy prices). If pricing is vague, marketing-only, or absent, return an empty list - do not guess numbers.

Output this exact JSON structure:
{
  "tour_name": "",
  "description": "",
  "hotels_text": "",
  "included": "",
  "excluded": "",
  "meeting_point": "",
  "policy_remarks": "",
  "itinerary_destinations": [],
  "nights": 0,
  "operational_days": [1, 2, 3, 4, 5, 6, 7],
  "price_list": []
}"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def extract_structured_data(raw_text: str, model: str = "claude-sonnet-5") -> dict:
    """
    Sends raw document text to Claude and returns structured, English,
    JSON-parsed tour data. Raises RuntimeError with a clear message if the
    API call fails or the response isn't valid JSON (rather than silently
    returning garbage).
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package isn't installed. Run: pip install anthropic --break-system-packages"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in .env. Get one at console.anthropic.com "
            "(Settings -> API Keys -> Create Key) and add it to your .env file."
        )

    client = Anthropic(api_key=api_key)

    print(f"🤖 Sending document to Claude ({model}) for extraction...")
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text}],
    )

    raw_response = "".join(block.text for block in response.content if block.type == "text")
    cleaned = _strip_code_fences(raw_response)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Claude's response wasn't valid JSON ({e}). Raw response:\n{raw_response[:1000]}"
        )

    # Defensive defaults in case the model omits a key
    defaults = {
        "tour_name": "", "description": "", "hotels_text": "", "included": "",
        "excluded": "", "meeting_point": "", "policy_remarks": "",
        "itinerary_destinations": [], "nights": 0,
        "operational_days": [1, 2, 3, 4, 5, 6, 7], "price_list": []
    }
    defaults.update(data)

    print(f"✅ Extraction complete: '{defaults['tour_name']}' "
          f"({len(defaults['itinerary_destinations'])} destinations, {defaults['nights']} nights)")
    return defaults