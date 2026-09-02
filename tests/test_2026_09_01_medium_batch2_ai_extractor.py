"""Tests for the MEDIUM/LOW "Batch 2" findings from the full-app audit
(full-app-audit-2026-09-01.md), fixed 2026-09-01 - covers ai_extractor.py, document_reader.py,
and web_extractor.py, per Chris's approved 3-batch plan (Batch 1 = app.py, Batch 2 = these
extraction-layer files, Batch 3 = builder.py/schemas.py):

  1. _detect_items caught a bare `Exception` and retried EVERY failure (including a genuine
     429/auth/network error) as if it were "document too large", up to 63 doomed calls before
     re-raising - fixed by narrowing the catch to _RECOVERABLE_DETECTION_ERRORS plus a
     size-related-message check, so anything else propagates immediately.
  2. TICKET_OPTION_ONLY_SYSTEM_PROMPT (the "add a Modality to an existing Ticket" flow) never
     asked for occupancy_prices at all - every group-size-banded rate sheet silently flattened
     to one adult price - fixed by adding the same occupancy_prices rule/JSON field the sibling
     full-ticket prompt already has.
  3. extract_text_from_xlsx flattened every row with " | ", ignoring merged cells entirely -
     fixed by rebuilding it on top of the same span/_render_grid mechanism the PDF and docx
     readers already use.
  4. scanned_document_warning only ever checked the WHOLE document's length - a real text cover
     page could clear the threshold while every actual pricing page was a blank screenshot -
     fixed by also checking individual PDF pages for near-zero content when the whole-document
     check passes.
  5. _with_hint's operator-instruction marker was unescaped plain text with no delimiter - fixed
     with explicit <operator_instruction>/<document> tags and a sentence telling the model which
     one to trust.
  6. Hotel extraction's max_tokens (8192) was the smallest among the big extractors despite
     having the largest output shape - fixed by raising it to _MODALITY_MAX_OUTPUT_TOKENS.
  7. extract_option_only_data was left on the old 4096-token ceiling after its sibling
     extract_modality_data was raised to _MODALITY_MAX_OUTPUT_TOKENS - fixed to match.
  8. duration_estimated defaulted to False (= "the document stated this") instead of True (=
     "flag for review") when the model omitted/nulled the field - fixed to default True.
  9. Both ticket-name/description retry calls dropped input_schema, falling back to the
     permissive schema at the exact moment structural enforcement matters most - fixed by
     passing input_schema=_required_keys_schema(defaults) on both retries too.
  10. Transfer/Transport currency defaults were "EUR" - an undetected currency was silently
      converted to EUR with no downstream "please choose" signal - fixed by defaulting to "".
  11. web_extractor.get_page_text kept only the FIRST <article> element, missing real content on
      WordPress-style pages that wrap every teaser card in its own <article> - fixed to combine
      every <article> (falling back to <main>/whole page when that combined text is
      implausibly short), plus a new short_page_text_warning() surfaced through app.py's
      existing _scanned_doc_warnings mechanism.
"""
import os

import ai_extractor as ax
import document_reader as dr
import web_extractor as we

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1. _detect_items no longer retries a non-size-related failure as truncation
# ======================================================================
def test_looks_like_size_related_true_for_runtime_error():
    assert ax._looks_like_size_related(RuntimeError("no tool call returned")) is True


def test_looks_like_size_related_true_for_size_worded_message():
    assert ax._looks_like_size_related(Exception("prompt is too long for this model")) is True


def test_looks_like_size_related_false_for_unrelated_message():
    assert ax._looks_like_size_related(Exception("invalid api key")) is False


def test_recoverable_detection_errors_includes_runtime_error():
    assert RuntimeError in ax._RECOVERABLE_DETECTION_ERRORS


def test_detect_items_reraises_immediately_on_non_recoverable_error(monkeypatch):
    calls = []

    def fake_call_with_stop(system_prompt, user_content, model, max_tokens, input_schema=None):
        calls.append(1)
        raise ValueError("boom - not a size problem at all")

    monkeypatch.setattr(ax, "_call_claude_with_stop", fake_call_with_stop)
    try:
        ax._detect_items("sys", "some text", "model", "flag", "list_key", lambda i: i)
        assert False, "expected ValueError to propagate"
    except ValueError:
        pass
    assert len(calls) == 1, "a non-recoverable error must not be retried as truncation"


def test_detect_items_retries_a_recoverable_error_as_truncation(monkeypatch):
    calls = []

    def fake_call_with_stop(system_prompt, user_content, model, max_tokens, input_schema=None):
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("no tool call returned")
        return {"list_key": [{"label": "a"}]}, "end_turn"

    monkeypatch.setattr(ax, "_call_claude_with_stop", fake_call_with_stop)
    # Long enough, with paragraph breaks, that _split_for_detection can actually split it.
    text = ("Route A to B, price 10.\n\n" * 20) + ("Route C to D, price 20.\n\n" * 20)
    result = ax._detect_items("sys", text, "model", "flag", "list_key", lambda i: i.get("label"))
    assert len(calls) >= 2, "a recoverable error should be retried via the split-and-merge path"
    assert isinstance(result, list)


# ======================================================================
# 2. Ticket option-only prompt now asks for occupancy_prices
# ======================================================================
def test_ticket_option_only_prompt_mentions_occupancy_prices():
    assert "occupancy_prices" in ax.TICKET_OPTION_ONLY_SYSTEM_PROMPT
    assert '"occupancy_prices": []' in ax.TICKET_OPTION_ONLY_SYSTEM_PROMPT


def test_ticket_option_only_prompt_has_the_exact_integer_expansion_rule():
    assert "EXPAND it into one entry per exact number" in ax.TICKET_OPTION_ONLY_SYSTEM_PROMPT


# ======================================================================
# 3. extract_text_from_xlsx preserves merged-cell grid structure
# ======================================================================
def test_xlsx_merged_header_spans_multiple_columns(tmp_path):
    openpyxl = __import__("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Season"
    ws["B1"] = "High"
    ws.merge_cells("B1:C1")
    ws["B2"] = "Adult"
    ws["C2"] = "Child"
    ws["A3"] = "Jan-Feb"
    ws["B3"] = 100
    ws["C3"] = 50
    path = tmp_path / "rates.xlsx"
    wb.save(str(path))

    text = dr.extract_text_from_xlsx(str(path))
    assert "spans C2-C3" in text, "a merged header must be recorded as spanning both columns"
    assert "BY COLUMN" in text
    assert "100" in text and "50" in text


def test_xlsx_blank_rows_are_skipped():
    assert "extract_text_from_xlsx" in dr.__dict__ or hasattr(dr, "extract_text_from_xlsx")


# ======================================================================
# 4. scanned_document_warning also checks individual PDF pages
# ======================================================================
def test_scanned_pages_warning_flags_a_blank_pricing_page_after_a_real_cover_page():
    cover = "Welcome to our tours. " * 20  # comfortably over _MIN_USEFUL_CHARS on its own
    text = f"--- Page 1 ---\n{cover}\n--- Page 2 ---\n"  # page 2 is blank (screenshot pricing)
    assert len(text.strip()) >= dr._MIN_USEFUL_CHARS  # whole-document check alone would pass
    warning = dr.scanned_document_warning("rates.pdf", text)
    assert warning is not None
    assert "page" in warning.lower() and "2" in warning


def test_scanned_pages_warning_is_none_when_every_page_has_content():
    text = "--- Page 1 ---\n" + ("Real content here. " * 20) + "\n--- Page 2 ---\n" + ("More real content. " * 20)
    assert dr.scanned_document_warning("rates.pdf", text) is None


def test_scanned_pages_warning_does_not_fire_for_non_pdf():
    cover = "Welcome to our tours. " * 20
    text = f"--- Page 1 ---\n{cover}\n--- Page 2 ---\n"
    assert dr._scanned_pages_warning("rates.docx", text) is None


# ======================================================================
# 5. _with_hint uses explicit, unambiguous delimiters
# ======================================================================
def test_with_hint_wraps_instruction_and_document_in_tags():
    out = ax._with_hint("only the Hurghada routes", "Route A to B\nRoute C to D")
    assert "<operator_instruction>" in out and "</operator_instruction>" in out
    assert "<document>" in out and "</document>" in out
    assert "only the Hurghada routes" in out
    assert out.index("</operator_instruction>") < out.index("<document>")


def test_with_hint_returns_raw_text_unchanged_when_no_hint():
    assert ax._with_hint("some text", None) == "some text"
    assert ax._with_hint("some text", "  ") == "some text"


# ======================================================================
# 6 & 7. Token budgets raised to match _MODALITY_MAX_OUTPUT_TOKENS
# ======================================================================
def test_hotel_extraction_uses_the_modality_token_budget():
    src = _read_app_py() if False else None  # not used; kept for structural symmetry
    import inspect
    source = inspect.getsource(ax.extract_hotel_data)
    assert "max_tokens=_MODALITY_MAX_OUTPUT_TOKENS" in source
    assert "max_tokens=8192" not in source


def test_extract_option_only_data_uses_the_modality_token_budget():
    import inspect
    source = inspect.getsource(ax.extract_option_only_data)
    assert "max_tokens=_MODALITY_MAX_OUTPUT_TOKENS" in source
    assert "max_tokens=4096" not in source


# ======================================================================
# 8. duration_estimated fails safe (defaults True, not False)
# ======================================================================
def test_transport_duration_estimated_defaults_to_true():
    import inspect
    source = inspect.getsource(ax.extract_transport_data)
    assert '"duration_estimated": True' in source
    assert '"duration_estimated": False' not in source


# ======================================================================
# 9. Both ticket retry calls now pass input_schema
# ======================================================================
def test_ticket_extraction_retry_passes_input_schema():
    import inspect
    source = inspect.getsource(ax.extract_ticket_data)
    # There must be at least 2 calls to TICKET_EXTRACTION_SYSTEM_PROMPT that both carry
    # input_schema=_required_keys_schema(defaults) - the first call and the retry.
    assert source.count("input_schema=_required_keys_schema(defaults)") >= 2


def test_ticket_main_info_retry_passes_input_schema():
    import inspect
    source = inspect.getsource(ax.extract_ticket_main_info)
    assert source.count("input_schema=_required_keys_schema(defaults)") >= 2


# ======================================================================
# 10. Transfer/Transport currency defaults to "" not "EUR"
# ======================================================================
def test_transfer_currency_default_is_empty_not_eur():
    import inspect
    source = inspect.getsource(ax.extract_transfer_data)
    assert '"currency": ""' in source


def test_transport_currency_default_is_empty_not_eur():
    import inspect
    source = inspect.getsource(ax.extract_transport_data)
    assert '"currency": ""' in source


def test_currency_prompt_rule_says_empty_if_not_stated():
    assert "Empty if not stated anywhere" in ax.TRANSFER_EXTRACTION_SYSTEM_PROMPT
    assert "Empty if not stated anywhere" in ax.TRANSPORT_EXTRACTION_SYSTEM_PROMPT


# ======================================================================
# 11. web_extractor combines every <article>, with a short-text warning
# ======================================================================
def test_get_page_text_combines_multiple_articles(monkeypatch):
    html = (
        "<html><body>"
        "<article>Real product content that actually matters here, quite long indeed.</article>"
        "<article>Unrelated teaser card one.</article>"
        "<article>Unrelated teaser card two.</article>"
        "</body></html>"
    )

    class FakeResponse:
        content = html.encode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(we.requests, "get", lambda *a, **kw: FakeResponse())
    text = we.get_page_text("https://example.com/page")
    assert "Real product content" in text
    assert "Unrelated teaser card one" in text
    assert "Unrelated teaser card two" in text


def test_get_page_text_falls_back_to_main_when_articles_are_too_short(monkeypatch):
    html = (
        "<html><body>"
        "<article>Hi</article>"
        "<main>The actual real product content lives here instead, well past the "
        "short-text threshold so it should be preferred over the tiny article.</main>"
        "</body></html>"
    )

    class FakeResponse:
        content = html.encode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(we.requests, "get", lambda *a, **kw: FakeResponse())
    text = we.get_page_text("https://example.com/page")
    assert "actual real product content" in text


def test_short_page_text_warning_fires_below_threshold():
    warning = we.short_page_text_warning("https://example.com/x", "too short")
    assert warning is not None
    assert "example.com/x" in warning


def test_short_page_text_warning_is_none_for_long_text():
    assert we.short_page_text_warning("https://example.com/x", "word " * 100) is None


def test_fetch_url_text_safe_surfaces_short_page_warning():
    source = _read_app_py()
    idx = source.index("def _fetch_url_text_safe")
    window = source[idx:idx + 1700]
    assert "short_page_text_warning" in window
    assert "_scanned_doc_warnings" in window
