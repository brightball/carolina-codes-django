from django.urls import path

from catalog import views

handler404 = "catalog.views.not_found"

urlpatterns = [
    path("", views.identity),
    path("health", views.health),
    path("v1/years", views.years),
    path("v1/speakers", views.speakers),
    path("v1/speakers/<int:year>/<slug:slug>", views.speaker_year),
    path("v1/speakers/<slug:slug>", views.speaker_detail),
    path("v1/sponsors", views.sponsors),
    path("v1/sponsors/<int:year>/<slug:slug>", views.sponsor_year),
    path("v1/sponsors/<slug:slug>", views.sponsor_detail),
]
