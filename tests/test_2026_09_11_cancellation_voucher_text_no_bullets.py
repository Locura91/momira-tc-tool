"""Regression test for the 2026-09-11 "no bullet points" fix to builder._cancellation_voucher_text.

Product owner, verbatim: "please change also the writting structure: From Old: 'Cancellation
Policy: - Free cancellation if cancelled at least 30 days before arrival.' To NEW: Cancellation
Policy: Free cancellation if cancelled at least 30 days before arrival.' as in the remarks are no
bullet points allowed, we just need to write a text without '-'"

Same underlying rule as the 2026-08-25 fix that stripped bullet markers from Ticket's Modality
Remarks field (see builder._strip_bullet_points) - now applied directly at the source for every
Voucher Remarks write this shared synthesizer produces (Transport, Transfer, and anywhere else a
real tier list reaches it), rather than needing a separate strip step.

builder.parse_cancellation_tiers_from_voucher_text already tolerates a leading "-"/"•" or none at
all (it strips one if present before matching), so this is verified as a safe one-way change: old
bulleted text still parses back into the same tiers, and multi-tier round-tripping still works.
"""
import builder


def test_single_tier_free_cancellation_has_no_leading_dash():
    text = builder._cancellation_voucher_text(None, [(30, 100.0)])
    assert text == "Cancellation Policy:\nFree cancellation if cancelled at least 30 days before arrival."
    assert "- " not in text
    assert "-Free" not in text


def test_no_refund_tier_has_no_leading_dash():
    text = builder._cancellation_voucher_text(None, [(14, 0.0)])
    assert text == "Cancellation Policy:\nNo refund if cancelled less than 14 days before arrival."


def test_partial_fee_tier_has_no_leading_dash():
    text = builder._cancellation_voucher_text(None, [(14, 75.0)])
    assert text == ("Cancellation Policy:\n"
                    "25% cancellation fee if cancelled less than 14 days before arrival (75% refund).")


def test_day_of_arrival_tiers_have_no_leading_dash():
    no_refund = builder._cancellation_voucher_text(None, [(0, 0.0)])
    assert no_refund == "Cancellation Policy:\nNo refund for cancellations on the day of arrival or no-shows."
    partial = builder._cancellation_voucher_text(None, [(0, 50.0)])
    assert partial == ("Cancellation Policy:\n"
                       "50% cancellation fee on the day of arrival or for no-shows (50% refund).")


def test_multi_tier_policy_has_no_dashes_on_any_line():
    text = builder._cancellation_voucher_text(None, [(90, 100.0), (30, 50.0), (0, 0.0)])
    lines = text.split("\n")
    assert lines[0] == "Cancellation Policy:"
    for line in lines[1:]:
        assert not line.startswith("- "), f"line still has a bullet marker: {line!r}"
    assert text == (
        "Cancellation Policy:\n"
        "Free cancellation if cancelled at least 90 days before arrival.\n"
        "50% cancellation fee if cancelled less than 30 days before arrival (50% refund).\n"
        "No refund for cancellations on the day of arrival or no-shows."
    )


def test_new_bullet_free_text_still_round_trips_through_the_parser():
    for tiers in ([(30, 100.0)], [(90, 100.0), (30, 50.0), (0, 0.0)], [(14, 25.0)]):
        text = builder._cancellation_voucher_text(None, tiers)
        assert builder.parse_cancellation_tiers_from_voucher_text(text) == tiers


def test_old_bulleted_text_still_parses_correctly_backward_compatible():
    # A live record written before this fix still has "- " bullets - the parser (used by the
    # existing_stricter/unchanged checks) must keep reading those correctly; only future writes
    # stop producing them.
    old_text = ("Cancellation Policy:\n"
               "- Free cancellation if cancelled at least 60 days before arrival.")
    assert builder.parse_cancellation_tiers_from_voucher_text(old_text) == [(60, 100.0)]
