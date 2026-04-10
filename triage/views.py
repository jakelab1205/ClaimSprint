import json
import os
from io import BytesIO

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .claude_client import _LOG_FILE, process_fnol, stream_fnol
from .forms import ClaimTypeFormSet, FnolForm, HandlerFormSet, SeverityThresholdFormSet
from .input_utils import form_data_to_fnol_text
from .models import ClaimType, Handler, SeverityThreshold
from .pdf_utils import PDFExtractionError, extract_text_from_pdf


@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == "GET":
        return render(request, "triage/index.html", {"fnol_form": FnolForm()})

    mode = request.POST.get("mode", "text")

    if mode == "form":
        fnol_form = FnolForm(request.POST)
        if not fnol_form.is_valid():
            return render(request, "triage/index.html", {
                "fnol_form": fnol_form,
                "active_tab": "form",
            })
        fnol_text = form_data_to_fnol_text(fnol_form.cleaned_data)

    elif mode == "pdf":
        uploaded = request.FILES.get("pdf_file")
        if not uploaded:
            return render(request, "triage/index.html", {
                "fnol_form": FnolForm(),
                "active_tab": "pdf",
                "pdf_error": "No file was uploaded.",
            })
        try:
            fnol_text = extract_text_from_pdf(uploaded)
        except PDFExtractionError as exc:
            return render(request, "triage/index.html", {
                "fnol_form": FnolForm(),
                "active_tab": "pdf",
                "pdf_error": str(exc),
            })

    else:
        fnol_text = request.POST.get("fnol_text", "")

    if len(fnol_text) > 50_000:
        return render(request, "triage/result.html", {
            "result": {"error": True, "message": "Input exceeds 50,000 characters. Please shorten the submission."},
            "fnol_text": fnol_text,
        })

    result = process_fnol(fnol_text)
    return render(request, "triage/result.html", {
        "result": result,
        "fnol_text": fnol_text,
        "result_json": json.dumps(result),
    })


@require_http_methods(["POST"])
def triage_stream(request):
    mode = request.POST.get("mode", "text")

    if mode == "form":
        fnol_form = FnolForm(request.POST)
        if not fnol_form.is_valid():
            errors = " ".join(
                str(msg) for field_errors in fnol_form.errors.values() for msg in field_errors
            )

            def form_error_stream():
                yield f"data: {json.dumps({'type': 'error', 'message': errors})}\n\n"

            response = StreamingHttpResponse(form_error_stream(), content_type="text/event-stream")
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        fnol_text = form_data_to_fnol_text(fnol_form.cleaned_data)

    elif mode == "pdf":
        uploaded = request.FILES.get("pdf_file")
        if not uploaded:
            def no_file_stream():
                yield f"data: {json.dumps({'type': 'error', 'message': 'No file was uploaded.'})}\n\n"

            response = StreamingHttpResponse(no_file_stream(), content_type="text/event-stream")
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        try:
            fnol_text = extract_text_from_pdf(uploaded)
        except PDFExtractionError as exc:
            msg = str(exc)

            def pdf_error_stream():
                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"

            response = StreamingHttpResponse(pdf_error_stream(), content_type="text/event-stream")
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response

    else:
        fnol_text = request.POST.get("fnol_text", "")

    if len(fnol_text) > 50_000:
        def too_long_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Input exceeds 50,000 characters. Please shorten the submission.'})}\n\n"

        response = StreamingHttpResponse(too_long_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def event_stream():
        for item in stream_fnol(fnol_text):
            if isinstance(item, str):
                yield f"data: {json.dumps({'type': 'token', 'text': item})}\n\n"
            elif isinstance(item, dict):
                if item.get("error"):
                    yield f"data: {json.dumps({'type': 'error', 'message': item['message']})}\n\n"
                else:
                    html = render_to_string(
                        "triage/result.html",
                        {
                            "result": item,
                            "fnol_text": fnol_text,
                            "result_json": json.dumps(item),
                        },
                        request=request,
                    )
                    yield f"data: {json.dumps({'type': 'done', 'html': html})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_http_methods(["POST"])
def result_pdf(request):
    from weasyprint import HTML

    result_json = request.POST.get("result_json", "")
    fnol_text = request.POST.get("fnol_text", "")

    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, ValueError):
        return HttpResponse("Invalid result data.", status=400)

    html_string = render_to_string(
        "triage/result_pdf.html",
        {"result": result, "fnol_text": fnol_text},
        request=request,
    )

    pdf_bytes = BytesIO()
    HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf(pdf_bytes)
    pdf_bytes.seek(0)

    claim_type = result.get("claim_type", "triage").replace(" ", "_")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="claimsprint_{claim_type}.pdf"'
    return response


def _check_api_key(request):
    """Return a 401 JsonResponse if CLAIMSPRINT_API_KEY is set and the request does not match."""
    required = os.environ.get("CLAIMSPRINT_API_KEY", "")
    if not required:
        return None
    provided = request.headers.get("X-Api-Key", "")
    if provided != required:
        return JsonResponse({"error": True, "message": "Unauthorized."}, status=401)
    return None


@require_http_methods(["POST"])
def api_triage(request):
    auth_error = _check_api_key(request)
    if auth_error:
        return auth_error

    content_type = request.content_type or ""

    if "application/json" in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse(
                {"error": True, "message": "Request body is not valid JSON."},
                status=400,
            )
        if "text" in body:
            fnol_text = str(body["text"])
        elif "form_data" in body:
            fnol_text = form_data_to_fnol_text(body["form_data"])
        else:
            return JsonResponse(
                {"error": True, "message": "Provide 'text' or 'form_data' in the JSON body."},
                status=400,
            )
    else:
        if "file" in request.FILES:
            try:
                fnol_text = extract_text_from_pdf(request.FILES["file"])
            except PDFExtractionError as exc:
                return JsonResponse({"error": True, "message": str(exc)}, status=400)
        elif "text" in request.POST:
            fnol_text = request.POST["text"]
        elif "form_data" in request.POST:
            try:
                data = json.loads(request.POST["form_data"])
            except (json.JSONDecodeError, ValueError):
                return JsonResponse(
                    {"error": True, "message": "form_data must be a JSON-encoded object."},
                    status=400,
                )
            fnol_text = form_data_to_fnol_text(data)
        else:
            return JsonResponse(
                {"error": True, "message": "Provide 'text', 'form_data', or 'file' in the request."},
                status=400,
            )

    if not fnol_text or not fnol_text.strip():
        return JsonResponse({"error": True, "message": "No FNOL content provided."}, status=400)

    if len(fnol_text) > 50_000:
        return JsonResponse({"error": True, "message": "Input exceeds 50,000 characters."}, status=400)

    result = process_fnol(fnol_text)

    if result.get("error"):
        return JsonResponse(result, status=422)

    return JsonResponse(result)


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


@require_http_methods(["GET", "POST"])
def claim_types_settings_view(request):
    qs = ClaimType.objects.all()
    if request.method == "POST":
        formset = ClaimTypeFormSet(request.POST, queryset=qs)
        if formset.is_valid():
            formset.save()
            return redirect(reverse("settings_claim_types") + "?saved=1")
    else:
        formset = ClaimTypeFormSet(queryset=qs)
    return render(request, "triage/settings_claim_types.html", {
        "formset": formset,
        "saved": request.GET.get("saved") == "1",
    })


@require_http_methods(["GET", "POST"])
def handlers_settings_view(request):
    qs = Handler.objects.all().order_by("region", "name")
    if request.method == "POST":
        formset = HandlerFormSet(request.POST, queryset=qs)
        if formset.is_valid():
            formset.save()
            return redirect(reverse("settings_handlers") + "?saved=1")
    else:
        formset = HandlerFormSet(queryset=qs)
    return render(request, "triage/settings_handlers.html", {
        "formset": formset,
        "saved": request.GET.get("saved") == "1",
    })
