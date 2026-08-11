"""
Extracts raw text content from PDF, Word (.docx), and Excel (.xlsx) files.

This is deliberately dumb/mechanical - it just gets everything readable
out of the file as plain text. The actual understanding (what's the tour
name, what's included, what are the destinations, etc.) happens in the
next step (ai_extractor.py), which is much better suited to messy,
inconsistent supplier documents than hand-written parsing rules.

TABLE STRUCTURE IS NOT DECORATION. A rate sheet carries its meaning in the GRID: which price
sits under which season, which header spans which columns. Flattening a row to "a | b | c"
throws that away, and no amount of prompting recovers it afterwards.

CONFIRMED REAL FAILURE (product owner, Le Fayan Fleet 2026/27): "the document reader is doing
very difficult to read those styles of documents... after 10 times trying and even with TELL AI
I was unable to publish that." That rate table has a season header spanning two columns, three
stacked date ranges under one season, From/To sub-headers on different rows for different
seasons, and - worst of all - Word stores the prices as a SEPARATE table with no header row at
all. The old renderer emitted a merged cell once per column it spanned, so "Season 2026 / 2027"
appeared eight times and every price appeared twice, and the header table and the price table
arrived as two unrelated blocks. The model had no way to know which $565 belonged to which
season - and no way to say so either, so it simply produced something wrong.

This module now preserves three things a model cannot reconstruct on its own:
  - WHICH COLUMNS a merged cell spans, written explicitly instead of by repetition
  - a COLUMN RULER, so any cell can be referred to by position
  - whether a table is a CONTINUATION of the one above it, sharing its headers
"""
import os

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-12-table-grid"

_EMPTY_CELL = "·"          # visible placeholder, so a blank column is not silently swallowed


def _render_grid(rows, table_label, previous_column_count=None):
    """Render one table as text that keeps its grid.

    `rows` is a list of rows, each a list of (text, start_col, end_col) spans - one entry per
    DISTINCT cell, carrying the columns it covers, rather than one entry per column.

    Returns (lines, column_count)."""
    if not rows:
        return [], 0

    width = max((span[2] + 1) for row in rows for span in row) if any(rows) else 0
    lines = [f"[{table_label}]",
             "COLUMNS: " + " | ".join(f"C{i + 1}" for i in range(width))]

    # A table whose first row is entirely empty and which matches the previous table's width is
    # almost certainly a continuation: in Word, one visual table is often stored as two, and the
    # second half then has no headers at all.
    first_row_blank = all(not (span[0] or "").strip() for span in rows[0])
    if first_row_blank and previous_column_count == width and width > 1:
        lines.append(
            f"NOTE: this table has NO header row of its own, and has the same number of columns "
            f"as the table immediately above it. Its columns line up with that table's headers "
            f"one for one - read C1..C{width} there to know what each column here means.")

    for row_index, row in enumerate(rows, start=1):
        cells = []
        for text, start, end in row:
            text = (text or "").strip() or _EMPTY_CELL
            if end > start:
                cells.append(f"{text} «spans C{start + 1}-C{end + 1}»")
            else:
                cells.append(f"{text} «C{start + 1}»")
        lines.append(f"R{row_index}: " + " | ".join(cells))
    lines.append(f"[/{table_label}]")
    return lines, width


def _docx_row_spans(row):
    """Distinct cells in a Word table row, with the columns each one covers.

    python-docx returns the SAME underlying <w:tc> element once per column a merged cell covers,
    so identity is what reveals the merge. Comparing text instead would wrongly merge two
    genuinely separate columns that happen to hold the same number - and on a rate sheet, two
    seasons at the same price is completely ordinary."""
    spans = []
    previous_tc = None
    for index, cell in enumerate(row.cells):
        tc = cell._tc
        if tc is previous_tc and spans:
            spans[-1][2] = index
        else:
            spans.append([cell.text.strip(), index, index])
            previous_tc = tc
    return [tuple(s) for s in spans]


def extract_text_from_pdf(file_path: str) -> str:
    import pdfplumber

    parts = []
    previous_pdf_width = None
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            parts.append(f"--- Page {page_num} ---\n{text}")

            for table_index, table in enumerate(page.extract_tables(), start=1):
                # pdfplumber gives a merged cell's text once and None for the columns it covers,
                # so a run of Nones after a value IS the span. Reconstructing it here means a PDF
                # rate sheet reads the same way a Word one does.
                rows = []
                for raw_row in table:
                    spans, last = [], None
                    for index, cell in enumerate(raw_row):
                        text = (cell or "").strip()
                        if text:
                            spans.append([text, index, index])
                            last = spans[-1]
                        elif last is not None and cell is None:
                            last[2] = index
                        else:
                            spans.append(["", index, index])
                            last = None
                    rows.append([tuple(s) for s in spans])
                lines, width = _render_grid(rows, f"TABLE p{page_num}.{table_index}",
                                            previous_pdf_width)
                parts.extend(lines)
                previous_pdf_width = width

    return "\n".join(parts)


def extract_text_from_docx(file_path: str) -> str:
    import docx

    doc = docx.Document(file_path)
    parts = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    previous_width = None
    for table_num, table in enumerate(doc.tables, start=1):
        try:
            rows = [_docx_row_spans(row) for row in table.rows]
        except Exception:
            # A table python-docx cannot walk (odd nesting, corrupt merge) must not lose the
            # whole document - fall back to the flat rendering for that one table.
            parts.append(f"[TABLE {table_num}]")
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            parts.append(f"[/TABLE {table_num}]")
            continue
        lines, width = _render_grid(rows, f"TABLE {table_num}", previous_width)
        parts.extend(lines)
        previous_width = width

    return "\n".join(parts)


def extract_text_from_xlsx(file_path: str) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    parts = []

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        parts.append(f"--- Sheet: {sheet_name} ---")
        for row in sheet.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                parts.append(" | ".join(str(cell) if cell is not None else "" for cell in row))

    return "\n".join(parts)


_MIN_USEFUL_CHARS = 200


def scanned_document_warning(file_path: str, text: str):
    """A plain-English warning when a file yielded almost no readable text, else None.

    CONFIRMED REAL CASE (product owner): a rate table sent as a SCREENSHOT saved to PDF. The
    whole page is one image, so text extraction returns 15 characters - and the app carried on
    as though it had read the document. Everything downstream then works from nothing: detection
    finds no products, or extraction fills a screen with plausible blanks, and the only clue is
    that the answers are wrong in a way that looks like the AI being stupid rather than the AI
    being handed an empty page.

    Cheap to detect, and impossible to diagnose from the symptoms - so it is said out loud."""
    if len((text or "").strip()) >= _MIN_USEFUL_CHARS:
        return None
    name = os.path.basename(file_path or "this file")
    extension = os.path.splitext(file_path or "")[1].lower()
    if extension == ".pdf":
        return (f"**{name} contains almost no readable text** ({len((text or '').strip())} "
                f"characters). It is almost certainly a scan or a screenshot - a picture of a "
                f"table rather than a table. Nothing can be extracted from it reliably. Please "
                f"upload the original Word or Excel file, or a PDF exported from it, rather than "
                f"a photographed or screenshotted page.")
    return (f"**{name} contains almost no readable text** ({len((text or '').strip())} "
            f"characters). If the content is inside images, the app cannot read it - please "
            f"send the underlying table instead.")


def extract_raw_text(file_path: str) -> str:
    """
    Dispatches to the right extractor based on file extension.
    Raises ValueError with a clear message for unsupported formats
    (e.g. legacy .doc / .xls - these need converting to .docx / .xlsx first).
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".xlsx":
        return extract_text_from_xlsx(file_path)
    elif ext in (".doc", ".xls"):
        raise ValueError(
            f"Legacy '{ext}' format isn't supported directly. "
            f"Please re-save/export the file as '.docx' or '.xlsx' first."
        )
    else:
        raise ValueError(f"Unsupported file type: '{ext}'. Supported: .pdf, .docx, .xlsx")


import hashlib


def extract_images_from_pdf(file_path: str, max_images: int = 12, seen_hashes: set = None) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in a PDF.
    Skips duplicate images (e.g. a logo repeated on every page) via content hash."""
    import fitz  # PyMuPDF

    if seen_hashes is None:
        seen_hashes = set()
    images = []
    doc = fitz.open(file_path)
    try:
        for page_index in range(len(doc)):
            if len(images) >= max_images:
                break
            page = doc[page_index]
            for img in page.get_images(full=True):
                if len(images) >= max_images:
                    break
                xref = img[0]
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]
                img_hash = hashlib.sha256(img_bytes).hexdigest()
                if img_hash in seen_hashes:
                    continue
                seen_hashes.add(img_hash)
                images.append((img_bytes, base_image["ext"]))
    finally:
        doc.close()
    return images


def extract_images_from_docx(file_path: str, max_images: int = 12, seen_hashes: set = None) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in a Word doc.
    Skips duplicate images via content hash."""
    import docx

    if seen_hashes is None:
        seen_hashes = set()
    doc = docx.Document(file_path)
    images = []
    for rel in doc.part.rels.values():
        if len(images) >= max_images:
            break
        if "image" in rel.reltype:
            img_bytes = rel.target_part.blob
            img_hash = hashlib.sha256(img_bytes).hexdigest()
            if img_hash in seen_hashes:
                continue
            seen_hashes.add(img_hash)
            ext = rel.target_ref.rsplit(".", 1)[-1] if "." in rel.target_ref else "png"
            images.append((img_bytes, ext))
    return images


def extract_images_from_xlsx(file_path: str, max_images: int = 12, seen_hashes: set = None) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in an Excel file.
    Skips duplicate images via content hash."""
    import openpyxl

    if seen_hashes is None:
        seen_hashes = set()
    wb = openpyxl.load_workbook(file_path)
    images = []
    for ws in wb.worksheets:
        if len(images) >= max_images:
            break
        for img in getattr(ws, "_images", []):
            if len(images) >= max_images:
                break
            try:
                data = img._data()
                img_hash = hashlib.sha256(data).hexdigest()
                if img_hash in seen_hashes:
                    continue
                seen_hashes.add(img_hash)
                images.append((data, "png"))
            except Exception:
                continue
    return images


def extract_images(file_path: str, max_images: int = 12, seen_hashes: set = None) -> list:
    """
    Dispatches to the right image extractor based on file extension.
    Returns list of (image_bytes, extension) tuples, or an empty list if
    the format isn't supported for image extraction or none are found.
    seen_hashes: an optional shared set of content hashes to dedupe against
    - pass the SAME set across multiple calls (e.g. multiple uploaded
    documents in one session) to dedupe across files too, not just within one.
    """
    if seen_hashes is None:
        seen_hashes = set()
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            return extract_images_from_pdf(file_path, max_images, seen_hashes)
        elif ext == ".docx":
            return extract_images_from_docx(file_path, max_images, seen_hashes)
        elif ext == ".xlsx":
            return extract_images_from_xlsx(file_path, max_images, seen_hashes)
    except Exception as e:
        print(f"⚠️ Image extraction failed for {file_path}: {e}")
    return []
