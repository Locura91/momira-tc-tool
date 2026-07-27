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


def extract_images_from_pdf(file_path: str, max_images: int = 5) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in a PDF."""
    import fitz  # PyMuPDF

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
                images.append((base_image["image"], base_image["ext"]))
    finally:
        doc.close()
    return images


def extract_images_from_docx(file_path: str, max_images: int = 5) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in a Word doc."""
    import docx

    doc = docx.Document(file_path)
    images = []
    for rel in doc.part.rels.values():
        if len(images) >= max_images:
            break
        if "image" in rel.reltype:
            ext = rel.target_ref.rsplit(".", 1)[-1] if "." in rel.target_ref else "png"
            images.append((rel.target_part.blob, ext))
    return images


def extract_images_from_xlsx(file_path: str, max_images: int = 5) -> list:
    """Returns list of (image_bytes, extension) tuples for embedded images in an Excel file."""
    import openpyxl

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
                images.append((data, "png"))
            except Exception:
                continue
    return images


def extract_images(file_path: str, max_images: int = 5) -> list:
    """
    Dispatches to the right image extractor based on file extension.
    Returns list of (image_bytes, extension) tuples, or an empty list if
    the format isn't supported for image extraction or none are found.
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            return extract_images_from_pdf(file_path, max_images)
        elif ext == ".docx":
            return extract_images_from_docx(file_path, max_images)
        elif ext == ".xlsx":
            return extract_images_from_xlsx(file_path, max_images)
    except Exception as e:
        print(f"⚠️ Image extraction failed for {file_path}: {e}")
    return []
