from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("stream/", views.triage_stream, name="triage_stream"),
    path("result/pdf/", views.result_pdf, name="result_pdf"),
    path("history/", views.history, name="history"),
    path("staff/", views.staff_directory, name="staff_directory"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/claim-types/", views.claim_types_settings_view, name="settings_claim_types"),
    path("settings/handlers/", views.handlers_settings_view, name="settings_handlers"),
    path("api/triage/", views.api_triage, name="api_triage"),
]
