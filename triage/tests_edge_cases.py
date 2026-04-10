import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from .models import ClaimType, Handler


# ---------------------------------------------------------------------------
# 1. Input length limit tests
# ---------------------------------------------------------------------------

class InputLengthLimitTests(TestCase):
    """The 50,000-character guard is exercised before any AI call."""

    def setUp(self):
        self.client = Client()
        self.long_text = "x" * 50_001

    # -- index view, text mode -----------------------------------------------

    def test_index_text_mode_over_limit_returns_error(self):
        response = self.client.post("/", {"mode": "text", "fnol_text": self.long_text})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/result.html")
        result = response.context["result"]
        self.assertTrue(result.get("error"))
        self.assertIn("50,000 characters", result["message"])

    def test_index_text_mode_exactly_limit_is_allowed(self):
        """A string of exactly 50,000 characters must not be rejected by the
        length guard.  We mock process_fnol so no real API call happens."""
        text_at_limit = "x" * 50_000
        with patch("triage.views.process_fnol", return_value={"error": False, "summary": "ok"}):
            response = self.client.post("/", {"mode": "text", "fnol_text": text_at_limit})
        self.assertEqual(response.status_code, 200)
        # result context should NOT contain the length-error dict
        result = response.context["result"]
        self.assertFalse(result.get("error"))

    # -- index view, form mode -----------------------------------------------

    def test_index_form_mode_over_limit_returns_error(self):
        """form_data_to_fnol_text is patched to return a string over the limit."""
        ClaimType.objects.get_or_create(slug="hull", defaults={"label": "Hull", "active": True, "sort_order": 0})
        post_data = {
            "mode": "form",
            "claimant_name": "Alice",
            "policy_number": "POL-001",
            "loss_type": "hull",
            "incident_location": "Port of Hamburg",
            "estimated_value": "100000",
            "description": "Some damage",
        }
        with patch("triage.views.form_data_to_fnol_text", return_value="x" * 50_001):
            response = self.client.post("/", post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/result.html")
        result = response.context["result"]
        self.assertTrue(result.get("error"))
        self.assertIn("50,000 characters", result["message"])

    # -- stream view, text mode ----------------------------------------------

    def _collect_stream(self, response):
        """Consume a StreamingHttpResponse and return the concatenated body."""
        return b"".join(response.streaming_content).decode()

    def test_stream_text_mode_over_limit_returns_sse_error(self):
        response = self.client.post(
            "/stream/", {"mode": "text", "fnol_text": self.long_text}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "text/event-stream")
        body = self._collect_stream(response)
        # Body must contain an SSE data line with a JSON error payload
        self.assertIn("data:", body)
        # Extract the JSON payload from the first data line
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
                self.assertEqual(payload["type"], "error")
                self.assertIn("50,000 characters", payload["message"])
                break
        else:
            self.fail("No SSE data line found in stream response")

    # -- stream view, PDF mode -----------------------------------------------

    def test_stream_pdf_mode_over_limit_returns_sse_error(self):
        """extract_text_from_pdf is patched to return a string over the limit."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        dummy_pdf = SimpleUploadedFile(
            "test.pdf", b"%PDF-1.4 fake content", content_type="application/pdf"
        )
        with patch("triage.views.extract_text_from_pdf", return_value="x" * 50_001):
            response = self.client.post(
                "/stream/", {"mode": "pdf", "pdf_file": dummy_pdf}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "text/event-stream")
        body = self._collect_stream(response)
        self.assertIn("data:", body)
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
                self.assertEqual(payload["type"], "error")
                self.assertIn("50,000 characters", payload["message"])
                break
        else:
            self.fail("No SSE data line found in stream response")


# ---------------------------------------------------------------------------
# 2. API key authentication tests
# ---------------------------------------------------------------------------

class ApiKeyAuthTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/api/triage/"
        self.valid_body = json.dumps({"text": "A vessel ran aground near the harbour."})
        self.content_type = "application/json"

    def _post(self, key_header=None):
        headers = {}
        if key_header is not None:
            headers["HTTP_X_API_KEY"] = key_header
        return self.client.post(
            self.url,
            data=self.valid_body,
            content_type=self.content_type,
            **headers,
        )

    def test_missing_key_returns_401(self):
        with patch.dict(os.environ, {"CLAIMSPRINT_API_KEY": "secret"}):
            response = self._post(key_header=None)
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertTrue(data.get("error"))

    def test_wrong_key_returns_401(self):
        with patch.dict(os.environ, {"CLAIMSPRINT_API_KEY": "secret"}):
            response = self._post(key_header="wrong-key")
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertTrue(data.get("error"))

    def test_correct_key_proceeds(self):
        mock_result = {"error": False, "claim_type": "hull", "summary": "ok"}
        with patch.dict(os.environ, {"CLAIMSPRINT_API_KEY": "secret"}):
            with patch("triage.views.process_fnol", return_value=mock_result):
                response = self._post(key_header="secret")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data.get("error"))

    def test_no_env_key_set_proceeds_without_header(self):
        """When CLAIMSPRINT_API_KEY is absent, all requests pass the auth check."""
        mock_result = {"error": False, "claim_type": "hull", "summary": "ok"}
        env_without_key = {k: v for k, v in os.environ.items() if k != "CLAIMSPRINT_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with patch("triage.views.process_fnol", return_value=mock_result):
                response = self._post(key_header=None)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# 3. Handler delete via formset
# ---------------------------------------------------------------------------

class HandlerDeleteTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/settings/handlers/"

    def test_delete_flag_removes_handler(self):
        handler = Handler.objects.create(
            name="Jane Smith",
            role="Senior Adjuster",
            region="EMEA",
            speciality="Marine Hull",
            active=True,
        )
        pk = handler.pk

        post_data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(pk),
            "form-0-name": handler.name,
            "form-0-role": handler.role,
            "form-0-region": handler.region,
            "form-0-speciality": handler.speciality,
            "form-0-active": "on",
            "form-0-DELETE": "on",
        }
        response = self.client.post(self.url, post_data)
        # A successful save redirects back to the settings page
        self.assertIn(response.status_code, [200, 302])
        self.assertFalse(Handler.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# 4. ClaimType delete via formset
# ---------------------------------------------------------------------------

class ClaimTypeDeleteTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/settings/claim-types/"

    def test_delete_flag_removes_claim_type(self):
        ct = ClaimType.objects.create(
            slug="test_type",
            label="Test Type",
            active=True,
            sort_order=99,
        )
        pk = ct.pk

        post_data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(pk),
            "form-0-slug": ct.slug,
            "form-0-label": ct.label,
            "form-0-sort_order": str(ct.sort_order),
            "form-0-active": "on",
            "form-0-DELETE": "on",
        }
        response = self.client.post(self.url, post_data)
        self.assertIn(response.status_code, [200, 302])
        self.assertFalse(ClaimType.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# 5. API triage empty / whitespace text
# ---------------------------------------------------------------------------

class ApiTriageEmptyTextTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/api/triage/"

    def _post_json(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_whitespace_only_text_returns_400(self):
        response = self._post_json({"text": "   "})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertTrue(data.get("error"))

    def test_empty_string_text_returns_400(self):
        response = self._post_json({"text": ""})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertTrue(data.get("error"))

    def test_missing_text_key_returns_400(self):
        """A JSON body with neither 'text' nor 'form_data' should be rejected."""
        response = self._post_json({"unrecognised_key": "value"})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertTrue(data.get("error"))
