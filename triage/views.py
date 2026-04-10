import json
import os

from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .claude_client import _LOG_FILE, process_fnol
from .forms import SeverityThresholdFormSet
from .models import Handler, SeverityThreshold


@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == "GET":
        return render(request, "triage/index.html")

    fnol_text = request.POST.get("fnol_text", "")
    result = process_fnol(fnol_text)
    return render(request, "triage/result.html", {"result": result, "fnol_text": fnol_text})


@require_http_methods(["GET"])
def history(request):
    entries = []
    if os.path.exists(_LOG_FILE):
        with open(_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    entries.reverse()
    return render(request, "triage/history.html", {"entries": entries})


@require_http_methods(["GET"])
def staff_directory(request):
    handlers = Handler.objects.filter(active=True).order_by("region", "name")

    regions = {}
    region_order = ["EMEA", "APAC", "Americas", "Global"]
    for h in handlers:
        regions.setdefault(h.region, []).append(h)

    ordered_regions = [(r, regions[r]) for r in region_order if r in regions]
    for r, hs in regions.items():
        if r not in region_order:
            ordered_regions.append((r, hs))

    return render(request, "triage/staff.html", {"regions": ordered_regions})


@require_http_methods(["GET", "POST"])
def settings_view(request):
    qs = SeverityThreshold.objects.filter(
        claim_type__in=["marine_cargo", "liability", "hull"]
    ).order_by("claim_type")
    if request.method == "POST":
        formset = SeverityThresholdFormSet(request.POST, queryset=qs)
        if formset.is_valid():
            formset.save()
            return redirect("/settings/?saved=1")
    else:
        formset = SeverityThresholdFormSet(queryset=qs)
    return render(request, "triage/settings.html", {
        "formset": formset,
        "saved": request.GET.get("saved") == "1",
    })
