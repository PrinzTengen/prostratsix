from __future__ import annotations

import pytest
from django.urls import reverse

from prostratsix.teams import services
from prostratsix.teams.models import InvitationStatus
from prostratsix.teams.models import Team
from prostratsix.teams.models import TeamMembership
from prostratsix.teams.models import TeamRole
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


def make_user(**kwargs):
    """A user whose profile is completed, so ProfileCompletionMiddleware
    does not redirect them away from team pages."""
    return UserFactory(profile_completed=True, **kwargs)


@pytest.fixture
def owner():
    return make_user()


@pytest.fixture
def team(owner):
    return services.create_team(owner, name="Alpha")


class TestCreateView:
    def test_create_team_via_view(self, client):
        user = make_user()
        client.force_login(user)
        response = client.post(reverse("teams:create"), {"name": "Bravo"})
        assert response.status_code == 302
        team = Team.objects.get(name="Bravo")
        assert team.owner == user

    def test_user_with_team_redirected(self, client, team):
        client.force_login(team.owner)
        response = client.get(reverse("teams:create"))
        assert response.status_code == 302
        assert response.url == team.get_absolute_url()


class TestDetailAndInvite:
    def test_detail_is_public(self, client, team):
        response = client.get(team.get_absolute_url())
        assert response.status_code == 200

    def test_owner_can_invite_via_view(self, client, team):
        target = make_user()
        client.force_login(team.owner)
        response = client.post(
            reverse("teams:invite", kwargs={"slug": team.slug}),
            {"username_or_email": target.username},
        )
        assert response.status_code == 302
        assert team.invitations.filter(
            invited_user=target,
            status=InvitationStatus.PENDING,
        ).exists()

    def test_non_manager_cannot_invite_via_view(self, client, team):
        outsider = make_user()
        target = make_user()
        client.force_login(outsider)
        response = client.post(
            reverse("teams:invite", kwargs={"slug": team.slug}),
            {"username_or_email": target.username},
        )
        assert response.status_code == 302
        assert not team.invitations.exists()


class TestInvitationFlow:
    def test_accept_invitation_via_view(self, client, team):
        target = make_user()
        invitation = services.invite_member(team, team.owner, target)
        client.force_login(target)
        response = client.post(
            reverse("teams:invitation_accept", kwargs={"token": invitation.token}),
        )
        assert response.status_code == 302
        assert TeamMembership.objects.filter(
            user=target,
            team=team,
            role=TeamRole.PLAYER,
        ).exists()

    def test_invitation_detail_hidden_from_other_user(self, client, team):
        target = make_user()
        invitation = services.invite_member(team, team.owner, target)
        client.force_login(make_user())
        response = client.get(
            reverse("teams:invitation_detail", kwargs={"token": invitation.token}),
        )
        assert response.status_code == 404

    def test_leave_team_via_view(self, client, team):
        target = make_user()
        invitation = services.invite_member(team, team.owner, target)
        services.accept_invitation(invitation, target)
        client.force_login(target)
        response = client.post(reverse("teams:leave"))
        assert response.status_code == 302
        assert not TeamMembership.objects.filter(user=target).exists()
