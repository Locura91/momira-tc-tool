"""Regression tests for api_client.describe_tc_fetch_error - added after a real incident
(2026-08-25): translating Closed Tour TNR-01 for supplier 50370 failed with

    {"error": 400, "message": "{\"error\":[\"java.lang.NullPointerException: Cannot invoke
    \\\"com.tr2.entity.AgeRange.getMin()\\\" because \\\"ageRange\\\" is null\"],
    \"status\":\"BAD_REQUEST\"}"}

Travel Compositor's own server threw an unhandled NullPointerException while reading that
record's stored data and wrapped it in a 400 - a bug on their side, not anything this tool sent
(a plain GET, no payload). Before this fix, that raw nested-JSON Java stack trace was the only
thing shown to the user (in the "Full result" JSON expander), with no plain-language explanation
of whose problem it was or what to do about it - the same courtesy sync_closed_tour() already
gives a wrong/missing code (a clear "not found" message) was missing for this class of error.
"""
from api_client import describe_tc_fetch_error


# The exact real detail dict from the TNR-01 incident.
REAL_NPE_DETAIL = {
    "error": 400,
    "message": ('{"error":["java.lang.NullPointerException: Cannot invoke '
                '\\"com.tr2.entity.AgeRange.getMin()\\" because \\"ageRange\\" is null"],'
                '"status":"BAD_REQUEST"}'),
}


def test_real_incident_detail_is_recognized_as_a_tc_server_bug():
    msg = describe_tc_fetch_error(REAL_NPE_DETAIL, "closed tour 'TNR-01'")
    assert "closed tour 'TNR-01'" in msg
    assert "Travel Compositor's own server" in msg
    assert "not a mistake in what was entered here or anything this tool sent" in msg
    assert "Contact Travel Compositor support" in msg


def test_nullpointerexception_detected_case_insensitively_and_without_the_json_wrapper():
    """The inner `message` isn't always double-JSON-encoded - a caller might pass the exception
    text more directly. The signature check must still fire."""
    msg = describe_tc_fetch_error({"error": 400, "message": "nullpointerexception: boom"}, "X")
    assert "NullPointerException" in msg


def test_generic_5xx_gets_a_temporary_error_message_not_the_npe_message():
    msg = describe_tc_fetch_error({"error": 500, "message": "Internal Server Error"}, "hotel ABC")
    assert "hotel ABC" in msg
    assert "usually temporary" in msg
    assert "NullPointerException" not in msg


def test_generic_4xx_gets_a_see_full_result_message():
    msg = describe_tc_fetch_error({"error": 403, "message": "Forbidden"}, "ticket XYZ")
    assert "ticket XYZ" in msg
    assert "403" in msg
    assert "Full result" in msg


def test_non_dict_detail_does_not_crash():
    msg = describe_tc_fetch_error("not a dict at all", "something")
    assert "unexpected error" in msg
    assert "something" in msg


def test_missing_message_field_does_not_crash():
    msg = describe_tc_fetch_error({"error": 400}, "closed tour Z")
    assert "closed tour Z" in msg


def test_entity_label_defaults_to_a_generic_phrase_when_not_given():
    msg = describe_tc_fetch_error({"error": 500})
    assert "this record" in msg
