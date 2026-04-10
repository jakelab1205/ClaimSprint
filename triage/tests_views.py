import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from triage.forms import FnolForm, SeverityThresholdForm
from triage.models import ClaimType, Handler, SeverityThreshold
from triage.pdf_utils import PDFExtractionError


GOOD_RESULT = {
    "claim_type": "marine cargo",
    "claim_subtype": "refrigerated cargo loss",
    "severity": "High",
    "severity_factors": ["Cold chain break", "High value cargo", "Carrier dispute"],
    "recommended_action": "assign_to_handler",
    "action_reasoning": ["Dispatch surveyor", "Request bill of lading"],
    "recommended_handler": {
        "name": "Alice",
        "role": "Senior Handler",
        "region": "EMEA",
        "speciality": "marine cargo refrigerated",
        "reason": "Region match.",
    },
    "coverage_flags": [],
    "confidence_score": 0.9,
    "reasoning_chain": ["step 1", "step 2", "step 3", "step 4"],
    "risk_flag_explanations": [],
}

ERROR_RESULT = {
    "error": True,
    "message": "Input does not appear to be a First Notice of Loss.",
}


# ---------------------------------------------------------------------------
# index view
# ---------------------------------------------------------------------------

class IndexViewTests(TestCase):

    def test_get_returns_200(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/index.html")

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_post_calls_process_fnol_and_renders_result(self, mock_process):
        response = self.client.post("/", {"fnol_text": "Shipment damaged in transit"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/result.html")
        mock_process.assert_called_once_with("Shipment damaged in transit")
        self.assertEqual(response.context["result"], GOOD_RESULT)
        self.assertEqual(response.context["fnol_text"], "Shipment damaged in transit")

    @patch("triage.views.process_fnol", return_value=ERROR_RESULT)
    def test_post_empty_text_returns_error_result(self, mock_process):
        response = self.client.post("/", {"fnol_text": ""})
        self.assertEqual(response.status_code, 200)
        mock_process.assert_called_once_with("")
        self.assertTrue(response.context["result"]["error"])

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_post_passes_fnol_text_to_context(self, _):
        response = self.client.post("/", {"fnol_text": "test claim"})
        self.assertEqual(response.context["fnol_text"], "test claim")


# ---------------------------------------------------------------------------
# triage_stream view
# ---------------------------------------------------------------------------

class TriageStreamViewTests(TestCase):

    def test_get_returns_405(self):
        response = self.client.get("/stream/")
        self.assertEqual(response.status_code, 405)

    @patch("triage.views.stream_fnol")
    def test_post_returns_event_stream_content_type(self, mock_stream):
        mock_stream.return_value = iter([])
        response = self.client.post("/stream/", {"fnol_text": "claim text"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

    @patch("triage.views.stream_fnol")
    def test_token_chunks_formatted_as_sse(self, mock_stream):
        mock_stream.return_value = iter(["Hello", " world"])
        response = self.client.post("/stream/", {"fnol_text": "claim"})
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "token"', content)
        self.assertIn('"text": "Hello"', content)

    @patch("triage.views.stream_fnol")
    def test_final_dict_formatted_as_done_event(self, mock_stream):
        mock_stream.return_value = iter([GOOD_RESULT])
        response = self.client.post("/stream/", {"fnol_text": "claim"})
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "done"', content)
        self.assertIn('"html"', content)

    @patch("triage.views.stream_fnol")
    def test_error_dict_formatted_as_error_event(self, mock_stream):
        mock_stream.return_value = iter([{"error": True, "message": "Bad input"}])
        response = self.client.post("/stream/", {"fnol_text": "hi"})
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "error"', content)
        self.assertIn('"message": "Bad input"', content)

    @patch("triage.views.stream_fnol")
    def test_no_cache_headers_set(self, mock_stream):
        mock_stream.return_value = iter([])
        response = self.client.post("/stream/", {"fnol_text": "claim"})
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["X-Accel-Buffering"], "no")


# ---------------------------------------------------------------------------
# history view
# ---------------------------------------------------------------------------

class HistoryViewTests(TestCase):

    def test_post_returns_405(self):
        response = self.client.post("/history/")
        self.assertEqual(response.status_code, 405)

    @patch("triage.views.os.path.exists", return_value=False)
    def test_get_no_log_file_returns_empty_entries(self, _):
        response = self.client.get("/history/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["entries"], [])

    def test_get_with_log_file_returns_entries_reversed(self):
        entry1 = {"claim_type": "marine cargo", "severity": "High"}
        entry2 = {"claim_type": "liability", "severity": "Low"}
        log_content = json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(log_content)
            tmp_path = f.name

        try:
            with patch("triage.views._LOG_FILE", tmp_path):
                response = self.client.get("/history/")
            self.assertEqual(response.status_code, 200)
            entries = response.context["entries"]
            self.assertEqual(len(entries), 2)
            # reversed: entry2 first, then entry1
            self.assertEqual(entries[0]["claim_type"], "liability")
            self.assertEqual(entries[1]["claim_type"], "marine cargo")
        finally:
            os.unlink(tmp_path)

    def test_get_log_with_blank_lines_skipped(self):
        entry = {"claim_type": "hull", "severity": "Medium"}
        log_content = "\n" + json.dumps(entry) + "\n\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(log_content)
            tmp_path = f.name

        try:
            with patch("triage.views._LOG_FILE", tmp_path):
                response = self.client.get("/history/")
            entries = response.context["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["claim_type"], "hull")
        finally:
            os.unlink(tmp_path)

    def test_get_log_with_invalid_json_lines_skipped(self):
        entry = {"claim_type": "hull"}
        log_content = "not valid json\n" + json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(log_content)
            tmp_path = f.name

        try:
            with patch("triage.views._LOG_FILE", tmp_path):
                response = self.client.get("/history/")
            entries = response.context["entries"]
            self.assertEqual(len(entries), 1)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# staff_directory view
# ---------------------------------------------------------------------------

class StaffDirectoryViewTests(TestCase):

    def test_get_no_handlers_returns_empty_regions(self):
        response = self.client.get("/staff/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["regions"], [])

    def test_post_returns_405(self):
        response = self.client.post("/staff/")
        self.assertEqual(response.status_code, 405)

    def test_get_groups_handlers_by_region(self):
        Handler.objects.create(name="Alice", role="Handler", region="EMEA", speciality="marine cargo")
        Handler.objects.create(name="Bob", role="Handler", region="APAC", speciality="hull")
        response = self.client.get("/staff/")
        regions = dict(response.context["regions"])
        self.assertIn("EMEA", regions)
        self.assertIn("APAC", regions)
        self.assertEqual(len(regions["EMEA"]), 1)
        self.assertEqual(regions["EMEA"][0].name, "Alice")

    def test_get_region_order_emea_before_apac(self):
        Handler.objects.create(name="Bob", role="Handler", region="APAC", speciality="hull")
        Handler.objects.create(name="Alice", role="Handler", region="EMEA", speciality="marine cargo")
        response = self.client.get("/staff/")
        region_names = [r for r, _ in response.context["regions"]]
        self.assertLess(region_names.index("EMEA"), region_names.index("APAC"))

    def test_get_inactive_handlers_excluded(self):
        Handler.objects.create(name="Active", role="Handler", region="EMEA", speciality="marine cargo", active=True)
        Handler.objects.create(name="Inactive", role="Handler", region="EMEA", speciality="hull", active=False)
        response = self.client.get("/staff/")
        all_handlers = [h for _, hs in response.context["regions"] for h in hs]
        names = [h.name for h in all_handlers]
        self.assertIn("Active", names)
        self.assertNotIn("Inactive", names)


# ---------------------------------------------------------------------------
# settings_view
# ---------------------------------------------------------------------------

class SettingsViewTests(TestCase):

    def setUp(self):
        for claim_type, low, med, desc in [
            ("marine_cargo", 50000, 250000, "Total loss >EUR 1M"),
            ("liability", 25000, 150000, "Multi-party or regulatory action"),
            ("hull", 100000, 500000, "CTL or structural failure"),
        ]:
            SeverityThreshold.objects.update_or_create(
                claim_type=claim_type,
                defaults={"low_max": low, "medium_max": med, "critical_description": desc},
            )

    def test_get_returns_200_with_formset(self):
        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/settings.html")
        self.assertIn("formset", response.context)

    def test_get_saved_flag_false_by_default(self):
        response = self.client.get("/settings/")
        self.assertFalse(response.context["saved"])

    def test_get_saved_flag_true_when_querystring(self):
        response = self.client.get("/settings/?saved=1")
        self.assertTrue(response.context["saved"])

    def test_post_valid_data_redirects(self):
        qs = SeverityThreshold.objects.filter(
            claim_type__in=["marine_cargo", "liability", "hull"]
        ).order_by("claim_type")

        post_data = {"form-TOTAL_FORMS": "3", "form-INITIAL_FORMS": "3", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000"}
        for i, obj in enumerate(qs):
            post_data[f"form-{i}-id"] = str(obj.pk)
            post_data[f"form-{i}-low_max"] = str(int(obj.low_max))
            post_data[f"form-{i}-medium_max"] = str(int(obj.medium_max))
            post_data[f"form-{i}-critical_description"] = obj.critical_description

        response = self.client.post("/settings/", post_data)
        self.assertRedirects(response, "/settings/?saved=1")

    def test_post_invalid_data_rerenders_with_errors(self):
        qs = SeverityThreshold.objects.filter(
            claim_type__in=["marine_cargo", "liability", "hull"]
        ).order_by("claim_type")

        post_data = {"form-TOTAL_FORMS": "3", "form-INITIAL_FORMS": "3", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000"}
        for i, obj in enumerate(qs):
            post_data[f"form-{i}-id"] = str(obj.pk)
            # Make low_max > medium_max to trigger validation error
            post_data[f"form-{i}-low_max"] = "999999"
            post_data[f"form-{i}-medium_max"] = "1"
            post_data[f"form-{i}-critical_description"] = obj.critical_description

        response = self.client.post("/settings/", post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["formset"].is_valid())


# ---------------------------------------------------------------------------
# SeverityThresholdForm
# ---------------------------------------------------------------------------

class SeverityThresholdFormTests(TestCase):

    def setUp(self):
        self.threshold, _ = SeverityThreshold.objects.update_or_create(
            claim_type="marine_cargo",
            defaults={
                "low_max": 50000,
                "medium_max": 250000,
                "critical_description": "Total loss >EUR 1M",
            },
        )

    def _form(self, low, med, desc="Total loss >EUR 1M"):
        return SeverityThresholdForm(
            data={"low_max": low, "medium_max": med, "critical_description": desc},
            instance=self.threshold,
        )

    def test_valid_when_low_less_than_medium(self):
        form = self._form(10000, 50000)
        self.assertTrue(form.is_valid())

    def test_invalid_when_low_equals_medium(self):
        form = self._form(50000, 50000)
        self.assertFalse(form.is_valid())
        self.assertIn("Low max must be less than Medium max", str(form.errors))

    def test_invalid_when_low_greater_than_medium(self):
        form = self._form(100000, 50000)
        self.assertFalse(form.is_valid())
        self.assertIn("Low max must be less than Medium max", str(form.errors))

    def test_invalid_when_low_max_missing(self):
        form = SeverityThresholdForm(
            data={"medium_max": 50000, "critical_description": "desc"},
            instance=self.threshold,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("low_max", form.errors)

    def test_invalid_when_medium_max_missing(self):
        form = SeverityThresholdForm(
            data={"low_max": 10000, "critical_description": "desc"},
            instance=self.threshold,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("medium_max", form.errors)

    def test_valid_form_saves_to_db(self):
        form = self._form(20000, 100000, "Updated desc")
        self.assertTrue(form.is_valid())
        form.save()
        self.threshold.refresh_from_db()
        self.assertEqual(int(self.threshold.low_max), 20000)
        self.assertEqual(int(self.threshold.medium_max), 100000)


# ---------------------------------------------------------------------------
# index view — form mode
# ---------------------------------------------------------------------------

class IndexViewFormModeTests(TestCase):

    def test_get_passes_fnol_form_to_context(self):
        response = self.client.get("/")
        self.assertIn("fnol_form", response.context)

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_form_mode_valid_calls_process_fnol(self, mock_process):
        response = self.client.post("/", {
            "mode": "form",
            "description": "Cargo fell overboard.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/result.html")
        args, _ = mock_process.call_args
        self.assertIn("FIRST NOTICE OF LOSS", args[0])
        self.assertIn("Cargo fell overboard.", args[0])

    def test_form_mode_all_empty_rerenders_with_error(self):
        response = self.client.post("/", {"mode": "form"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/index.html")
        self.assertEqual(response.context["active_tab"], "form")
        self.assertFalse(response.context["fnol_form"].is_valid())


# ---------------------------------------------------------------------------
# index view — pdf mode
# ---------------------------------------------------------------------------

class IndexViewPdfModeTests(TestCase):

    def test_pdf_mode_no_file_rerenders_with_error(self):
        response = self.client.post("/", {"mode": "pdf"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/index.html")
        self.assertEqual(response.context["active_tab"], "pdf")
        self.assertIn("pdf_error", response.context)

    @patch("triage.views.extract_text_from_pdf", side_effect=PDFExtractionError("scanned image"))
    def test_pdf_mode_extraction_error_rerenders_with_error(self, _):
        pdf_file = SimpleUploadedFile("claim.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/", {"mode": "pdf", "pdf_file": pdf_file})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/index.html")
        self.assertEqual(response.context["active_tab"], "pdf")
        self.assertIn("scanned image", response.context["pdf_error"])

    @patch("triage.views.extract_text_from_pdf", return_value="extracted claim text")
    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_pdf_mode_success_calls_process_fnol(self, mock_process, _):
        pdf_file = SimpleUploadedFile("claim.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/", {"mode": "pdf", "pdf_file": pdf_file})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/result.html")
        mock_process.assert_called_once_with("extracted claim text")


# ---------------------------------------------------------------------------
# triage_stream view — form and pdf modes
# ---------------------------------------------------------------------------

class TriageStreamViewFormPdfTests(TestCase):

    @patch("triage.views.stream_fnol")
    def test_form_mode_valid_streams_result(self, mock_stream):
        mock_stream.return_value = iter([GOOD_RESULT])
        response = self.client.post("/stream/", {
            "mode": "form",
            "description": "Hull crack discovered at port.",
        })
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "done"', content)

    def test_form_mode_all_empty_streams_error_event(self):
        response = self.client.post("/stream/", {"mode": "form"})
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "error"', content)

    @patch("triage.views.extract_text_from_pdf", side_effect=PDFExtractionError("no text"))
    def test_pdf_mode_extraction_error_streams_error_event(self, _):
        pdf_file = SimpleUploadedFile("c.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/stream/", {"mode": "pdf", "pdf_file": pdf_file})
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "error"', content)
        self.assertIn("no text", content)

    @patch("triage.views.extract_text_from_pdf", return_value="pdf text")
    @patch("triage.views.stream_fnol")
    def test_pdf_mode_success_streams_result(self, mock_stream, _):
        mock_stream.return_value = iter([GOOD_RESULT])
        pdf_file = SimpleUploadedFile("c.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/stream/", {"mode": "pdf", "pdf_file": pdf_file})
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "done"', content)
        mock_stream.assert_called_once_with("pdf text")

    def test_pdf_mode_no_file_streams_error_event(self):
        response = self.client.post("/stream/", {"mode": "pdf"})
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"type": "error"', content)


# ---------------------------------------------------------------------------
# api_triage view
# ---------------------------------------------------------------------------

class ApiTriageViewTests(TestCase):

    def test_get_returns_405(self):
        response = self.client.get("/api/triage/")
        self.assertEqual(response.status_code, 405)

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_json_text_key_returns_200_json(self, mock_process):
        response = self.client.post(
            "/api/triage/",
            data=json.dumps({"text": "Cargo loss at sea"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = json.loads(response.content)
        self.assertEqual(data["claim_type"], "marine cargo")
        mock_process.assert_called_once_with("Cargo loss at sea")

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_json_form_data_key_returns_200_json(self, mock_process):
        response = self.client.post(
            "/api/triage/",
            data=json.dumps({"form_data": {"claimant_name": "Acme", "description": "Hull breach"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        args, _ = mock_process.call_args
        self.assertIn("FIRST NOTICE OF LOSS", args[0])
        self.assertIn("Acme", args[0])

    def test_json_missing_keys_returns_400(self):
        response = self.client.post(
            "/api/triage/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_body_returns_400(self):
        response = self.client.post(
            "/api/triage/",
            data=b"not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_multipart_text_field_returns_200_json(self, mock_process):
        response = self.client.post("/api/triage/", {"text": "Claim text here"})
        self.assertEqual(response.status_code, 200)
        mock_process.assert_called_once_with("Claim text here")

    @patch("triage.views.extract_text_from_pdf", return_value="pdf extracted text")
    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_multipart_file_calls_extract_pdf(self, mock_process, mock_extract):
        pdf_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/api/triage/", {"file": pdf_file})
        self.assertEqual(response.status_code, 200)
        mock_extract.assert_called_once()
        mock_process.assert_called_once_with("pdf extracted text")

    @patch("triage.views.extract_text_from_pdf", side_effect=PDFExtractionError("bad pdf"))
    def test_multipart_pdf_extraction_error_returns_400(self, _):
        pdf_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/api/triage/", {"file": pdf_file})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertTrue(data["error"])
        self.assertIn("bad pdf", data["message"])

    @patch("triage.views.process_fnol", return_value=ERROR_RESULT)
    def test_process_fnol_error_returns_422(self, _):
        response = self.client.post(
            "/api/triage/",
            data=json.dumps({"text": "Not a claim"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)
        data = json.loads(response.content)
        self.assertTrue(data["error"])

    def test_no_content_returns_400(self):
        response = self.client.post("/api/triage/", {})
        self.assertEqual(response.status_code, 400)

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def test_multipart_form_data_json_string_returns_200(self, mock_process):
        response = self.client.post(
            "/api/triage/",
            {"form_data": json.dumps({"description": "Hull breach during loading."})},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["claim_type"], "marine cargo")
        args, _ = mock_process.call_args
        self.assertIn("FIRST NOTICE OF LOSS", args[0])

    def test_multipart_form_data_invalid_json_returns_400(self):
        response = self.client.post("/api/triage/", {"form_data": "not valid json"})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertTrue(data["error"])


# ---------------------------------------------------------------------------
# claim_types_settings_view
# ---------------------------------------------------------------------------

class ClaimTypesSettingsViewTests(TestCase):

    def setUp(self):
        self.ct, _ = ClaimType.objects.get_or_create(
            slug="marine_cargo",
            defaults={"label": "Marine Cargo", "sort_order": 0, "active": True},
        )

    def test_get_returns_200(self):
        response = self.client.get("/settings/claim-types/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/settings_claim_types.html")
        self.assertIn("formset", response.context)

    def test_get_saved_flag_false_by_default(self):
        response = self.client.get("/settings/claim-types/")
        self.assertFalse(response.context["saved"])

    def test_get_saved_flag_true_when_querystring(self):
        response = self.client.get("/settings/claim-types/?saved=1")
        self.assertTrue(response.context["saved"])

    def test_post_valid_data_redirects(self):
        post_data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(self.ct.pk),
            "form-0-slug": "marine_cargo",
            "form-0-label": "Marine Cargo",
            "form-0-sort_order": "0",
            "form-0-active": "on",
        }
        response = self.client.post("/settings/claim-types/", post_data)
        self.assertRedirects(response, "/settings/claim-types/?saved=1")


# ---------------------------------------------------------------------------
# handlers_settings_view
# ---------------------------------------------------------------------------

class HandlersSettingsViewTests(TestCase):

    def setUp(self):
        self.handler = Handler.objects.create(
            name="Alice",
            role="Senior Handler",
            region="EMEA",
            speciality="marine cargo",
            active=True,
        )

    def test_get_returns_200(self):
        response = self.client.get("/settings/handlers/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/settings_handlers.html")
        self.assertIn("formset", response.context)

    def test_get_saved_flag_false_by_default(self):
        response = self.client.get("/settings/handlers/")
        self.assertFalse(response.context["saved"])

    def test_get_saved_flag_true_when_querystring(self):
        response = self.client.get("/settings/handlers/?saved=1")
        self.assertTrue(response.context["saved"])

    def test_post_valid_data_redirects(self):
        post_data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(self.handler.pk),
            "form-0-name": "Alice",
            "form-0-role": "Senior Handler",
            "form-0-region": "EMEA",
            "form-0-speciality": "marine cargo",
            "form-0-active": "on",
        }
        response = self.client.post("/settings/handlers/", post_data)
        self.assertRedirects(response, "/settings/handlers/?saved=1")


# ---------------------------------------------------------------------------
# FnolForm standalone validation
# ---------------------------------------------------------------------------

class FnolFormTests(TestCase):

    def setUp(self):
        ClaimType.objects.get_or_create(
            slug="marine_cargo",
            defaults={"label": "Marine Cargo", "sort_order": 0, "active": True},
        )

    def test_all_empty_is_invalid(self):
        form = FnolForm({})
        self.assertFalse(form.is_valid())

    def test_single_field_filled_is_valid(self):
        form = FnolForm({"description": "Hull damage from grounding."})
        self.assertTrue(form.is_valid())

    def test_loss_type_choices_filtered_to_active(self):
        ClaimType.objects.update_or_create(
            slug="liability",
            defaults={"label": "Liability", "sort_order": 1, "active": False},
        )
        form = FnolForm()
        choice_values = [v for v, _ in form.fields["loss_type"].choices]
        self.assertIn("marine_cargo", choice_values)
        self.assertNotIn("liability", choice_values)
