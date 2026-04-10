from django import forms
from django.forms import modelformset_factory

from .models import ClaimType, Handler, SeverityThreshold


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


class ClaimTypeForm(forms.ModelForm):
    class Meta:
        model = ClaimType
        fields = ["slug", "label", "sort_order", "active"]
        widgets = {
            "slug": forms.TextInput(attrs={"placeholder": "e.g. marine_cargo"}),
            "label": forms.TextInput(attrs={"placeholder": "e.g. Marine Cargo"}),
            "sort_order": forms.NumberInput(attrs={"min": "0"}),
        }
        labels = {
            "slug": "Slug",
            "label": "Display label",
            "sort_order": "Order",
            "active": "Active",
        }


ClaimTypeFormSet = modelformset_factory(
    ClaimType,
    form=ClaimTypeForm,
    extra=1,
    can_delete=True,
)


class HandlerForm(forms.ModelForm):
    class Meta:
        model = Handler
        fields = ["name", "role", "region", "speciality", "active"]
        widgets = {
            "speciality": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "name": "Name",
            "role": "Role",
            "region": "Region",
            "speciality": "Speciality",
            "active": "Active",
        }


HandlerFormSet = modelformset_factory(
    Handler,
    form=HandlerForm,
    extra=1,
    can_delete=True,
)


class FnolForm(forms.Form):
    incident_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Incident Date",
    )
    incident_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="Incident Time",
    )
    claimant_name = forms.CharField(max_length=200, required=False, label="Claimant Name")
    policy_number = forms.CharField(max_length=100, required=False, label="Policy Number")
    loss_type = forms.ChoiceField(choices=[], required=False, label="Type of Loss")
    incident_location = forms.CharField(max_length=300, required=False, label="Location of Incident")
    estimated_value = forms.CharField(
        max_length=100,
        required=False,
        label="Estimated Value (EUR)",
        help_text="Enter a number, e.g. 45000",
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        required=False,
        label="Description of Loss",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "— Select type of loss —")]
        choices += [
            (ct.slug, ct.label)
            for ct in ClaimType.objects.filter(active=True).order_by("sort_order", "slug")
        ]
        self.fields["loss_type"].choices = choices

    def clean(self):
        cleaned = super().clean()
        filled = [v for v in cleaned.values() if v]
        if not filled:
            raise forms.ValidationError("Please fill in at least one field before submitting.")
        return cleaned
