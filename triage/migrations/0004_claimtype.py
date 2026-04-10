from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("triage", "0003_seed_severity_thresholds"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClaimType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=50, unique=True)),
                ("label", models.CharField(max_length=100)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "ordering": ["sort_order", "slug"],
            },
        ),
    ]
