"""
Quick manual runner for trip_prompt_extractor.py — NOT part of the pytest suite (deliberately;
this makes a real, billed Anthropic API call, which the automated suite must never do — see
tests/conftest.py, which strips ANTHROPIC_API_KEY before any pytest run).

Run this yourself, wherever ANTHROPIC_API_KEY is actually set (your local .env, or the deployed
app's environment) - this sandbox that built the prototype does not have it, so nothing could be
run live from there.

Usage:
    python try_trip_prompt_extractor.py

Deliberately named without a "test_" prefix (unlike tests/test_trip_prompt_extractor.py, the
real pytest coverage for this module's pure/no-API-call logic) so it can never be accidentally
picked up by pytest and so there's no filename collision with that file.
"""
import json

from trip_prompt_extractor import extract_trip_criteria

EXAMPLE_PROMPTS = [
    "2 adults, travelling in February, with goal of city and beach in Spain",
    "we'd love something relaxing by the sea next month, budget-friendly",
    "Family trip with 2 kids (ages 6 and 9) to Portugal this summer, looking for beaches and "
    "some culture too, about 10 days",
    "honeymoon somewhere romantic",
    "solo trip, adventure and hiking, Norway, first two weeks of September",
]

if __name__ == "__main__":
    for prompt in EXAMPLE_PROMPTS:
        print("=" * 70)
        print("PROMPT:", prompt)
        result = extract_trip_criteria(prompt)
        print(json.dumps(result, indent=2))
        print()
