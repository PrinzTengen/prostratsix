from django.urls import path

from . import views

app_name = "leagues"
urlpatterns = [
    path("", views.league_list_view, name="list"),
    path("<slug:slug>/", views.league_detail_view, name="detail"),
    path("<slug:slug>/register/", views.league_register_view, name="register"),
    path("<slug:slug>/withdraw/", views.league_withdraw_view, name="withdraw"),
]
