def form_data_to_fnol_text(data: dict) -> str:
    lines = ["FIRST NOTICE OF LOSS — STRUCTURED FORM SUBMISSION", ""]
    field_map = [
        ("Incident date",       str(data.get("incident_date") or "").strip()),
        ("Incident time",       str(data.get("incident_time") or "").strip()),
        ("Claimant name",       str(data.get("claimant_name") or "").strip()),
        ("Policy number",       str(data.get("policy_number") or "").strip()),
        ("Type of loss",        str(data.get("loss_type") or "").strip().replace("_", " ")),
        ("Incident location",   str(data.get("incident_location") or "").strip()),
        ("Estimated value",     str(data.get("estimated_value") or "").strip()),
        ("Description of loss", str(data.get("description") or "").strip()),
    ]
    for label, value in field_map:
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
