import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from triage.claude_client import process_fnol
from triage.models import Handler
from triage.tests_client import _REQUIRED_KEYS, _VALID_ACTIONS, _VALID_SEVERITIES

# ---------------------------------------------------------------------------
# Handler pool shared by all scenarios
# Names must exactly match the recommended_handler.name in each mock response.
# ---------------------------------------------------------------------------

SCENARIO_HANDLERS = [
    {
        "name": "Lars Brandt",
        "role": "Senior Handler",
        "region": "EMEA",
        "speciality": "marine cargo refrigerated",
        "active": True,
        "experience_years": 10,
    },
    {
        "name": "Sophie Mercier",
        "role": "Handler",
        "region": "EMEA",
        "speciality": "liability third-party bodily injury",
        "active": True,
        "experience_years": 7,
    },
    {
        "name": "Tom Kowalski",
        "role": "Senior Complex Claims Specialist",
        "region": "EMEA",
        "speciality": "multi-line complex exposure hull liability cargo",
        "active": True,
        "experience_years": 15,
    },
]

# ---------------------------------------------------------------------------
# Scenario A — Vague English, low specificity, single factor
# No policy number, no loss quantum, no insured identity.
# Expected: request_documentation, Low severity, confidence < 0.45.
# ---------------------------------------------------------------------------

FNOL_TEXT_A = (
    "Something happened to our shipment. It arrived damaged and we are not happy.\n"
    "We need this sorted out urgently. Please advise."
)

MOCK_RESPONSE_A = {
    "claim_type": "marine cargo",
    "claim_subtype": "cargo damage — cause unknown",
    "severity": "Low",
    "severity_factors": [
        "Damage reported by consignee with no supporting evidence",
        "No claim value provided — extent of loss unknown",
        "No policy number or insured party name supplied",
    ],
    "recommended_action": "request_documentation",
    "action_reasoning": [
        "Request policy number or certificate of insurance before any assessment can begin",
        "Obtain commercial invoice and packing list to establish cargo value",
        "Request survey report or photographic evidence of damage",
    ],
    "recommended_handler": {
        "name": "Lars Brandt",
        "role": "Senior Handler",
        "region": "EMEA",
        "speciality": "marine cargo refrigerated",
        "reason": "EMEA region default; no subtype match possible without further documentation.",
    },
    "coverage_flags": [],
    "confidence_score": 0.28,
    "reasoning_chain": [
        "FNOL received as a brief informal complaint with no structured claim data.",
        "No policy reference, insured party, loss quantum, or supporting documents identified.",
        "Severity cannot be meaningfully rated; Low assigned as provisional floor pending documentation.",
        "Handler assigned to EMEA region by default; subtype routing deferred until claim details received.",
        "No coverage flags raised — insufficient facts to identify any exclusion exposure.",
    ],
    "risk_flag_explanations": [],
}

# ---------------------------------------------------------------------------
# Scenario B — Detailed French, medium specificity, multi-factor liability
# Policy reference, identified insured, EUR 85,000 loss quantum.
# Expected: assign_to_handler_urgent, High severity, confidence >= 0.65.
# ---------------------------------------------------------------------------

FNOL_TEXT_B = (
    "Objet : Déclaration de sinistre — Police n° FR-2024-LB-00892\n\n"
    "Madame, Monsieur,\n\n"
    "Nous vous informons d'un accident survenu le 3 avril 2024 sur le terminal portuaire\n"
    "de Marseille-Fos. Un cariste employé par notre sous-traitant, M. Jean Dubois,\n"
    "a été blessé lors du déchargement de conteneurs frigorifiques appartenant à notre\n"
    "client Transmed SAS. Suite à une défaillance présumée du chariot élévateur,\n"
    "M. Dubois a subi une fracture ouverte du tibia droit et a été hospitalisé\n"
    "en urgence. Les frais médicaux estimés s'élèvent à EUR 85 000, avec une\n"
    "incapacité de travail provisoire de 6 mois.\n\n"
    "Un rapport de témoin a été établi et est disponible sur demande.\n"
    "Nous sollicitons votre intervention urgente.\n\n"
    "Cordialement,\n"
    "Responsable Sinistres, Transmed SAS"
)

MOCK_RESPONSE_B = {
    "claim_type": "liability",
    "claim_subtype": "third-party bodily injury — port operations",
    "severity": "High",
    "severity_factors": [
        "Open tibial fracture requiring emergency hospitalisation — serious personal injury confirmed",
        "Estimated medical costs of EUR 85,000 with 6-month provisional disability",
        "Third-party contractor involvement introduces employer/occupier liability complexity",
        "Incident at commercial port terminal — regulatory reporting obligations may apply",
    ],
    "recommended_action": "assign_to_handler_urgent",
    "action_reasoning": [
        "Instruct independent medical assessor to review injury severity and prognosis",
        "Obtain witness statement and forklift maintenance records to establish liability",
        "Confirm whether carrier's liability regime applies and whether subrogation is viable",
        "Flag to senior handler if medical costs exceed EUR 120,000 or disability becomes permanent",
    ],
    "recommended_handler": {
        "name": "Sophie Mercier",
        "role": "Handler",
        "region": "EMEA",
        "speciality": "liability third-party bodily injury",
        "reason": "EMEA region match; speciality in third-party bodily injury aligns directly with port injury claim subtype.",
    },
    "coverage_flags": ["Carrier liability — subrogation angle"],
    "confidence_score": 0.78,
    "reasoning_chain": [
        "FNOL is in French; classified as a liability claim — third-party bodily injury in port operations context.",
        "Policy reference FR-2024-LB-00892 confirmed; insured is Transmed SAS; loss quantum EUR 85,000 plus ongoing disability costs.",
        "Severity rated High: confirmed serious injury, estimated costs within High band, disability duration and third-party contractor chain elevate exposure.",
        "Sophie Mercier selected — EMEA region match with speciality in third-party bodily injury.",
        "Carrier liability coverage flag raised: forklift operated by sub-contractor under carrier's operational control; subrogation recovery against carrier should be assessed.",
    ],
    "risk_flag_explanations": [
        {
            "flag": "Carrier liability — subrogation angle",
            "must_verify": [
                "Forklift maintenance and inspection log from sub-contractor",
                "Port terminal operating contract between Transmed SAS and sub-contractor",
                "Carrier's liability policy schedule and jurisdictional limit endorsement",
                "Medical report confirming injury classification and prognosis",
            ],
            "risk_if_unverified": (
                "Without establishing whether the carrier's operational control extends to the forklift driver, "
                "subrogation recovery may be forfeited and Nacora's insured left to absorb the full EUR 85,000+ loss."
            ),
        }
    ],
}

# ---------------------------------------------------------------------------
# Scenario C — Highly specific German, Critical severity, cross-coverage
# Storm collision: hull EUR 1.2M + pharma cargo total loss EUR 3.4M + third-party claim.
# Expected: escalate_to_senior, Critical severity, Tom Kowalski override, confidence >= 0.85.
# ---------------------------------------------------------------------------

FNOL_TEXT_C = (
    "Betreff: FNOL — Police-Nr. DE-2024-HU-03317 / Havarie MV Nordstern\n\n"
    "Sehr geehrte Damen und Herren,\n\n"
    "wir melden hiermit einen schweren Schadensfall auf der MV Nordstern (IMO 9876543),\n"
    "die sich am 28. März 2024 auf der Route Hamburg–Rotterdam befand. Infolge eines\n"
    "extremen Sturms (Windstärke 11 Bft) kollidierte das Vessel mit dem Frachter\n"
    "MV Baltic Star. Dabei entstand ein Rumpfschaden am Bug (vorläufige Schadensschätzung:\n"
    "EUR 1.200.000) sowie ein Totalverlust von 42 Kühlcontainern mit pharmazeutischen\n"
    "Waren (Warenwert: EUR 3.400.000, Kühlung unterbrochen seit Unfall).\n\n"
    "Die Drittpartei (MV Baltic Star, Betreiber: Nordvik Shipping AS, Norwegen) hat\n"
    "bereits Haftungsansprüche angemeldet. Ein gemeinsamer Havereikommissar wurde bestellt.\n\n"
    "Versicherungsnehmer: Hamburg Freight Logistics GmbH\n"
    "Bearbeiter: Klaus Hoffmann, +49 40 555 2200\n"
    "Unterlagen: Konnossement Nr. HB-2024-0388, Temperaturprotokoll (lückenhaltig),\n"
    "Survey-Bericht vorläufig, Fotos vorhanden.\n\n"
    "Bitte um sofortige Eskalation."
)

MOCK_RESPONSE_C = {
    "claim_type": "hull",
    "claim_subtype": "collision damage — multi-line exposure (hull, cargo, third-party liability)",
    "severity": "Critical",
    "severity_factors": [
        "Hull damage estimate EUR 1,200,000 — exceeds Critical threshold",
        "Cargo total loss of 42 refrigerated containers of pharmaceutical goods valued at EUR 3,400,000",
        "Third-party liability claim filed by MV Baltic Star operator — collision with Nordvik Shipping AS",
        "Interrupted cold chain on pharmaceutical cargo — inherent vice and product liability exposure",
        "Combined exposure exceeds EUR 4,600,000 across three coverage lines simultaneously",
    ],
    "recommended_action": "escalate_to_senior",
    "action_reasoning": [
        "Escalate immediately to Tom Kowalski — combined multi-line exposure of EUR 4.6M triggers Critical threshold override",
        "Appoint specialist average adjuster and coordinate with jointly appointed average commissioner",
        "Secure temperature logs and bill of lading HB-2024-0388 before any cargo settlement discussion",
        "Instruct maritime lawyers in Hamburg to assess collision liability and third-party exposure under COLREGs",
    ],
    "recommended_handler": {
        "name": "Tom Kowalski",
        "role": "Senior Complex Claims Specialist",
        "region": "EMEA",
        "speciality": "multi-line complex exposure hull liability cargo",
        "reason": "Critical severity with multi-line exposure triggers mandatory override to Tom Kowalski per escalation protocol.",
    },
    "coverage_flags": [
        "Inherent vice — pharma cold chain",
        "Carrier liability — subrogation angle",
    ],
    "confidence_score": 0.91,
    "reasoning_chain": [
        "FNOL in German; classified as hull claim with simultaneous marine cargo and third-party liability exposure — cross-coverage collision scenario.",
        "Policy DE-2024-HU-03317 confirmed; insured Hamburg Freight Logistics GmbH; total exposure EUR 4,600,000 with unquantified third-party liability.",
        "Severity rated Critical: combined exposure across three lines exceeds EUR 4M; storm force 11 Bft; total cargo loss; active third-party claim filed.",
        "Tom Kowalski assigned per mandatory Critical/multi-line override — standard region/subtype routing bypassed.",
        "Inherent vice flag raised: temperature protocol described as incomplete — pharma cargo cold chain integrity unverifiable.",
        "Subrogation flag raised: MV Baltic Star collision — COLREGs fault allocation needed before liability is accepted.",
    ],
    "risk_flag_explanations": [
        {
            "flag": "Inherent vice — pharma cold chain",
            "must_verify": [
                "Full uninterrupted temperature log from carrier (reefer set-point and actual temp)",
                "Shipper's pre-shipment pharmaceutical storage certificate",
                "Cargo manifest confirming product type and required temperature range",
                "Survey report confirming whether cargo degradation preceded or followed the storm",
            ],
            "risk_if_unverified": (
                "Insurer may invoke inherent vice exclusion for the pharmaceutical cargo total loss, "
                "disallowing the EUR 3,400,000 claim and exposing Nacora to E&O liability for inadequate due diligence."
            ),
        },
        {
            "flag": "Carrier liability — subrogation angle",
            "must_verify": [
                "COLREGs fault apportionment report from average commissioner",
                "Nordvik Shipping AS P&I Club details and liability limit",
                "Collision clause endorsement on hull policy DE-2024-HU-03317",
                "Crew testimonies and VDR data from both vessels",
            ],
            "risk_if_unverified": (
                "Without establishing COLREGs fault allocation, Nacora cannot pursue subrogation against "
                "MV Baltic Star's P&I Club, forfeiting potential recovery of up to EUR 2.3M depending on proportional fault."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class FnolInputScenarioTests(TestCase):

    def setUp(self):
        for h in SCENARIO_HANDLERS:
            Handler.objects.create(**h)

        self.log_patcher = patch("triage.claude_client._append_to_log")
        self.log_patcher.start()

    def tearDown(self):
        self.log_patcher.stop()

    def _make_mock_client(self, mock_anthropic_cls, response_dict):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value.content = [
            MagicMock(text=json.dumps(response_dict))
        ]
        return mock_client

    def _assert_schema(self, result):
        self.assertFalse(result.get("error"), msg=f"process_fnol returned error: {result}")

        self.assertTrue(
            _REQUIRED_KEYS <= result.keys(),
            msg=f"Missing required keys: {_REQUIRED_KEYS - result.keys()}",
        )

        self.assertIn("claim_subtype", result)
        self.assertIsInstance(result["claim_subtype"], str)
        self.assertTrue(result["claim_subtype"].strip(), msg="claim_subtype is blank")

        self.assertIn(result["severity"], _VALID_SEVERITIES)
        self.assertIn(result["recommended_action"], _VALID_ACTIONS)

        score = result["confidence_score"]
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

        self.assertIsInstance(result["severity_factors"], list)
        self.assertGreater(len(result["severity_factors"]), 0)

        self.assertIsInstance(result["action_reasoning"], list)
        self.assertGreater(len(result["action_reasoning"]), 0)

        self.assertIsInstance(result["reasoning_chain"], list)
        self.assertGreaterEqual(len(result["reasoning_chain"]), 4)

        self.assertIsInstance(result["coverage_flags"], list)
        self.assertIsInstance(result["risk_flag_explanations"], list)
        self.assertEqual(
            len(result["coverage_flags"]),
            len(result["risk_flag_explanations"]),
            msg="coverage_flags and risk_flag_explanations lengths differ",
        )

        for rfe in result["risk_flag_explanations"]:
            self.assertIn("flag", rfe)
            self.assertIn("must_verify", rfe)
            self.assertIn("risk_if_unverified", rfe)
            self.assertIsInstance(rfe["must_verify"], list)
            self.assertIsInstance(rfe["risk_if_unverified"], str)
            self.assertIn(
                rfe["flag"],
                result["coverage_flags"],
                msg=f"risk_flag_explanations flag '{rfe['flag']}' not in coverage_flags",
            )

        rh = result["recommended_handler"]
        self.assertIsInstance(rh, dict)
        self.assertTrue(
            {"name", "role", "region", "speciality", "reason"} <= rh.keys()
        )
        for field in ("name", "role", "region", "speciality", "reason"):
            self.assertIsInstance(rh[field], str)
            self.assertTrue(rh[field].strip(), msg=f"recommended_handler.{field} is blank")

    @patch("triage.claude_client.anthropic.Anthropic")
    def test_scenario_a_vague_english_request_documentation(self, mock_anthropic_cls):
        self._make_mock_client(mock_anthropic_cls, MOCK_RESPONSE_A)
        result = process_fnol(FNOL_TEXT_A)
        self._assert_schema(result)
        self.assertEqual(result["recommended_action"], "request_documentation")
        self.assertLess(result["confidence_score"], 0.45)
        self.assertEqual(result["severity"], "Low")
        self.assertEqual(len(result["coverage_flags"]), 0)
        self.assertEqual(len(result["risk_flag_explanations"]), 0)

    @patch("triage.claude_client.anthropic.Anthropic")
    def test_scenario_b_french_liability_urgent(self, mock_anthropic_cls):
        self._make_mock_client(mock_anthropic_cls, MOCK_RESPONSE_B)
        result = process_fnol(FNOL_TEXT_B)
        self._assert_schema(result)
        self.assertEqual(result["claim_type"], "liability")
        self.assertEqual(result["severity"], "High")
        self.assertEqual(result["recommended_action"], "assign_to_handler_urgent")
        self.assertGreaterEqual(result["confidence_score"], 0.65)
        self.assertEqual(result["recommended_handler"]["name"], "Sophie Mercier")
        self.assertEqual(len(result["coverage_flags"]), 1)
        self.assertEqual(len(result["risk_flag_explanations"]), 1)
        self.assertEqual(
            result["risk_flag_explanations"][0]["flag"], result["coverage_flags"][0]
        )

    @patch("triage.claude_client.anthropic.Anthropic")
    def test_scenario_c_german_critical_multiline_escalation(self, mock_anthropic_cls):
        self._make_mock_client(mock_anthropic_cls, MOCK_RESPONSE_C)
        result = process_fnol(FNOL_TEXT_C)
        self._assert_schema(result)
        self.assertEqual(result["claim_type"], "hull")
        self.assertEqual(result["severity"], "Critical")
        self.assertEqual(result["recommended_action"], "escalate_to_senior")
        self.assertGreaterEqual(result["confidence_score"], 0.85)
        self.assertEqual(result["recommended_handler"]["name"], "Tom Kowalski")
        self.assertEqual(len(result["coverage_flags"]), 2)
        self.assertEqual(len(result["risk_flag_explanations"]), 2)
        self.assertGreaterEqual(len(result["severity_factors"]), 3)
