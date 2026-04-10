from django.db import migrations

INITIAL = [
    {"slug": "marine_cargo", "label": "Marine Cargo", "sort_order": 0},
    {"slug": "hull", "label": "Hull / Vessel", "sort_order": 1},
    {"slug": "liability", "label": "Liability", "sort_order": 2},
    {"slug": "property", "label": "Property", "sort_order": 3},
    {"slug": "other", "label": "Other", "sort_order": 4},
]


def seed(apps, schema_editor):
    ClaimType = apps.get_model("triage", "ClaimType")
    for row in INITIAL:
        ClaimType.objects.get_or_create(
            slug=row["slug"],
            defaults={k: v for k, v in row.items() if k != "slug"},
        )


def unseed(apps, schema_editor):
    ClaimType = apps.get_model("triage", "ClaimType")
    ClaimType.objects.filter(slug__in=[r["slug"] for r in INITIAL]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("triage", "0004_claimtype"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
