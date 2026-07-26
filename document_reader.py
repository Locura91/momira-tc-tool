"""
Extracts raw text content from PDF, Word (.docx), and Excel (.xlsx) files.

This is deliberately dumb/mechanical - it just gets everything readable
out of the file as plain text. The actual understanding (what's the tour
name, what's included, what are the destinations, etc.) happens in the
next step (ai_extractor.py), which is much better suited to messy,
inconsistent supplier documents than hand-written parsing rules.
"""
import os


def extract_text_from_pdf(file_path: str) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            parts.append(f"--- Page {page_num} ---\n{text}")

            for table in page.extract_tables():
                parts.append("[TABLE]")
                for row in table:
                    parts.append(" | ".join(cell or "" for cell in row))
                parts.append("[/TABLE]")

    return "\n".join(parts)


def extract_text_from_docx(file_path: str) -> str:
    import docx

    doc = docx.Document(file_path)
    parts = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    for table_num, table in enumerate(doc.tables, start=1):
        parts.append(f"[TABLE {table_num}]")
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        parts.append("[/TABLE]")

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
