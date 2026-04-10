import json
import os
import tempfile
from unittest.mock import patch

from django.test import TestCase

from triage.models import Handler


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


# ---------------------------------------------------------------------------
# IndexTemplateTests
# ---------------------------------------------------------------------------

class IndexTemplateTests(TestCase):

    def setUp(self):
        self.response = self.client.get("/")

    def test_renders_claimsprint_brand(self):
        self.assertContains(self.response, "ClaimSprint")

    def test_renders_fnol_text_textarea(self):
        self.assertContains(self.response, 'name="fnol_text"')

    def test_renders_free_text_tab(self):
        self.assertContains(self.response, "Free Text")

    def test_renders_structured_form_tab(self):
        self.assertContains(self.response, "Structured Form")

    def test_renders_pdf_upload_tab(self):
        self.assertContains(self.response, "PDF Upload")

    def test_renders_triage_claim_submit_button(self):
        self.assertContains(self.response, "Triage Claim")

    def test_renders_nav_link_to_history(self):
        self.assertContains(self.response, 'href="/history/"')

    def test_renders_nav_link_to_staff(self):
        self.assertContains(self.response, 'href="/staff/"')

    def test_renders_nav_link_to_settings(self):
        self.assertContains(self.response, 'href="/settings/"')

    def test_form_has_multipart_enctype(self):
        self.assertContains(self.response, 'enctype="multipart/form-data"')


# ---------------------------------------------------------------------------
# ResultTemplateTests
# ---------------------------------------------------------------------------

class ResultTemplateTests(TestCase):

    @patch("triage.views.process_fnol", return_value=GOOD_RESULT)
    def _post(self, mock_process):
        return self.client.post("/", {"fnol_text": "Refrigerated cargo lost in transit."})

    def test_success_renders_claim_type_titlecased(self):
        response = self._post()
        self.assertContains(response, "Marine Cargo")

    def test_success_renders_severity(self):
        response = self._post()
        self.assertContains(response, "High")

    def test_success_renders_handler_name(self):
        response = self._post()
        self.assertContains(response, "Alice")

    def test_success_renders_triage_another_claim_link(self):
        response = self._post()
        self.assertContains(response, "Triage another claim")

    def test_success_renders_verify_ai_reasoning_button(self):
        response = self._post()
        self.assertContains(response, "Verify AI reasoning")

    def test_success_renders_view_submitted_fnol_text_details(self):
        response = self._post()
        self.assertContains(response, "View submitted FNOL text")

    def test_success_renders_assign_to_handler_action_label(self):
        response = self._post()
        self.assertContains(response, "Assign to Handler")

    @patch("triage.views.process_fnol", return_value={"error": True, "message": "Not a valid FNOL document."})
    def test_error_renders_error_card_with_message(self, mock_process):
        response = self.client.post("/", {"fnol_text": "hello world"})
        self.assertContains(response, "error-card")
        self.assertContains(response, "Not a valid FNOL document.")


# ---------------------------------------------------------------------------
# HistoryTemplateTests
# ---------------------------------------------------------------------------

class HistoryTemplateTests(TestCase):

    def test_get_no_entries_returns_200(self):
        with patch("triage.views.os.path.exists", return_value=False):
            response = self.client.get("/history/")
        self.assertEqual(response.status_code, 200)

    def test_get_no_entries_shows_empty_state(self):
        with patch("triage.views.os.path.exists", return_value=False):
            response = self.client.get("/history/")
        self.assertContains(response, "No triage decisions recorded yet.")

    def test_get_with_entries_renders_claim_type(self):
        entry = {
            "claim_type": "hull",
            "claim_subtype": "structural failure",
            "severity": "Critical",
            "severity_factors": [],
            "recommended_action": "assign_to_handler",
            "action_reasoning": [],
            "recommended_handler": {"name": "Bob", "role": "Handler", "region": "APAC", "speciality": "hull", "reason": ""},
            "coverage_flags": [],
            "confidence_score": 0.8,
            "reasoning_chain": [],
            "risk_flag_explanations": [],
            "timestamp": "2026-04-10T12:00:00Z",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(entry) + "\n")
            tmp_path = f.name
        try:
            with patch("triage.views._LOG_FILE", tmp_path):
                response = self.client.get("/history/")
            self.assertContains(response, "Hull")
        finally:
            os.unlink(tmp_path)

    def test_get_with_entries_renders_severity(self):
        entry = {
            "claim_type": "hull",
            "claim_subtype": "structural failure",
            "severity": "Critical",
            "severity_factors": [],
            "recommended_action": "assign_to_handler",
            "action_reasoning": [],
            "recommended_handler": {"name": "Bob", "role": "Handler", "region": "APAC", "speciality": "hull", "reason": ""},
            "coverage_flags": [],
            "confidence_score": 0.8,
            "reasoning_chain": [],
            "risk_flag_explanations": [],
            "timestamp": "2026-04-10T12:00:00Z",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(entry) + "\n")
            tmp_path = f.name
        try:
            with patch("triage.views._LOG_FILE", tmp_path):
                response = self.client.get("/history/")
            self.assertContains(response, "Critical")
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# StaffTemplateTests
# ---------------------------------------------------------------------------

class StaffTemplateTests(TestCase):

    def test_get_with_handlers_renders_handler_name(self):
        Handler.objects.create(
            name="Diana Prince",
            role="Senior Handler",
            region="EMEA",
            speciality="marine cargo",
            active=True,
        )
        response = self.client.get("/staff/")
        self.assertContains(response, "Diana Prince")

    def test_get_with_handlers_renders_region_label(self):
        Handler.objects.create(
            name="Diana Prince",
            role="Senior Handler",
            region="EMEA",
            speciality="marine cargo",
            active=True,
        )
        response = self.client.get("/staff/")
        self.assertContains(response, "EMEA")

    def test_get_no_handlers_returns_200(self):
        response = self.client.get("/staff/")
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# SettingsTemplateTests
# ---------------------------------------------------------------------------

class SettingsTemplateTests(TestCase):

    def test_get_settings_renders_save_button(self):
        response = self.client.get("/settings/")
        self.assertContains(response, "Save")

    def test_get_settings_with_saved_param_renders_confirmation(self):
        response = self.client.get("/settings/?saved=1")
        self.assertContains(response, "Thresholds saved.")

    def test_get_claim_types_returns_200(self):
        response = self.client.get("/settings/claim-types/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/settings_claim_types.html")

    def test_get_handlers_returns_200(self):
        response = self.client.get("/settings/handlers/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "triage/settings_handlers.html")


# ---------------------------------------------------------------------------
# ResultTemplateActionVariantsTests
# ---------------------------------------------------------------------------

class ResultTemplateActionVariantsTests(TestCase):

    def _post_with_action(self, action):
        result = dict(GOOD_RESULT)
        result["recommended_action"] = action
        with patch("triage.views.process_fnol", return_value=result):
            return self.client.post("/", {"fnol_text": "test claim"})

    def test_assign_to_handler_urgent_renders_assign_urgent(self):
        response = self._post_with_action("assign_to_handler_urgent")
        content = response.content.decode()
        self.assertIn("Assign", content)
        self.assertIn("Urgent", content)

    def test_escalate_to_senior_renders_label(self):
        response = self._post_with_action("escalate_to_senior")
        self.assertContains(response, "Escalate to Senior")

    def test_reject_renders_reject_claim_label(self):
        response = self._post_with_action("reject")
        self.assertContains(response, "Reject Claim")
