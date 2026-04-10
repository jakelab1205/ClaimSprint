from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .claude_client import process_fnol


@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == "GET":
        return render(request, "triage/index.html")

    fnol_text = request.POST.get("fnol_text", "")
    result = process_fnol(fnol_text)
    return render(request, "triage/result.html", {"result": result, "fnol_text": fnol_text})
