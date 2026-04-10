from pypdf import PdfReader


class PDFExtractionError(ValueError):
    pass


def extract_text_from_pdf(file_obj) -> str:
    try:
        reader = PdfReader(file_obj)
    except Exception as exc:
        raise PDFExtractionError(f"Could not read PDF: {exc}") from exc

    pages_text = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages_text.append(text)

    full_text = "\n".join(pages_text).strip()
    if not full_text:
        raise PDFExtractionError(
            "No extractable text found in the PDF. "
            "This may be a scanned image — please paste the text manually."
        )
    return full_text
