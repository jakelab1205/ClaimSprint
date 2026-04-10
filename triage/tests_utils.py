import io
from unittest.mock import MagicMock, patch

from django.test import TestCase

from .input_utils import form_data_to_fnol_text
from .pdf_utils import PDFExtractionError, extract_text_from_pdf


class FormDataToFnolTextTests(TestCase):
    def test_header_always_present(self):
        result = form_data_to_fnol_text({})
        self.assertIn("FIRST NOTICE OF LOSS", result)

    def test_all_fields_present(self):
        data = {
            "incident_date": "2025-03-01",
            "incident_time": "14:30",
            "claimant_name": "Acme Shipping",
            "policy_number": "POL-12345",
            "loss_type": "marine_cargo",
            "incident_location": "Port of Rotterdam",
            "estimated_value": "150000",
            "description": "Container fell overboard during loading.",
        }
        result = form_data_to_fnol_text(data)
        self.assertIn("Incident date: 2025-03-01", result)
        self.assertIn("Incident time: 14:30", result)
        self.assertIn("Claimant name: Acme Shipping", result)
        self.assertIn("Policy number: POL-12345", result)
        self.assertIn("Type of loss: marine cargo", result)
        self.assertIn("Incident location: Port of Rotterdam", result)
        self.assertIn("Estimated value: 150000", result)
        self.assertIn("Description of loss: Container fell overboard during loading.", result)

    def test_empty_fields_omitted(self):
        data = {"description": "Hull damage from collision.", "claimant_name": ""}
        result = form_data_to_fnol_text(data)
        self.assertIn("Description of loss:", result)
        self.assertNotIn("Claimant name:", result)

    def test_all_empty_returns_header_only(self):
        result = form_data_to_fnol_text({})
        lines = [l for l in result.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("FIRST NOTICE OF LOSS", lines[0])

    def test_none_values_omitted(self):
        data = {"claimant_name": None, "description": "Cargo spoilage."}
        result = form_data_to_fnol_text(data)
        self.assertNotIn("Claimant name:", result)
        self.assertIn("Description of loss:", result)


class ExtractTextFromPdfTests(TestCase):
    def test_raises_on_unreadable_file(self):
        with self.assertRaises(PDFExtractionError):
            extract_text_from_pdf(io.BytesIO(b"not a pdf"))

    def test_raises_on_empty_text_pdf(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        with patch("triage.pdf_utils.PdfReader", return_value=mock_reader):
            with self.assertRaises(PDFExtractionError) as cm:
                extract_text_from_pdf(io.BytesIO(b"fake"))
        self.assertIn("No extractable text", str(cm.exception))

    def test_returns_concatenated_page_text(self):
        page1 = MagicMock()
        page1.extract_text.return_value = "Page one content."
        page2 = MagicMock()
        page2.extract_text.return_value = "Page two content."
        mock_reader = MagicMock()
        mock_reader.pages = [page1, page2]
        with patch("triage.pdf_utils.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf(io.BytesIO(b"fake"))
        self.assertIn("Page one content.", result)
        self.assertIn("Page two content.", result)

    def test_none_page_text_treated_as_empty(self):
        page1 = MagicMock()
        page1.extract_text.return_value = None
        page2 = MagicMock()
        page2.extract_text.return_value = "Real text here."
        mock_reader = MagicMock()
        mock_reader.pages = [page1, page2]
        with patch("triage.pdf_utils.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf(io.BytesIO(b"fake"))
        self.assertIn("Real text here.", result)
