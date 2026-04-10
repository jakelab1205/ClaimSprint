from django import forms
from django.forms import modelformset_factory

from .models import SeverityThreshold


class SeverityThresholdForm(forms.ModelForm):
    class Meta:
        model = SeverityThreshold
        fields = ["low_max", "medium_max", "critical_description"]
        widgets = {
            "low_max": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "medium_max": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "critical_description": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "low_max": "Low Max (EUR)",
            "medium_max": "Medium Max (EUR)",
            "critical_description": "Critical description",
        }

    def clean(self):
        cleaned = super().clean()
        low = cleaned.get("low_max")
        med = cleaned.get("medium_max")
        if low is not None and med is not None and low >= med:
            raise forms.ValidationError("Low max must be less than Medium max.")
        return cleaned


SeverityThresholdFormSet = modelformset_factory(
    SeverityThreshold,
    form=SeverityThresholdForm,
    extra=0,
    can_delete=False,
)
