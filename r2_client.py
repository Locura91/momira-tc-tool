"""
Uploads image bytes to a private Cloudflare R2 bucket you own, returning a real public URL.
Used for images extracted from documents (PDF/Word/Excel), which only exist as raw bytes
until hosted somewhere Travel Compositor can reference (its API only accepts an image URL,
never raw bytes).

CONFIRMED (product owner, 2026-08-22): replaces freeimage_client.py. The supplier documents
these images come from are licensed "for the website only" - freeimage.host (and similar free
image hosts) puts the raw image on a THIRD PARTY's public servers with no access control,
which is a second, independent distribution channel outside momira.travel and outside your
control. R2 keeps the same "Travel Compositor just needs a public URL to fetch" mechanism, but
the bucket lives on infrastructure YOU own and control, under your own domain if you set up a
custom domain for it (see setup steps below) - same public-URL requirement, no third party.

R2 was chosen over S3 specifically because R2 has NO EGRESS FEES, ever, on its free tier or
paid tiers - every time a visitor loads a page with one of these images, or Travel Compositor's
own systems fetch it, that's an egress hit. S3 charges per GB after a limited free-tier
allowance; R2 does not, which matters a lot for images that get fetched repeatedly and
indefinitely.

============================== ONE-TIME SETUP (do this yourself - I cannot create the account
or bucket for you) ==============================
1. Create a free Cloudflare account at https://dash.cloudflare.com/sign-up (no credit card
   required for R2's free tier: 10 GB storage, 1M Class A + 10M Class B operations/month free).
2. In the Cloudflare dashboard, go to R2 Object Storage -> Create bucket. Name it something
   like "momira-images". Choose any location hint.
3. PUBLIC ACCESS - two options:
   a) Custom domain (RECOMMENDED for production use, e.g. images.momira.travel): in the
      bucket's Settings -> Public access -> Custom Domains, connect a subdomain of a domain
      you already control in Cloudflare. This is what makes the images genuinely "yours" -
      served from your own domain, not a third party's.
   b) r2.dev subdomain (quick to set up, fine for testing): Settings -> Public access -> enable
      the r2.dev subdomain. Cloudflare's own docs note this is rate-limited and not recommended
      for production traffic, but it's the fastest way to get something working today.
   Either way, note the resulting base URL (e.g. "https://images.momira.travel" or
   "https://pub-xxxxxxxx.r2.dev") - this is R2_PUBLIC_BASE_URL below.
4. Generate API credentials: R2 -> Manage R2 API Tokens -> Create API Token. Give it "Object
   Read & Write" permission, scoped to just this bucket if possible. Copy the Access Key ID and
   Secret Access Key it shows you ONCE (Cloudflare will not show the secret again).
5. Find your Account ID: shown on the R2 Object Storage overview page in the dashboard sidebar.
6. CONFIRMED (product owner, 2026-08-22): these images are only needed long enough for Travel
   Compositor to fetch them once during publish - a real captured example
   (cdn.travelconline.com serving from tr2storage.blob.core.windows.net) confirms Travel
   Compositor downloads and re-hosts the image into its OWN storage rather than hotlinking your
   URL forever, so nothing needs to stay in this bucket afterward. Rather than having the app
   delete each image itself right after publish (a real race-condition risk if Travel
   Compositor's own fetch happens asynchronously rather than during the API call itself - delete
   too early and you'd leave a broken image on your live site), set an R2 LIFECYCLE RULE to
   auto-expire objects a couple of days after upload - this has zero race-condition risk (Travel
   Compositor has ample time either way) and needs no code:
     Bucket -> Settings -> Object lifecycle rules -> Add rule -> "Delete objects" ->
     "X days after upload" (2 days is a reasonable default - long enough for any reasonable
     ingestion delay on Travel Compositor's side, short enough that nothing lingers). Applies to
     the whole bucket automatically; nothing else to configure.
7. Set these five values as environment variables (.env locally, Streamlit Secrets when
   deployed) - never paste real credentials into a chat, a document, or commit them to git:
     R2_ACCOUNT_ID=<your Cloudflare account ID>
     R2_ACCESS_KEY_ID=<from step 4>
     R2_SECRET_ACCESS_KEY=<from step 4>
     R2_BUCKET_NAME=momira-images  (or whatever you named it)
     R2_PUBLIC_BASE_URL=https://images.momira.travel   (or your r2.dev URL, no trailing slash)
8. Add "boto3" to requirements.txt (R2 speaks the S3 API, so the standard AWS SDK works against
   it unmodified - just point it at R2's endpoint instead of AWS's) and `pip install boto3`.

Same interface as freeimage_client.py (upload_images / upload_images_with_errors) so it's a
drop-in replacement - only the import line in app.py / ui_components.py needs to change.
"""
import os
import time
import uuid
import mimetypes
from dotenv import load_dotenv

MODULE_BUILD = "2026-09-03-ticket-modality-code-default-and-html-preview"

# CONFIRMED FIX (2026-08-22): this module reads its five R2_* values via os.getenv() below, but
# nothing was actually loading the .env file into the process environment - the old
# freeimage_client.py called load_dotenv() itself, and nothing replaced that call when this
# module took over. Without this, a perfectly correct local .env file would silently never be
# read (os.getenv returns None for everything, surfacing as "Missing R2 credential(s)" even
# though the file is right there) - self-contained here so it doesn't depend on some other
# module in the app having already called it first.
load_dotenv()

_EXT_TO_CONTENT_TYPE = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}


def _get_client():
    """Builds a boto3 S3 client pointed at R2's S3-compatible endpoint. Imports boto3 lazily so
    the rest of the app doesn't hard-fail on import if boto3 isn't installed yet in an
    environment that never touches this module."""
    try:
        import boto3
    except ImportError:
        raise RuntimeError(
            "The 'boto3' package is required for R2 uploads and isn't installed - "
            "run `pip install boto3` (it's also in requirements.txt)."
        )
    account_id = os.getenv("R2_ACCOUNT_ID", "")
    access_key = os.getenv("R2_ACCESS_KEY_ID", "")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY", "")
    missing = [name for name, val in [
        ("R2_ACCOUNT_ID", account_id), ("R2_ACCESS_KEY_ID", access_key),
        ("R2_SECRET_ACCESS_KEY", secret_key),
    ] if not val]
    if missing:
        raise RuntimeError(
            f"Missing R2 credential(s): {', '.join(missing)}. See r2_client.py's module "
            f"docstring for the one-time setup steps (create a free Cloudflare account, "
            f"create an R2 bucket, generate an API token), then set these as environment "
            f"variables in your .env (locally) or Streamlit Secrets (when deployed)."
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


# CONFIRMED (full-app audit MED, 2026-09-02): the lifecycle-rule expiry window this module's
# own setup docs recommend (2 days, step 6 above) can legitimately be shorter than a real review
# - a document can sit in review for several days before the operator publishes. Travel
# Compositor's fetch at publish time would then silently 404 against an already-expired object,
# with nothing in the app telling the operator why. The upload key now embeds the upload
# timestamp so a caller can warn BEFORE publish that a given image URL is old enough to plausibly
# have expired already, rather than only finding out from a downstream publish failure. Kept
# comfortably under the 2-day (48h) lifecycle rule so the warning fires with room to re-upload.
STALE_IMAGE_WARNING_THRESHOLD_HOURS = 42


def _upload_timestamp_key(ext: str) -> str:
    # Embeds the upload time as a leading decimal segment so it can be recovered later purely
    # from the URL, with no separate database - `image_upload_age_hours` below is the reader.
    return f"{int(time.time())}-{uuid.uuid4().hex}.{ext}"


def image_upload_age_hours(url: str):
    """Returns how many hours ago an R2 URL from this module was uploaded, or None if the URL
    doesn't carry a recognizable embedded timestamp (e.g. a manually pasted URL, or one uploaded
    before this timestamp-embedding was added)."""
    if not url:
        return None
    key = url.rsplit("/", 1)[-1]
    ts_part = key.split("-", 1)[0]
    if not ts_part.isdigit():
        return None
    uploaded_at = int(ts_part)
    return max(0.0, (time.time() - uploaded_at) / 3600.0)


def stale_image_urls(urls, threshold_hours: float = STALE_IMAGE_WARNING_THRESHOLD_HOURS) -> list:
    """Given an iterable of R2 URLs, returns the subset old enough to plausibly have already
    expired under the bucket's lifecycle rule (or be about to). URLs with no recoverable
    timestamp (manual/legacy URLs) are never flagged - there's nothing to warn about reliably."""
    stale = []
    for url in urls or []:
        age = image_upload_age_hours(url)
        if age is not None and age >= threshold_hours:
            stale.append(url)
    return stale


def stale_image_warning(urls) -> str:
    """Human-readable warning for the operator's review screen if any of `urls` looks stale, or
    "" if none do. Callers wire this into whichever review/publish screen holds the URLs, the
    same "surface it, don't publish silently wrong" pattern this app uses for scanned-document
    and short-page-text warnings."""
    stale = stale_image_urls(urls)
    if not stale:
        return ""
    plural = "s" if len(stale) != 1 else ""
    return (
        f"⚠️ {len(stale)} image{plural} were uploaded more than "
        f"{int(STALE_IMAGE_WARNING_THRESHOLD_HOURS)}h ago and may have already expired from "
        f"temporary hosting - if Travel Compositor's own fetch fails at publish time, re-extract "
        f"or re-upload the affected image(s) first."
    )


def upload_image(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """Uploads raw image bytes to your R2 bucket, returns the public URL."""
    bucket = os.getenv("R2_BUCKET_NAME", "")
    base_url = os.getenv("R2_PUBLIC_BASE_URL", "")
    if not bucket or not base_url:
        raise RuntimeError(
            "Missing R2_BUCKET_NAME and/or R2_PUBLIC_BASE_URL. See r2_client.py's module "
            "docstring for the one-time setup steps."
        )
    client = _get_client()
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    content_type = _EXT_TO_CONTENT_TYPE.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # A random key, not the original filename - every document reuses generic names like
    # "image.jpg", which would silently overwrite unrelated uploads sharing the same key. Also
    # now carries the upload timestamp as a leading segment - see _upload_timestamp_key.
    key = _upload_timestamp_key(ext)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=image_bytes, ContentType=content_type)
    except Exception as e:
        raise RuntimeError(f"R2 upload failed: {e}")
    return f"{base_url.rstrip('/')}/{key}"


def upload_images(image_list: list) -> list:
    """
    image_list: list of (image_bytes, extension) tuples.
    Returns list of successfully uploaded URLs - skips (doesn't crash on) any individual image
    that fails to upload, since one bad image shouldn't block the whole extraction. Same
    silent-skip contract as freeimage_client.upload_images() - see upload_images_with_errors()
    below when the caller needs to know WHY an upload failed.
    """
    urls, _ = upload_images_with_errors(image_list)
    return urls


def upload_images_with_errors(image_list: list) -> tuple:
    """
    Same upload as upload_images(), but returns (urls, errors) instead of discarding the reason
    for each failure - errors is a list of "filename: message" strings, one per failed image, in
    the order they were attempted.

    CONFIRMED BUG FIX (full-app audit LOW, 2026-09-02): every failed upload used to be labeled
    the same generic "image.jpg" regardless of which one it actually was, so a batch with several
    failures gave no way to tell which image(s) failed. Each attempt is now numbered
    (1-based, matching the image's position in image_list) so failures are distinguishable.
    """
    urls, errors = [], []
    for idx, (img_bytes, ext) in enumerate(image_list, start=1):
        filename = f"image_{idx}.{ext or 'jpg'}"
        try:
            urls.append(upload_image(img_bytes, filename=filename))
        except Exception as e:
            errors.append(f"{filename}: {e}")
    return urls, errors
