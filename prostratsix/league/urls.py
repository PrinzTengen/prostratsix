from django.urls import path

from . import views

app_name = "league"
urlpatterns = [
    path("", views.season_list_view, name="season_list"),
    path("registrations/", views.registration_queue_view, name="registration_queue"),
    path(
        "registrations/<int:pk>/approve/",
        views.registration_approve_view,
        name="registration_approve",
    ),
    path(
        "registrations/<int:pk>/reject/",
        views.registration_reject_view,
        name="registration_reject",
    ),
    path(
        "registrations/<int:pk>/withdraw/",
        views.registration_withdraw_view,
        name="registration_withdraw",
    ),
    path("match/<int:pk>/", views.match_detail_view, name="match_detail"),
    path("match/<int:pk>/report/", views.match_report_view, name="match_report"),
    path("match/<int:pk>/confirm/", views.match_confirm_view, name="match_confirm"),
    path("match/<int:pk>/dispute/", views.match_dispute_view, name="match_dispute"),
    path(
        "match/<int:pk>/resolve/",
        views.match_resolve_dispute_view,
        name="match_resolve_dispute",
    ),
    path(
        "match/<int:pk>/forfeit/<int:team_pk>/",
        views.match_forfeit_view,
        name="match_forfeit",
    ),
    path("match/<int:pk>/cancel/", views.match_cancel_view, name="match_cancel"),
    path("<slug:slug>/", views.season_detail_view, name="season_detail"),
    path("<slug:slug>/register/", views.season_register_view, name="season_register"),
    path("<slug:slug>/schedule/", views.season_schedule_view, name="season_schedule"),
    path(
        "<slug:slug>/schedule/generate/",
        views.season_generate_schedule_view,
        name="season_generate_schedule",
    ),
    path(
        "<slug:slug>/standings/",
        views.season_standings_view,
        name="season_standings",
    ),
]
