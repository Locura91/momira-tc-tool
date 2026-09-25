"""Regression test for a real production bug (product owner, 2026-09-25, same day the bulk
Transfer-image tool shipped): a bulk image update was applied through flows/transfer_image_
bulk.py's "Load" step, and the Transfer's Images tab in Travel Compositor then showed the raw
R2 URL as unrendered/broken text instead of a picture.

ROOT CAUSE: r2_client.upload_image's PUT succeeding is not proof the resulting URL is actually
publicly fetchable - see r2_client.verify_public_url's own docstring for the identical bug
already found and fixed once before (2026-09-18, document-image uploads: a bucket with Public
Access not enabled, or a misconfigured R2_PUBLIC_BASE_URL pointing at R2's private S3 API
endpoint instead of a real public URL, makes every upload "succeed" while the URL 404s for
everyone, including Travel Compositor's own fetch). flows/transfer_image_bulk.py called the
raw, unverified upload_image() and skipped that check entirely - the one place in the app that
had.

flows/*.py files can't be imported outside the real Streamlit app (see every other flows test's
own docstring for why) - this reads the module's source text instead, the same approach every
other test on a flows/*.py file in this suite already uses.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_load_step_calls_verify_public_url_before_scanning():
    src = _read("flows", "transfer_image_bulk.py")
    upload_idx = src.index("r2_client.upload_image(")
    verify_idx = src.index("r2_client.verify_public_url(")
    plan_idx = src.index("tib.plan(")
    # verify_public_url must run AFTER the upload (there has to be a URL to check) and BEFORE
    # plan() (nothing gets scanned, let alone written, with an unverified URL).
    assert upload_idx < verify_idx < plan_idx


def test_a_failed_verification_returns_before_plan_is_ever_called():
    src = _read("flows", "transfer_image_bulk.py")
    verify_block = src[src.index("ok, reason = r2_client.verify_public_url("):]
    # The failure branch must return (not just warn) - same "never write with a bad image"
    # rule the rest of this screen follows.
    not_ok_branch = verify_block[:verify_block.index("bar = st.progress(0.0, text=\"Scanning...\")")]
    assert "if not ok:" in not_ok_branch
    assert "return" in not_ok_branch


def test_verification_applies_to_both_the_upload_and_the_pasted_url_source():
    """The check sits after the source branch resolves final_url, not inside the "Upload from
    my computer" branch only - a pasted URL (never touched R2) must be checked too, since a
    typo'd or unreachable pasted URL is just as damaging to a live Transfer record."""
    src = _read("flows", "transfer_image_bulk.py")
    upload_branch_end = src.index("# CONFIRMED BUG FIX (product owner, 2026-09-25")
    upload_branch_start = src.index('if source == "Upload from my computer":', src.index('if st.button("📥 Load"'))
    upload_branch = src[upload_branch_start:upload_branch_end]
    # verify_public_url is called exactly once, after the if/else that sets final_url for
    # either source - not duplicated inside the upload branch only, and not skipped for a
    # pasted URL (which never touches R2 at all but is just as capable of being unreachable).
    assert src.count("r2_client.verify_public_url(") == 1
    assert "verify_public_url" not in upload_branch
