from __future__ import annotations

import pytest
from django.urls import reverse

from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.teams import services
from prostratsix.teams.models import InvitationStatus
from prostratsix.teams.models import TeamMembership
from prostratsix.teams.models import TeamRole
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def owner():
    return UserFactory()


@pytest.fixture
def team(owner):
    return services.create_team(owner, name="Alpha")


class TestCreateTeam:
    def test_creates_team_with_owner_membership(self, owner):
        team = services.create_team(owner, name="Alpha")
        assert team.slug == "alpha"
        assert team.owner == owner
        assert owner.team_membership.role == TeamRole.OWNER

    def test_rejects_user_already_in_team(self, team):
        with pytest.raises(services.AlreadyInTeamError):
            services.create_team(team.owner, name="Beta")


class TestInviteMember:
    def test_owner_can_invite_and_notifies(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        assert invitation.status == InvitationStatus.PENDING
        assert Notification.objects.filter(
            recipient=target,
            category=NotificationCategory.TEAM,
        ).exists()

    def test_invite_notification_carries_accept_decline_actions(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)

        notification = Notification.objects.get(recipient=target)
        actions = {action["label"]: action["url"] for action in notification.actions}
        assert actions["Accept"] == reverse(
            "teams:invitation_accept",
            kwargs={"token": invitation.token},
        )
        assert actions["Decline"] == reverse(
            "teams:invitation_decline",
            kwargs={"token": invitation.token},
        )

    def test_non_manager_cannot_invite(self, team):
        outsider = UserFactory()
        target = UserFactory()
        with pytest.raises(services.TeamPermissionError):
            services.invite_member(team, outsider, target)

    def test_cannot_invite_user_already_in_team(self, team):
        other_team = services.create_team(UserFactory(), name="Beta")
        with pytest.raises(services.AlreadyInTeamError):
            services.invite_member(team, team.owner, other_team.owner)

    def test_cannot_invite_twice(self, team):
        target = UserFactory()
        services.invite_member(team, team.owner, target)
        with pytest.raises(services.InvitationError):
            services.invite_member(team, team.owner, target)


class TestAcceptDecline:
    def test_accept_creates_membership(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        membership = services.accept_invitation(invitation, target)
        assert membership.role == TeamRole.PLAYER
        invitation.refresh_from_db()
        assert invitation.status == InvitationStatus.ACCEPTED

    def test_wrong_user_cannot_accept(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        with pytest.raises(services.TeamPermissionError):
            services.accept_invitation(invitation, UserFactory())

    def test_expired_invitation_rejected(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        invitation.expires_at = invitation.created_at.replace(year=2000)
        invitation.save(update_fields=["expires_at"])
        with pytest.raises(services.InvitationError):
            services.accept_invitation(invitation, target)
        invitation.refresh_from_db()
        assert invitation.status == InvitationStatus.EXPIRED

    def test_decline_sets_status(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        services.decline_invitation(invitation, target)
        invitation.refresh_from_db()
        assert invitation.status == InvitationStatus.DECLINED
        assert not TeamMembership.objects.filter(user=target).exists()


class TestMembershipLifecycle:
    def _member(self, team):
        target = UserFactory()
        invitation = services.invite_member(team, team.owner, target)
        services.accept_invitation(invitation, target)
        return target

    def test_remove_member(self, team):
        member = self._member(team)
        services.remove_member(team, team.owner, member)
        assert not TeamMembership.objects.filter(user=member).exists()

    def test_cannot_remove_owner(self, team):
        with pytest.raises(services.TeamPermissionError):
            services.remove_member(team, team.owner, team.owner)

    def test_owner_cannot_leave(self, team):
        with pytest.raises(services.TeamPermissionError):
            services.leave_team(team.owner)

    def test_member_can_leave(self, team):
        member = self._member(team)
        services.leave_team(member)
        assert not TeamMembership.objects.filter(user=member).exists()

    def test_transfer_ownership(self, team):
        member = self._member(team)
        old_owner = team.owner
        services.transfer_ownership(team, old_owner, member)
        member.team_membership.refresh_from_db()
        old_owner.team_membership.refresh_from_db()
        assert member.team_membership.role == TeamRole.OWNER
        assert old_owner.team_membership.role == TeamRole.MANAGER

    def test_delete_team_only_owner(self, team):
        member = self._member(team)
        with pytest.raises(services.TeamPermissionError):
            services.delete_team(team, member)
