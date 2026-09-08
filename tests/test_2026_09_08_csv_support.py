"""Tests for CSV support (product owner, 2026-09-08): "can the document reader read CSV files?
If not, can we include it."

Before this, document_reader.extract_raw_text raised "Unsupported file type" for a .csv - the
exact same kind of grid a supplier already sends as .xlsx, just saved with a different
extension. extract_text_from_csv reuses the SAME grid-rendering primitive (_render_grid) as
xlsx/docx/pdf, so a CSV rate sheet gets the identical COLUMNS ruler + BY COLUMN treatment every
other table format already gets - no separate, weaker code path for this format.

Delimiter and encoding are sniffed rather than assumed, since a supplier's CSV export is just as
likely to be semicolon-delimited (a European Excel locale) or comma-delimited, and just as
likely to be Windows-1252/latin-1 as UTF-8.
"""
import os
import tempfile

import document_reader as dr


def _write_csv(content: str, encoding: str = "utf-8") -> str:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
        f.write(content.encode(encoding))
        return f.name


def test_extract_raw_text_dispatches_csv_instead_of_raising():
    path = _write_csv("Code,Name,Price\nCAI-01,Pyramids Tour,45\n")
    try:
        text = dr.extract_raw_text(path)
    finally:
        os.remove(path)
    assert "CAI-01" in text
    assert "Pyramids Tour" in text
    assert "45" in text


def test_csv_grid_carries_column_headers_the_same_way_xlsx_does():
    path = _write_csv("Code,Name,1-2 pax,3-4 pax\nCAI-01,Pyramids Tour,45,40\n")
    try:
        text = dr.extract_text_from_csv(path)
    finally:
        os.remove(path)
    assert "[CSV]" in text and "[/CSV]" in text
    assert "COLUMNS: C1 | C2 | C3 | C4" in text
    # BY COLUMN view - the same "match a value to its heading" mechanism xlsx/docx tables get.
    assert "BY COLUMN" in text
    assert "3-4 pax" in text and "40" in text


def test_semicolon_delimited_csv_is_read_correctly():
    # CONFIRMED REAL CASE (European Excel locale export uses ';' as the field separator, not
    # ',' - a supplier CSV is just as likely to arrive this way).
    path = _write_csv("Code;Name;Price\nLXR-01;Karnak Temple;50\n")
    try:
        text = dr.extract_text_from_csv(path)
    finally:
        os.remove(path)
    assert "LXR-01" in text
    assert "Karnak Temple" in text
    assert "50" in text
    # If the sniffer had guessed comma instead, the whole row would land in one cell.
    assert "LXR-01;Karnak" not in text


def test_latin1_encoded_csv_does_not_crash():
    # A supplier's regional Excel export is routinely Windows-1252/latin-1, not UTF-8.
    path = _write_csv("Code,Name,Price\nASW-01,Château Tour,60\n", encoding="latin-1")
    try:
        text = dr.extract_text_from_csv(path)
    finally:
        os.remove(path)
    assert "ASW-01" in text
    assert "60" in text


def test_blank_rows_are_skipped():
    path = _write_csv("Code,Name\nCAI-01,Pyramids\n\n\nCAI-02,Museum\n")
    try:
        text = dr.extract_text_from_csv(path)
    finally:
        os.remove(path)
    assert "CAI-01" in text and "CAI-02" in text
    # Only 3 real rows (header + 2 data rows) should be numbered - no blank R3/R4.
    assert "R4:" not in text


def test_empty_csv_returns_empty_string_not_an_error():
    path = _write_csv("")
    try:
        text = dr.extract_text_from_csv(path)
    finally:
        os.remove(path)
    assert text == ""


def test_extract_images_from_csv_returns_empty_list_not_an_error():
    # CSVs never carry embedded images - this must fall through extract_images' extension
    # dispatch silently (like .doc/.xls/.ppt already do), not raise or log a false "failed" error.
    path = _write_csv("Code,Name\nCAI-01,Pyramids\n")
    try:
        errors = []
        result = dr.extract_images(path, errors=errors)
    finally:
        os.remove(path)
    assert result == []
    assert errors == []


def test_unsupported_type_error_message_now_mentions_csv():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello")
        path = f.name
    try:
        try:
            dr.extract_raw_text(path)
            assert False, "expected ValueError for an unsupported extension"
        except ValueError as e:
            assert ".csv" in str(e)
    finally:
        os.remove(path)
