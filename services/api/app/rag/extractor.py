"""Text extraction utilities for supported document types.

Supported file types: pdf, docx, txt.

All functions are pure (no I/O side effects) so they can be called synchronously
inside an async endpoint without blocking the event loop on network I/O. File
parsing is CPU-bound but fast for the document sizes we expect at hackathon scale.
"""

import io


def extract_text(file_bytes: bytes, file_type: str) -> str:
    """Extract plain text from the raw bytes of a document file.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        file_type: Lowercase extension without the dot — one of "pdf", "docx", "txt".

    Returns:
        Extracted text as a single string. Pages / paragraphs joined with newlines.

    Raises:
        ValueError: If file_type is not one of the supported types.
        RuntimeError: If the file cannot be parsed (propagated from pdfplumber / python-docx).
    """
    if file_type == "pdf":
        return _extract_pdf(file_bytes)
    if file_type == "docx":
        return _extract_docx(file_bytes)
    if file_type == "txt":
        return _extract_txt(file_bytes)
    raise ValueError(f"Unsupported file type: {file_type!r}. Expected one of: pdf, docx, txt.")


def _extract_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF using pdfplumber.

    Args:
        file_bytes: Raw PDF bytes.

    Returns:
        Text from all pages joined with newlines.
    """
    import pdfplumber  # local import — only needed when processing PDFs

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    return "\n".join(pages)


def _extract_docx(file_bytes: bytes) -> str:
    """Extract text from a DOCX file using python-docx.

    Args:
        file_bytes: Raw DOCX bytes.

    Returns:
        All paragraph texts joined with newlines.
    """
    from docx import Document  # local import — only needed when processing DOCX files

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n".join(paragraphs)


def _extract_txt(file_bytes: bytes) -> str:
    """Decode plain-text file bytes as UTF-8.

    Args:
        file_bytes: Raw text file bytes.

    Returns:
        Decoded string content.
    """
    return file_bytes.decode("utf-8")
