import json
from io import StringIO
from unittest.mock import MagicMock, mock_open, patch

from django.test import TestCase

from triage.claude_client import (
    _build_claim_type_list,
    _extract_json,
    _validate_handler,
    process_fnol,
    stream_fnol,
)
from triage.models import ClaimType, Handler, SeverityThreshold


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class ExtractJsonTests(TestCase):

    def test_plain_json_unchanged(self):
        raw = '{"foo": 1}'
        self.assertEqual(_extract_json(raw), '{"foo": 1}')

    def test_fenced_json_no_language_tag(self):
        raw = "```\n{\"foo\": 1}\n```"
        self.assertEqual(json.loads(_extract_json(raw)), {"foo": 1})

    def test_fenced_json_with_language_tag(self):
        raw = "```json\n{\"foo\": 1}\n```"
        self.assertEqual(json.loads(_extract_json(raw)), {"foo": 1})

    def test_prose_before_and_after(self):
        raw = 'Here is the result: {"foo": 1} end'
        self.assertEqual(json.loads(_extract_json(raw)), {"foo": 1})

    def test_fences_and_prose_combined(self):
        raw = "Sure!\n```json\n{\"foo\": 1}\n```\nDone."
        self.assertEqual(json.loads(_extract_json(raw)), {"foo": 1})

    def test_nested_braces(self):
        raw = '{"a": {"b": 2}}'
        self.assertEqual(json.loads(_extract_json(raw)), {"a": {"b": 2}})

    def test_no_braces_returns_raw(self):
        raw = "no braces here"
        result = _extract_json(raw)
        self.assertEqual(result, raw)


# ---------------------------------------------------------------------------
# _validate_handler
# ---------------------------------------------------------------------------

HANDLER_POOL = [
    {"name": "Alice", "role": "Senior Handler", "region": "EMEA", "speciality": "marine cargo refrigerated"},
    {"name": "Bob", "role": "Handler", "region": "APAC", "speciality": "hull and machinery"},
    {"name": "Carol", "role": "Handler", "region": "Americas", "speciality": "liability third-party"},
]


class ValidateHandlerTests(TestCase):

    def test_name_in_pool_unchanged(self):
        result = {"recommended_handler": {"name": "Alice", "role": "Senior Handler"}, "claim_type": "marine cargo"}
        out = _validate_handler(result, HANDLER_POOL)
        self.assertEqual(out["recommended_handler"]["name"], "Alice")
        self.assertEqual(out["recommended_handler"]["role"], "Senior Handler")

    def test_name_not_in_pool_speciality_match(self):
        result = {"recommended_handler": {"name": "Unknown"}, "claim_type": "hull"}
        out = _validate_handler(result, HANDLER_POOL)
        self.assertEqual(out["recommended_handler"]["name"], "Bob")
        self.assertEqual(out["recommended_handler"]["reason"], "Assigned based on speciality match.")

    def test_name_not_in_pool_no_match_falls_back_to_first(self):
        result = {"recommended_handler": {"name": "Nobody"}, "claim_type": "property"}
        out = _validate_handler(result, HANDLER_POOL)
        self.assertEqual(out["recommended_handler"]["name"], "Alice")
        self.assertEqual(out["recommended_handler"]["reason"], "Assigned based on speciality match.")

    def test_missing_recommended_handler_key(self):
        result = {"claim_type": "marine cargo"}
        out = _validate_handler(result, HANDLER_POOL)
        self.assertIn(out["recommended_handler"]["name"], {h["name"] for h in HANDLER_POOL})

    def test_all_handler_fields_copied_on_fallback(self):
        result = {"recommended_handler": {"name": "Ghost"}, "claim_type": "hull"}
        out = _validate_handler(result, HANDLER_POOL)
        rh = out["recommended_handler"]
        self.assertEqual(rh["role"], "Handler")
        self.assertEqual(rh["region"], "APAC")
        self.assertEqual(rh["speciality"], "hull and machinery")


# ---------------------------------------------------------------------------
# process_fnol
# ---------------------------------------------------------------------------

GOOD_RESULT = {
    "claim_type": "marine cargo",
    "claim_subtype": "refrigerated cargo loss",
    "severity": "High",
    "severity_factors": ["factor 1", "factor 2", "factor 3"],
    "recommended_action": "assign_to_handler",
    "action_reasoning": ["step 1", "step 2"],
    "recommended_handler": {"name": "Alice", "role": "Senior Handler", "region": "EMEA", "speciality": "marine cargo refrigerated", "reason": "Region match."},
    "coverage_flags": [],
    "confidence_score": 0.9,
    "reasoning_chain": ["step 1", "step 2", "step 3", "step 4"],
    "risk_flag_explanations": [],
}

MOCK_HANDLERS = [
    {"name": "Alice", "role": "Senior Handler", "region": "EMEA", "speciality": "marine cargo refrigerated"},
]


class ProcessFnolTests(TestCase):

    def test_empty_text_returns_error(self):
        result = process_fnol("")
        self.assertTrue(result["error"])
        self.assertIn("No FNOL text", result["message"])

    def test_whitespace_only_returns_error(self):
        result = process_fnol("   \n  ")
        self.assertTrue(result["error"])

    @patch("triage.claude_client._get_handlers", return_value=[])
    def test_no_handlers_returns_error(self, _):
        result = process_fnol("Some FNOL text")
        self.assertTrue(result["error"])
        self.assertIn("No active handlers", result["message"])

    @patch("triage.claude_client._append_to_log")
    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_successful_path_returns_result(self, mock_anthropic_cls, mock_get_handlers, mock_log):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value.content = [MagicMock(text=json.dumps(GOOD_RESULT))]

        result = process_fnol("FNOL text here")
        self.assertFalse(result.get("error"))
        self.assertEqual(result["claim_type"], "marine cargo")
        mock_log.assert_called_once()

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_api_returns_error_dict_passthrough(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        error_response = {"error": True, "message": "Not a valid FNOL."}
        mock_client.messages.create.return_value.content = [MagicMock(text=json.dumps(error_response))]

        result = process_fnol("hello")
        self.assertTrue(result["error"])
        self.assertEqual(result["message"], "Not a valid FNOL.")

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_json_decode_error_returns_error(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value.content = [MagicMock(text="not valid json")]

        result = process_fnol("FNOL text")
        self.assertTrue(result["error"])
        self.assertIn("unexpected response", result["message"])

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_missing_required_keys_returns_error(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        partial = {"claim_type": "marine cargo", "severity": "High"}
        mock_client.messages.create.return_value.content = [MagicMock(text=json.dumps(partial))]

        result = process_fnol("FNOL text")
        self.assertTrue(result["error"])
        self.assertIn("incomplete", result["message"])

    @patch("triage.claude_client._append_to_log")
    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_handler_validation_applied(self, mock_anthropic_cls, mock_get_handlers, mock_log):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        bad_handler = dict(GOOD_RESULT)
        bad_handler["recommended_handler"] = {"name": "Ghost"}
        mock_client.messages.create.return_value.content = [MagicMock(text=json.dumps(bad_handler))]

        result = process_fnol("FNOL text")
        self.assertEqual(result["recommended_handler"]["name"], "Alice")

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_fenced_response_parsed_correctly(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fenced = f"```json\n{json.dumps(GOOD_RESULT)}\n```"
        mock_client.messages.create.return_value.content = [MagicMock(text=fenced)]

        with patch("triage.claude_client._append_to_log"):
            result = process_fnol("FNOL text")
        self.assertFalse(result.get("error"))
        self.assertEqual(result["severity"], "High")

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_generic_exception_returns_error(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("network error")

        result = process_fnol("FNOL text")
        self.assertTrue(result["error"])
        self.assertIn("network error", result["message"])


# ---------------------------------------------------------------------------
# stream_fnol
# ---------------------------------------------------------------------------

class StreamFnolTests(TestCase):

    def test_empty_text_yields_error(self):
        items = list(stream_fnol(""))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["error"])

    @patch("triage.claude_client._get_handlers", return_value=[])
    def test_no_handlers_yields_error(self, _):
        items = list(stream_fnol("FNOL text"))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["error"])

    @patch("triage.claude_client._append_to_log")
    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_successful_stream_yields_tokens_then_result(self, mock_anthropic_cls, mock_get_handlers, mock_log):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        chunks = list(json.dumps(GOOD_RESULT))
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(chunks)
        mock_client.messages.stream.return_value = mock_stream

        items = list(stream_fnol("FNOL text"))
        string_items = [i for i in items if isinstance(i, str)]
        dict_items = [i for i in items if isinstance(i, dict)]

        self.assertTrue(len(string_items) > 0)
        self.assertEqual(len(dict_items), 1)
        self.assertEqual(dict_items[0]["claim_type"], "marine cargo")

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_json_decode_error_yields_error(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["not", " ", "json"])
        mock_client.messages.stream.return_value = mock_stream

        items = list(stream_fnol("FNOL text"))
        dict_items = [i for i in items if isinstance(i, dict)]
        self.assertTrue(dict_items[-1]["error"])

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_api_error_dict_yielded(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        error_payload = {"error": True, "message": "Not a valid FNOL."}
        chunks = list(json.dumps(error_payload))
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(chunks)
        mock_client.messages.stream.return_value = mock_stream

        items = list(stream_fnol("hello"))
        dict_items = [i for i in items if isinstance(i, dict)]
        self.assertTrue(dict_items[-1]["error"])
        self.assertEqual(dict_items[-1]["message"], "Not a valid FNOL.")

    @patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
    @patch("triage.claude_client.anthropic.Anthropic")
    def test_missing_required_keys_yields_error(self, mock_anthropic_cls, _):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        partial = {"claim_type": "marine cargo"}
        chunks = list(json.dumps(partial))
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(chunks)
        mock_client.messages.stream.return_value = mock_stream

        items = list(stream_fnol("FNOL text"))
        dict_items = [i for i in items if isinstance(i, dict)]
        self.assertTrue(dict_items[-1]["error"])
        self.assertIn("incomplete", dict_items[-1]["message"])


# ---------------------------------------------------------------------------
# Integration: real DB rows
# ---------------------------------------------------------------------------

class IntegrationDbTests(TestCase):

    def setUp(self):
        Handler.objects.create(
            name="Alice",
            role="Senior Handler",
            region="EMEA",
            speciality="marine cargo refrigerated",
            active=True,
        )
        Handler.objects.create(
            name="Inactive Bob",
            role="Handler",
            region="APAC",
            speciality="hull",
            active=False,
        )
        SeverityThreshold.objects.update_or_create(
            claim_type="marine_cargo",
            defaults={
                "low_max": 50000,
                "medium_max": 250000,
                "critical_description": "Total loss or multi-modal exposure >EUR 1M",
            },
        )

    def test_get_handlers_returns_only_active(self):
        from triage.claude_client import _get_handlers
        handlers = _get_handlers()
        names = [h["name"] for h in handlers]
        self.assertIn("Alice", names)
        self.assertNotIn("Inactive Bob", names)

    def test_build_severity_guidelines_includes_thresholds(self):
        from triage.claude_client import _build_severity_guidelines
        text = _build_severity_guidelines()
        self.assertIn("50,000", text)
        self.assertIn("250,000", text)
        self.assertIn("Marine cargo", text)


# ---------------------------------------------------------------------------
# _build_claim_type_list
# ---------------------------------------------------------------------------

class BuildClaimTypeListTests(TestCase):

    def setUp(self):
        ClaimType.objects.all().delete()

    def test_returns_slugs_for_active_claim_types(self):
        ClaimType.objects.create(slug="marine_cargo", label="Marine Cargo", active=True, sort_order=0)
        ClaimType.objects.create(slug="hull", label="Hull", active=True, sort_order=1)
        result = _build_claim_type_list()
        self.assertIn("marine cargo", result)
        self.assertIn("hull", result)

    def test_excludes_inactive_claim_types(self):
        ClaimType.objects.create(slug="marine_cargo", label="Marine Cargo", active=True, sort_order=0)
        ClaimType.objects.create(slug="liability", label="Liability", active=False, sort_order=1)
        result = _build_claim_type_list()
        self.assertIn("marine cargo", result)
        self.assertNotIn("liability", result)

    def test_empty_db_returns_empty_string(self):
        result = _build_claim_type_list()
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# process_fnol response contract
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "claim_type",
    "severity",
    "severity_factors",
    "recommended_action",
    "action_reasoning",
    "recommended_handler",
    "confidence_score",
    "reasoning_chain",
    "risk_flag_explanations",
}

_VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}

_VALID_ACTIONS = {
    "assign_to_handler",
    "assign_to_handler_urgent",
    "escalate_to_senior",
    "request_documentation",
    "reject",
}


class ProcessFnolContractTests(TestCase):

    def setUp(self):
        self.mock_anthropic_patcher = patch("triage.claude_client.anthropic.Anthropic")
        self.mock_get_handlers_patcher = patch("triage.claude_client._get_handlers", return_value=MOCK_HANDLERS)
        self.mock_log_patcher = patch("triage.claude_client._append_to_log")

        mock_anthropic_cls = self.mock_anthropic_patcher.start()
        self.mock_get_handlers_patcher.start()
        self.mock_log_patcher.start()

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value.content = [MagicMock(text=json.dumps(GOOD_RESULT))]

        self.result = process_fnol("FNOL text here")

    def tearDown(self):
        self.mock_anthropic_patcher.stop()
        self.mock_get_handlers_patcher.stop()
        self.mock_log_patcher.stop()

    def test_all_required_keys_present(self):
        self.assertTrue(_REQUIRED_KEYS <= self.result.keys(), f"Missing keys: {_REQUIRED_KEYS - self.result.keys()}")

    def test_severity_is_valid_enum(self):
        self.assertIn(self.result["severity"], _VALID_SEVERITIES)

    def test_recommended_action_is_valid_enum(self):
        self.assertIn(self.result["recommended_action"], _VALID_ACTIONS)

    def test_confidence_score_is_float_in_range(self):
        score = self.result["confidence_score"]
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_severity_factors_is_nonempty_list(self):
        factors = self.result["severity_factors"]
        self.assertIsInstance(factors, list)
        self.assertGreater(len(factors), 0)

    def test_recommended_handler_has_required_subkeys(self):
        rh = self.result["recommended_handler"]
        self.assertIsInstance(rh, dict)
        self.assertTrue({"name", "role", "region", "speciality", "reason"} <= rh.keys())

    def test_reasoning_chain_minimum_length(self):
        chain = self.result["reasoning_chain"]
        self.assertIsInstance(chain, list)
        self.assertGreaterEqual(len(chain), 4)

    def test_risk_flag_explanations_matches_coverage_flags_length(self):
        self.assertEqual(
            len(self.result["risk_flag_explanations"]),
            len(self.result["coverage_flags"]),
        )
