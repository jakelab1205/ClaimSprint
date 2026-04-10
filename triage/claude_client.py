import json
import os
from datetime import datetime, timezone

import anthropic


def _extract_json(raw: str) -> str:
    """Strip markdown fences and any surrounding prose, returning the JSON object."""
    s = raw
    if s.startswith("```"):
        s = s.split("\n", 1)[1]
        s = s.rsplit("```", 1)[0].strip()
    start = s.find("{")
    end = s.rfind("}") + 1
    if start != -1 and end > start:
        return s[start:end]
    return s


def _build_claim_type_list() -> str:
    from .models import ClaimType
    slugs = list(ClaimType.objects.filter(active=True).order_by("sort_order", "slug").values_list("slug", flat=True))
    return ", ".join(s.replace("_", " ") for s in slugs)


def _build_base_prompt() -> str:
    claim_types = _build_claim_type_list()
    return (
        "You are a senior insurance claims triage specialist at Nacora, an international freight insurance broker."
        " Your job is to read a First Notice of Loss (FNOL) and return a structured triage card."
        " The FNOL text may be written in any language."
        " You must always respond in English using the JSON schema below — never translate the schema keys or enum values.\n\n"
        "Before triaging, verify the input is a recognizable insurance claim."
        " A valid FNOL must contain at least two of: a description of an incident or loss,"
        " reference to insured goods or property, a policy number or insured party name,"
        " an estimated claim value or affected party."
        " If the input does not meet this threshold — e.g. it is a greeting, a test string, a question,"
        ' or unrelated text — return this exact JSON and nothing else:\n\n'
        '{"error": true, "message": "Input does not appear to be a First Notice of Loss. Please paste the actual claim notification text."}\n\n'
        "First write a triage summary: 2-3 plain-English sentences covering the claim type, estimated severity,"
        " and intended handler direction. Do not use curly braces or JSON syntax in this summary."
        " Every sentence must be complete and end with a full stop before you begin the JSON."
        " Then, on a new line, output the JSON object — no markdown, no code fences."
        " The JSON must exactly match this schema:\n\n"
        "{\n"
        f'  "claim_type": "<string: one of {claim_types}>",\n'
        '  "claim_subtype": "<string: specific subtype, e.g. refrigerated cargo loss, third-party bodily injury>",\n\n'
        '  "severity": "<string: one of Low, Medium, High, Critical>",\n'
        '  "severity_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],\n\n'
        '  "recommended_action": "<string: one of assign_to_handler, assign_to_handler_urgent, escalate_to_senior, request_documentation, reject>",\n'
        '  "action_reasoning": ["<step 1>", "<step 2>", "<step 3>"],\n\n'
        '  "recommended_handler": {\n'
        '    "name": "<string>",\n'
        '    "role": "<string>",\n'
        '    "region": "<string>",\n'
        '    "speciality": "<string>",\n'
        '    "reason": "<string: one sentence explaining why this handler was chosen>"\n'
        "  },\n\n"
        '  "coverage_flags": ["<short label 1>", "<short label 2>"],\n\n'
        '  "confidence_score": 0.85,\n'
        '  "reasoning_chain": [\n'
        '    "<step 1: FNOL identification and claim classification>",\n'
        '    "<step 2: loss quantum and exposure assessment>",\n'
        '    "<step 3: severity determination with specific facts>",\n'
        '    "<step 4: region and handler selection logic>",\n'
        '    "<step 5: coverage flag rationale>"\n'
        "  ],\n"
        '  "risk_flag_explanations": [\n'
        "    {\n"
        '      "flag": "<short label matching the entry in coverage_flags>",\n'
        '      "must_verify": ["<specific document or check 1>", "<specific document or check 2>"],\n'
        '      "risk_if_unverified": "<one sentence: consequence if this is not verified before settlement>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "confidence_score must be a float between 0.0 and 1.0. Score reflects how complete and unambiguous the FNOL is:\n"
        "- 0.85\u20131.0: FNOL is detailed, contains policy reference, loss quantum, and supporting evidence. Classification is unambiguous.\n"
        "- 0.65\u20130.84: FNOL is reasonably complete but missing one element (e.g. no policy number, or claim value estimated). Classification is clear.\n"
        "- 0.45\u20130.64: FNOL is sparse, ambiguous, or covers multiple claim types. Reasonable inference required.\n"
        "- Below 0.45: FNOL is critically incomplete. Key facts are missing. Handler should treat result as provisional.\n\n"
        "reasoning_chain must be an ordered list of 4 to 6 strings tracing the full decision path:"
        " FNOL classification \u2192 loss quantum \u2192 severity \u2192 handler selection \u2192 coverage flags."
        " Each step must cite specific facts from the FNOL, not generic statements."
        " This is distinct from action_reasoning (which is action-specific).\n\n"
        "risk_flag_explanations must be an array with one object per entry in coverage_flags."
        " If coverage_flags is empty, return an empty array. Each object has three fields:\n"
        "- flag: the exact short label from coverage_flags\n"
        '- must_verify: an array of 2 to 4 short strings, each naming a specific document, record,'
        ' or check the handler must obtain before proceeding'
        ' (e.g. "Pre-shipment temperature log from shipper", "Reefer set-point records from carrier")\n'
        "- risk_if_unverified: exactly one sentence stating the consequence if this check is skipped"
        ' (e.g. "Insurer may deny coverage on inherent vice grounds, leaving Nacora exposed to E&O liability.")\n\n'
        "Before assigning a handler, assess documentation completeness. A complete FNOL should contain:\n"
        "- A policy number or reference\n"
        "- Identity of the insured party or shipper\n"
        "- An estimated claim value or loss quantum\n"
        "- Supporting evidence referenced (bill of lading, survey report, photos, temperature log, etc.)\n\n"
        "If two or more of these are absent, set recommended_action to request_documentation and set action_reasoning"
        " to an array listing exactly which documents are missing and why each is required before the claim can be assessed.\n\n"
        "Severity guidelines \u2014 apply the set matching the claim_type:\n\n"
    )

_SYSTEM_PROMPT_HANDLER_INTRO = (
    "You must select the recommended_handler from this exact pool"
    " — use the name, role, region, and speciality verbatim:\n\n"
)

_SYSTEM_PROMPT_SUFFIX = """
Select the handler using this priority order:
1. Region match first — identify the geographic region of the incident or insured party (EMEA, APAC, Americas). If unclear, use EMEA as default.
2. Subtype match second — within the matched region, compare claim_subtype against each handler's speciality. Prefer the handler whose speciality contains keywords that match the subtype (e.g. "refrigerated cargo loss" → speciality containing "refrigerated"; "third-party bodily injury" → speciality containing "third-party liability").
3. If no subtype match exists within the region, fall back to the handler whose claim_type coverage best fits.
4. If the claim is Critical severity or involves complex multi-line exposure, override the above and assign Tom Kowalski regardless of region.
The reason field must name the specific region, subtype, and speciality factors that drove the selection — not a generic statement.

severity_factors must include 3 to 5 bullet-style strings. Each factor must state a specific fact from the FNOL that drove the severity rating — not generic statements.

action_reasoning must be an array of 2 to 4 concise imperative strings listing exactly what the handler should do, in priority order (e.g. "Dispatch independent surveyor immediately", "Request bill of lading and temperature log from shipper", "Flag for senior review given claim value"). Be specific to the claim details.

coverage_flags must be an array of 0 to 2 short label strings — maximum 6 words each. Each label names the exclusion or condition only, not the full explanation (e.g. "Inherent vice", "Delay exclusion", "Wilful misconduct", "Carrier liability — subrogation angle", "Sue and labour obligations"). The full explanation goes in risk_flag_explanations. If no exclusions are apparent, return an empty array.
"""


def _build_severity_guidelines() -> str:
    from .models import SeverityThreshold
    LABEL = {
        "marine_cargo": "Marine cargo / property",
        "liability": "Liability",
        "hull": "Hull",
    }
    ORDER = ["marine_cargo", "liability", "hull"]
    rows = {t.claim_type: t for t in SeverityThreshold.objects.filter(claim_type__in=ORDER)}
    lines = []
    for ct in ORDER:
        t = rows.get(ct)
        if t is None:
            continue
        low, med = int(t.low_max), int(t.medium_max)
        lines.append(
            f"{LABEL[ct]}:\n"
            f"- Low: value <EUR {low:,}\n"
            f"- Medium: EUR {low:,}\u2013{med:,}\n"
            f"- High: value >EUR {med:,}, time pressure, or escalating exposure\n"
            f"- Critical: {t.critical_description}\n"
        )
    return "\n".join(lines)


def _build_system_prompt(handlers: list) -> str:
    return (
        _build_base_prompt()
        + _build_severity_guidelines()
        + "\n"
        + _SYSTEM_PROMPT_HANDLER_INTRO
        + json.dumps(handlers, indent=2)
        + _SYSTEM_PROMPT_SUFFIX
    )


_LOG_FILE = "/data/triage_log.jsonl"


def _append_to_log(result: dict) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "claim_type": result.get("claim_type"),
        "claim_subtype": result.get("claim_subtype"),
        "severity": result.get("severity"),
        "severity_factors": result.get("severity_factors"),
        "recommended_action": result.get("recommended_action"),
        "action_reasoning": result.get("action_reasoning"),
        "recommended_handler": result.get("recommended_handler"),
        "coverage_flags": result.get("coverage_flags"),
        "confidence_score": result.get("confidence_score"),
        "reasoning_chain": result.get("reasoning_chain"),
        "risk_flag_explanations": result.get("risk_flag_explanations"),
    }
    os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
    with open(_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


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


def _get_handlers() -> list:
    from .models import Handler
    return list(
        Handler.objects.filter(active=True).values("name", "role", "region", "speciality")
    )


def _validate_handler(result: dict, handlers: list) -> dict:
    handler_names = {h["name"] for h in handlers}
    rh = result.get("recommended_handler") or {}
    if rh.get("name") not in handler_names:
        claim_type = result.get("claim_type", "").lower()
        fallback = next(
            (h for h in handlers if claim_type in h.get("speciality", "").lower()),
            handlers[0],
        )
        result["recommended_handler"] = {
            **fallback,
            "reason": "Assigned based on speciality match.",
        }
    return result


def process_fnol(text: str) -> dict:
    if not text or not text.strip():
        return {
            "error": True,
            "message": "No FNOL text provided. Please paste the claim notification and try again.",
        }

    handlers = _get_handlers()
    if not handlers:
        return {
            "error": True,
            "message": "No active handlers found in the staff directory. Please contact an administrator.",
        }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _build_system_prompt(handlers)

    raw = ""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            timeout=30,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Triage this FNOL:\n\n{text.strip()}",
                }
            ],
        )
        raw = response.content[0].text.strip()
        result = json.loads(_extract_json(raw))
        if result.get("error"):
            return result
        missing = _REQUIRED_KEYS - result.keys()
        if missing:
            return {
                "error": True,
                "message": f"AI response was incomplete (missing: {', '.join(sorted(missing))}). Please try again.",
                "raw": raw,
            }
        validated = _validate_handler(result, handlers)
        _append_to_log(validated)
        return validated
    except json.JSONDecodeError:
        return {
            "error": True,
            "message": "The AI returned an unexpected response. Please try again.",
            "raw": raw,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"An error occurred: {exc}",
        }


def stream_fnol(text: str):
    """Generator: yields raw token strings during streaming, then the final result dict."""
    if not text or not text.strip():
        yield {"error": True, "message": "No FNOL text provided. Please paste the claim notification and try again."}
        return

    handlers = _get_handlers()
    if not handlers:
        yield {"error": True, "message": "No active handlers found in the staff directory. Please contact an administrator."}
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _build_system_prompt(handlers)

    raw = ""
    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            timeout=120,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Triage this FNOL:\n\n{text.strip()}"}],
        ) as stream:
            for chunk in stream.text_stream:
                raw += chunk
                yield chunk

        result = json.loads(_extract_json(raw))
        if result.get("error"):
            yield result
            return
        missing = _REQUIRED_KEYS - result.keys()
        if missing:
            yield {"error": True, "message": f"AI response was incomplete (missing: {', '.join(sorted(missing))}). Please try again.", "raw": raw}
            return
        validated = _validate_handler(result, handlers)
        _append_to_log(validated)
        yield validated
    except json.JSONDecodeError:
        yield {"error": True, "message": "The AI returned an unexpected response. Please try again.", "raw": raw}
    except Exception as exc:
        yield {"error": True, "message": f"An error occurred: {exc}"}
