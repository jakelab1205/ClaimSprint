from django.db import models


class Handler(models.Model):
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=150)
    region = models.CharField(max_length=50)
    speciality = models.TextField()
    licenses = models.TextField(blank=True)
    expertise_tags = models.TextField(blank=True)
    bio = models.TextField(blank=True)
    experience_years = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["region", "name"]

    def __str__(self):
        return f"{self.name} ({self.region})"

    def expertise_list(self):
        return [t.strip() for t in self.expertise_tags.split(",") if t.strip()]


class SeverityThreshold(models.Model):
    CLAIM_TYPE_CHOICES = [
        ("hull", "Hull"),
        ("liability", "Liability"),
        ("marine_cargo", "Marine Cargo"),
    ]
    claim_type = models.CharField(max_length=20, choices=CLAIM_TYPE_CHOICES, unique=True)
    low_max = models.DecimalField(max_digits=12, decimal_places=2)
    medium_max = models.DecimalField(max_digits=12, decimal_places=2)
    critical_description = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["claim_type"]

    def __str__(self):
        return f"SeverityThreshold({self.claim_type})"
