"""Regression test for a real bug report (product owner, 2026-09-18, verbatim):

    "when adding pictures from the document, it is still not working. But what should easier
    work are images coming from an URL, as those images are already hosted. This is also not
    working so far."

Follow-up screenshot: the "Images found in your document/page (24)" picker (Hotel/ClosedTour/
Ticket all share this same pool - fed by r2_client.upload_images_with_errors via
ui_components._add_page_images_to_doc_pool and the document-extraction call sites in flows/
hotel.py, flows/ticket.py, flows/multi_tour.py, app.py) showed ALL 24 thumbnails rendering as
broken images, with no error or warning shown anywhere.

Root cause: r2_client.py has always trusted boto3's put_object succeeding as proof an uploaded
image would actually be usable afterward. A successful PUT only proves the WRITE credentials
work - it says nothing about whether the bucket's PUBLIC ACCESS is enabled in Cloudflare (a
separate manual dashboard setting, see r2_client.py's own setup docs, step 3). When it isn't
enabled (or R2_PUBLIC_BASE_URL is wrong), every upload still "succeeds" from this app's point of
view - a real-looking URL comes back, no exception - while every one of those URLs is actually
unreachable for anyone who tries to fetch it, including the browser rendering the picker. That
matches the reported shape exactly: not one bad image among many, but ALL of them, with nothing
surfaced anywhere.

Fix: r2_client.upload_images_with_errors now fetches each freshly-uploaded URL back once before
handing it to the caller (verify_public_url) - a URL that isn't actually publicly reachable, or
whose body isn't genuinely an image (magic-byte check, not the server's own Content-Type header),
is now reported as a failure with an explicit "check your R2 bucket's Public Access setting"
hint, instead of silently landing in the picker as a broken thumbnail.
"""
import r2_client


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", raise_on_get=None):
        self.status_code = status_code
        self._body = body
        self._raise_on_get = raise_on_get
        self.closed = False

    def iter_content(self, chunk_size=32):
        body = self._body
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]

    def close(self):
        self.closed = True


_REAL_PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def test_verify_public_url_true_for_a_real_image_response(monkeypatch):
    monkeypatch.setattr(r2_client.requests, "get",
                         lambda url, timeout=None, stream=None: _FakeResponse(200, _REAL_PNG_HEAD))
    ok, reason = r2_client.verify_public_url("https://images.example.com/some-key.png")
    assert ok is True
    assert reason == ""


def test_verify_public_url_false_on_non_200_status(monkeypatch):
    monkeypatch.setattr(r2_client.requests, "get",
                         lambda url, timeout=None, stream=None: _FakeResponse(404, b""))
    ok, reason = r2_client.verify_public_url("https://images.example.com/missing.png")
    assert ok is False
    assert "404" in reason
    assert "Public Access" in reason


def test_verify_public_url_false_when_response_isnt_actually_an_image(monkeypatch):
    # The exact "Public Access disabled" shape: HTTP 200, but the body is an HTML challenge/
    # error page rather than real image bytes.
    monkeypatch.setattr(r2_client.requests, "get",
                         lambda url, timeout=None, stream=None: _FakeResponse(200, b"<html>not found</html>"))
    ok, reason = r2_client.verify_public_url("https://images.example.com/some-key.png")
    assert ok is False
    assert "isn't a real image" in reason
    assert "Public Access" in reason


def test_verify_public_url_false_when_request_raises(monkeypatch):
    def _raise(url, timeout=None, stream=None):
        raise ConnectionError("boom")
    monkeypatch.setattr(r2_client.requests, "get", _raise)
    ok, reason = r2_client.verify_public_url("https://images.example.com/some-key.png")
    assert ok is False
    assert "not reachable" in reason
    assert "boom" in reason


def test_verify_public_url_closes_the_response(monkeypatch):
    resp = _FakeResponse(200, _REAL_PNG_HEAD)
    monkeypatch.setattr(r2_client.requests, "get", lambda url, timeout=None, stream=None: resp)
    r2_client.verify_public_url("https://images.example.com/some-key.png")
    assert resp.closed is True


def test_upload_images_with_errors_excludes_urls_that_fail_verification(monkeypatch):
    monkeypatch.setattr(r2_client, "upload_image",
                         lambda img_bytes, filename="image.jpg": f"https://images.example.com/{filename}")
    monkeypatch.setattr(r2_client, "verify_public_url", lambda url: (False, "HTTP 404 - check X"))
    urls, errors = r2_client.upload_images_with_errors([(b"a", "jpg"), (b"b", "png")])
    assert urls == []
    assert len(errors) == 2
    assert "uploaded to R2, but HTTP 404" in errors[0]
    assert "image_1.jpg" in errors[0]
    assert "image_2.png" in errors[1]


def test_upload_images_with_errors_keeps_urls_that_pass_verification(monkeypatch):
    monkeypatch.setattr(r2_client, "upload_image",
                         lambda img_bytes, filename="image.jpg": f"https://images.example.com/{filename}")
    monkeypatch.setattr(r2_client, "verify_public_url", lambda url: (True, ""))
    urls, errors = r2_client.upload_images_with_errors([(b"a", "jpg"), (b"b", "png")])
    assert urls == [
        "https://images.example.com/image_1.jpg",
        "https://images.example.com/image_2.png",
    ]
    assert errors == []


def test_upload_images_with_errors_mixed_batch_keeps_only_the_good_ones(monkeypatch):
    monkeypatch.setattr(r2_client, "upload_image",
                         lambda img_bytes, filename="image.jpg": f"https://images.example.com/{filename}")

    def _fake_verify(url):
        return (True, "") if "image_1" in url else (False, "HTTP 404 - check X")
    monkeypatch.setattr(r2_client, "verify_public_url", _fake_verify)

    urls, errors = r2_client.upload_images_with_errors([(b"a", "jpg"), (b"b", "png")])
    assert urls == ["https://images.example.com/image_1.jpg"]
    assert len(errors) == 1
    assert "image_2.png" in errors[0]


def test_upload_images_with_errors_still_labels_a_real_upload_failure_per_image(monkeypatch):
    # Pre-existing behavior (2026-09-02 fix) must survive unchanged: an upload_image failure is
    # reported per-image and verify_public_url is never reached for it.
    calls = []

    def _boom(img_bytes, filename="image.jpg"):
        raise RuntimeError("simulated upload failure")

    def _should_not_be_called(url):
        calls.append(url)
        return (True, "")

    monkeypatch.setattr(r2_client, "upload_image", _boom)
    monkeypatch.setattr(r2_client, "verify_public_url", _should_not_be_called)
    urls, errors = r2_client.upload_images_with_errors([(b"a", "jpg"), (b"b", "png"), (b"c", "jpg")])
    assert urls == []
    assert len(errors) == 3
    assert errors[0].startswith("image_1.jpg:")
    assert errors[1].startswith("image_2.png:")
    assert errors[2].startswith("image_3.jpg:")
    assert calls == []


def test_looks_like_real_image_bytes_accepts_common_formats():
    assert r2_client._looks_like_real_image_bytes(b"\xff\xd8\xff" + b"\x00" * 10) is True
    assert r2_client._looks_like_real_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10) is True
    assert r2_client._looks_like_real_image_bytes(b"GIF89a" + b"\x00" * 10) is True
    assert r2_client._looks_like_real_image_bytes(b"BM" + b"\x00" * 10) is True
    assert r2_client._looks_like_real_image_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10) is True


def test_looks_like_real_image_bytes_rejects_html_and_empty():
    assert r2_client._looks_like_real_image_bytes(b"<html><body>404</body></html>") is False
    assert r2_client._looks_like_real_image_bytes(b"") is False
    assert r2_client._looks_like_real_image_bytes(None) is False
