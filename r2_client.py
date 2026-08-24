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
import uuid
import mimetypes

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
    # "image.jpg", which would silently overwrite unrelated uploads sharing the same key.
    key = f"{uuid.uuid4().hex}.{ext}"
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
    """
    urls, errors = [], []
    for img_bytes, ext in image_list:
        filename = f"image.{ext or 'jpg'}"
        try:
            urls.append(upload_image(img_bytes, filename=filename))
        except Exception as e:
            errors.append(f"{filename}: {e}")
    return urls, errors
