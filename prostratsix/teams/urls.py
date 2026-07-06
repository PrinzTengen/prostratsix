from django.urls import path

from . import views

app_name = "teams"
urlpatterns = [
    path("", views.team_list_view, name="list"),
    path("create/", views.team_create_view, name="create"),
    path("leave/", views.leave_team_view, name="leave"),
    path(
        "invitations/<uuid:token>/",
        views.invitation_detail_view,
        name="invitation_detail",
    ),
    path(
        "invitations/<uuid:token>/accept/",
        views.invitation_accept_view,
        name="invitation_accept",
    ),
    path(
        "invitations/<uuid:token>/decline/",
        views.invitation_decline_view,
        name="invitation_decline",
    ),
    path("<slug:slug>/", views.team_detail_view, name="detail"),
    path("<slug:slug>/edit/", views.team_update_view, name="update"),
    path("<slug:slug>/invite/", views.team_invite_view, name="invite"),
    path(
        "<slug:slug>/invitations/<int:pk>/revoke/",
        views.revoke_invitation_view,
        name="revoke_invitation",
    ),
    path(
        "<slug:slug>/members/<int:pk>/remove/",
        views.remove_member_view,
        name="remove_member",
    ),
    path(
        "<slug:slug>/transfer/<int:pk>/",
        views.transfer_ownership_view,
        name="transfer_ownership",
    ),
    path("<slug:slug>/delete/", views.delete_team_view, name="delete"),
]
