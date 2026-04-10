from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("staff/", views.staff_directory, name="staff_directory"),
    path("settings/", views.settings_view, name="settings"),
]
