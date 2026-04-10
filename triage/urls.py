from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("stream/", views.triage_stream, name="triage_stream"),
    path("history/", views.history, name="history"),
    path("staff/", views.staff_directory, name="staff_directory"),
    path("settings/", views.settings_view, name="settings"),
]
