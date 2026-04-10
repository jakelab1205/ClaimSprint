from django.db import migrations

INITIAL = [
    {
        "claim_type": "marine_cargo",
        "low_max": "10000.00",
        "medium_max": "50000.00",
        "critical_description": (
            "Total loss, catastrophic damage, or regulatory/legal exposure imminent"
        ),
    },
    {
        "claim_type": "liability",
        "low_max": "5000.00",
        "medium_max": "25000.00",
        "critical_description": (
            "Serious bodily injury, fatality, class action exposure, or regulatory investigation"
        ),
    },
    {
        "claim_type": "hull",
        "low_max": "20000.00",
        "medium_max": "100000.00",
        "critical_description": (
            "Constructive total loss (CTL), vessel missing, or crew safety involved"
        ),
    },
]


def seed(apps, schema_editor):
    SeverityThreshold = apps.get_model("triage", "SeverityThreshold")
    for row in INITIAL:
        SeverityThreshold.objects.get_or_create(
            claim_type=row["claim_type"],
            defaults={k: v for k, v in row.items() if k != "claim_type"},
        )


def unseed(apps, schema_editor):
    SeverityThreshold = apps.get_model("triage", "SeverityThreshold")
    SeverityThreshold.objects.filter(
        claim_type__in=[r["claim_type"] for r in INITIAL]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("triage", "0002_severitythreshold"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
