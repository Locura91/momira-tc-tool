"""
trip_prompt_extractor.py — PROTOTYPE: turns a customer's free-text travel idea into
structured search criteria.

CONTEXT (2026-08-19): Chris's long-term idea is a client-facing widget where a customer types
their travel idea as a sentence ("2 adults, travelling in February, city and beach in Spain")
instead of filling in separate destination/date/pax/theme fields, and the platform turns that
into a real search against Travel Compositor returning an actual package (hotels, transfers,
flights if available).

THIS FILE IS ONLY THE FIRST HALF OF THAT IDEA: prompt -> structured criteria. It does NOT talk
to Travel Compositor at all - that half is intentionally still blocked on two open questions
Chris is checking with his TC account manager: (1) whether the Multidestination Booking Engine /
search API is enabled on the account at all (this codebase has only ever used TC's contract-
management API, a different module), and (2) how flights would be sourced (TC's own inventory,
if the module includes it, vs. a separate flight API). Building the extraction layer first is
useful on its own and independent of those answers - it's exactly the same
prompt-in/structured-JSON-out pattern already proven in ai_extractor.py, just pointed at a
customer's own words instead of a supplier's contract document.

WHY A NEW FILE INSTEAD OF ADDING TO ai_extractor.py: ai_extractor.py's extraction prompts are
all about READING A SUPPLIER CONTRACT (post-booking, B2B, high-stakes accuracy on money/dates
that go straight into an API payload). This is READING A CUSTOMER'S OWN WORDS (pre-booking,
public-facing, best-effort - if it's wrong, the customer sees a search box to correct it, not a
publish button). Different audience, different accuracy bar, different failure mode - keeping
it a separate module avoids either one's conventions leaking into the other.

REUSES ai_extractor's Claude plumbing (_get_anthropic_client / _call_claude via
_stream_claude_tool_call's forced-tool-call pattern) rather than duplicating it - same reasons
that pattern was built there in the first place: guarantees well-formed structured JSON back
(no manual parsing), and gets prompt caching on the system prompt for free.
"""
from typing import Any, Dict, Optional

import ai_extractor as ax

TRIP_PROMPT_MODEL = "claude-sonnet-5"

TRIP_PROMPT_TOOL_NAME = "provide_trip_criteria"

# A REAL schema (not the permissive one ai_extractor's document extraction uses) - this output
# feeds a search form, so a dropped field is a search filter silently not applied, not just an
# odd-looking payload. "required" only covers the two fields a search is meaningless without
# (destination, party) - everything else is optional because a real customer prompt often
# doesn't mention it ("theme" and "dates" are the most commonly missing).
TRIP_PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "destination_country": {
            "type": "string",
            "description": "The country the customer wants to travel to, e.g. 'Spain'. Empty string "
                            "if genuinely not mentioned or not resolvable to a real country.",
        },
        "destination_region_or_city": {
            "type": "string",
            "description": "A more specific place WITHIN the country if the customer named one "
                            "(e.g. 'Costa Brava', 'Barcelona'). Empty string if they only named the "
                            "country, or a broad idea like 'the coast' with nothing specific enough "
                            "to search on.",
        },
        "travel_month": {
            "type": "string",
            "description": "The month the customer wants to travel, as a full English month name "
                            "(e.g. 'February'). Empty string if not mentioned.",
        },
        "date_range_start": {
            "type": "string",
            "description": "ISO date (YYYY-MM-DD) if the customer gave an actual specific date or a "
                            "narrow enough range to resolve to one - e.g. 'the second week of "
                            "February'. Empty string if only a vague month/season was given; don't "
                            "invent a specific day the customer didn't imply.",
        },
        "date_range_end": {
            "type": "string",
            "description": "ISO date (YYYY-MM-DD), same rule as date_range_start.",
        },
        "duration_nights": {
            "type": ["integer", "null"],
            "description": "Number of nights, if stated or clearly implied (e.g. 'a week' -> 7, "
                            "'long weekend' -> 3). null if not mentioned.",
        },
        "adults": {
            "type": "integer",
            "description": "Number of adults. Default to 2 ONLY if the prompt implies a couple/pair "
                            "('we', 'my partner and I') without a number; default to 1 if the prompt "
                            "is clearly singular ('I want to go...'); otherwise use the stated number.",
        },
        "children": {
            "type": "integer",
            "description": "Number of children. 0 if not mentioned - never guess a positive number "
                            "that wasn't stated or implied.",
        },
        "children_ages": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Any specific child ages mentioned, in the order given. Empty array if "
                            "none were stated, even if `children` > 0.",
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The kinds of experience the customer described, as short lowercase "
                            "tags from their own words (e.g. 'city', 'beach', 'relaxation', "
                            "'adventure', 'culture', 'family', 'honeymoon', 'nightlife', 'nature'). "
                            "Use what the customer actually said or clearly implied - do not invent "
                            "themes they didn't suggest. Empty array if none were expressed.",
        },
        "budget_hint": {
            "type": "string",
            "description": "Any budget signal in the customer's own terms (e.g. 'budget-friendly', "
                            "'around 1500 euros per person', 'luxury'). Empty string if none given.",
        },
        "budget_tier": {
            "type": "string",
            "enum": ["budget", "superior", "luxury", "unspecified"],
            "description": "CONFIRMED PRODUCT-OWNER RULE (2026-08-19): the customer's budget language "
                            "mapped to one of Momira's three tiers - 'budget' (e.g. 'budget-friendly', "
                            "'cheap', 'affordable', 'on a budget'), 'superior' (e.g. 'nice hotel', "
                            "'upscale', 'a bit more comfort', '4-star'), or 'luxury' (e.g. 'luxury', "
                            "'high-end', '5-star', 'the best', 'money is no object'). 'unspecified' if "
                            "the customer gave no budget signal at all. This tier - NOT budget_hint - "
                            "is what actually drives the hotel star rating / board type / minimum "
                            "review score rules (see trip_search_rules.py); do not invent a tier from "
                            "a vague prompt that gave no real signal - 'unspecified' is a valid and "
                            "often correct answer.",
        },
        "car_wanted": {
            "type": "boolean",
            "description": "true only if the customer explicitly mentioned wanting a rental car / "
                            "self-drive / road trip. false if not mentioned, even if transfers or "
                            "getting around are implied some other way - never assume a car is wanted.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "'high' if destination and party size are both clear and unambiguous. "
                            "'medium' if one of them had to be inferred/defaulted (e.g. party size "
                            "assumed from 'we'). 'low' if the prompt is too vague to search on "
                            "usefully (e.g. no destination at all, or contradictory information).",
        },
        "clarification_needed": {
            "type": "string",
            "description": "If confidence is 'medium' or 'low', a SHORT, friendly, single question "
                            "to ask the customer to fill the biggest gap (e.g. 'Which country or "
                            "region did you have in mind?'). Empty string if confidence is 'high'.",
        },
    },
    "required": ["destination_country", "adults", "themes", "budget_tier", "confidence"],
}

TRIP_PROMPT_SYSTEM_PROMPT = """You are reading a PROSPECTIVE CUSTOMER's own free-text description of a
trip they want, on a tour operator's public website search box. Your job is ONLY to turn their words
into structured search criteria - you are not booking anything, not confirming availability, and not
making up details they didn't give you.

THE CUSTOMER IS NOT A TRAVEL PROFESSIONAL. They write casually, may mix languages, may be vague about
dates ("sometime in spring", "next month"), and may describe what they want by mood or activity
("something relaxing", "lots to see and do") rather than a formal theme taxonomy. Map their words to
the closest reasonable tags in the schema, but do not invent specifics they didn't give you - it is
far better to leave a field empty (or flag low/medium confidence) than to guess a destination, date,
or party size that isn't actually in their prompt. A wrong GUESS shown back to a real customer as if it
were understood correctly is worse than an honest "I need one more detail from you".

DESTINATION: extract the country if given. Only extract a specific region/city if the customer actually
named one - "somewhere warm in southern Europe" has no resolvable destination_country; "Spain" does,
even without a specific city.

PARTY SIZE: never invent a number that wasn't stated or clearly implied. "We" or "my partner and I"
without a number reasonably implies 2 adults - state that inference by returning confidence "medium",
not "high". A completely unstated party size should still get a best-effort default (2 adults is the
most common real case) but confidence must reflect that it was assumed, not read.

DATES: many prompts will only mention a month or a vague season, not exact dates - that's normal and
expected, leave date_range_start/end empty in that case and just fill travel_month. Only fill
date_range_start/end when the prompt is specific enough to actually resolve a range (an exact date, a
named week, "the first two weeks of March").

THEMES: use the customer's own words/intent, not a fixed list you must fill from - "city and beach"
should produce ["city", "beach"], not additional themes they didn't mention.

BUDGET_TIER: only set 'budget', 'superior', or 'luxury' when the customer's own words actually signal
one of those tiers - do not infer a tier from destination or theme alone (a beach trip is not
automatically 'budget'). No signal at all means 'unspecified', which is a normal, common, correct
answer - not a fallback to avoid.

Call the provide_trip_criteria tool with the structured result. Do not include any other commentary."""


def extract_trip_criteria(customer_prompt: str, model: str = TRIP_PROMPT_MODEL) -> Dict[str, Any]:
    """The one function this prototype needs: customer's free text in, structured search
    criteria out. Reuses ai_extractor._call_claude for the actual model call (forced tool call,
    so the JSON is guaranteed well-formed - see that function's docstring)."""
    text = (customer_prompt or "").strip()
    if not text:
        return {
            "destination_country": "", "destination_region_or_city": "", "travel_month": "",
            "date_range_start": "", "date_range_end": "", "duration_nights": None,
            "adults": 2, "children": 0, "children_ages": [], "themes": [], "budget_hint": "",
            "budget_tier": "unspecified", "car_wanted": False,
            "confidence": "low", "clarification_needed": "What kind of trip are you looking for?",
        }
    return ax._call_claude(
        TRIP_PROMPT_SYSTEM_PROMPT, text, model,
        max_tokens=1024, input_schema=TRIP_PROMPT_SCHEMA,
    )
