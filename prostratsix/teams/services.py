from __future__ import annotations

from django.contrib.sites.models import Site
from django.db import transaction
from django.urls import NoReverseMatch
from django.urls import reverse

from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.services import notify

from .models import InvitationStatus
from .models import Team
from .models import TeamInvitation
from .models import TeamMembership
from .models import TeamRole


class TeamError(Exception):
    """Base class for team service errors."""


class TeamPermissionError(TeamError):
    """Raised when the acting user lacks the required rights."""


class AlreadyInTeamError(TeamError):
    """Raised when a user is already a member of a team."""


class InvitationError(TeamError):
    """Raised for invalid invitation state transitions."""


def get_user_team(user) -> Team | None:
    """Return the team the user belongs to, or ``None``."""
    membership = get_user_membership(user)
    return membership.team if membership else None


def get_user_membership(user) -> TeamMembership | None:
    """Return the user's :class:`TeamMembership`, or ``None``."""
    try:
        return user.team_membership
    except TeamMembership.DoesNotExist:
        return None


def _absolute_url(get_path) -> str:
    """Build an absolute URL from a ``reverse``-based callable.

    Returns an empty string while the ``teams`` URL routes are not yet
    registered, so notifications can still be created without them.
    """
    try:
        path = get_path()
    except NoReverseMatch:
        return ""
    site = Site.objects.get_current()
    return f"https://{site.domain}{path}"


@transaction.atomic
def create_team(owner, **fields) -> Team:
    """Create a team and make ``owner`` its owner."""
    if get_user_membership(owner) is not None:
        msg = "You are already a member of a team."
        raise AlreadyInTeamError(msg)

    team = Team.objects.create(**fields)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    return team


@transaction.atomic
def invite_member(team: Team, actor, target_user) -> TeamInvitation:
    """Invite a registered user to ``team`` and notify them."""
    if not team.has_management_rights(actor):
        msg = "You do not have permission to invite members to this team."
        raise TeamPermissionError(msg)

    if get_user_membership(target_user) is not None:
        msg = "This user is already a member of a team."
        raise AlreadyInTeamError(msg)

    already_pending = team.invitations.filter(
        invited_user=target_user,
        status=InvitationStatus.PENDING,
    ).exists()
    if already_pending:
        msg = "This user already has a pending invitation to the team."
        raise InvitationError(msg)

    invitation = TeamInvitation.objects.create(
        team=team,
        invited_user=target_user,
        invited_by=actor,
    )

    notify(
        target_user,
        title=f"You've been invited to {team.name}",
        message=f"{actor} invited you to join {team.name}. "
        f"Accept or decline the invitation to continue.",
        level=NotificationLevel.INFO,
        category=NotificationCategory.TEAM,
        url=_absolute_url(invitation.get_absolute_url),
        actions=[
            {
                "label": "Accept",
                "url": reverse(
                    "teams:invitation_accept",
                    kwargs={"token": invitation.token},
                ),
            },
            {
                "label": "Decline",
                "url": reverse(
                    "teams:invitation_decline",
                    kwargs={"token": invitation.token},
                ),
            },
        ],
    )
    return invitation


def accept_invitation(invitation: TeamInvitation, user) -> TeamMembership:
    """Accept a pending invitation and join the team."""
    # Guarding runs outside the transaction so an expiry status update is
    # committed even though the guard then raises.
    _guard_invitation_actionable(invitation, user)

    if get_user_membership(user) is not None:
        msg = "You are already a member of a team."
        raise AlreadyInTeamError(msg)

    with transaction.atomic():
        membership = TeamMembership.objects.create(
            team=invitation.team,
            user=user,
            role=TeamRole.PLAYER,
        )
        invitation.status = InvitationStatus.ACCEPTED
        invitation.save(update_fields=["status"])

    owner = invitation.team.owner
    if owner is not None:
        notify(
            owner,
            title=f"{user} joined {invitation.team.name}",
            message=f"{user} accepted the invitation to {invitation.team.name}.",
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.TEAM,
            url=_absolute_url(invitation.team.get_absolute_url),
        )
    return membership


def decline_invitation(invitation: TeamInvitation, user) -> TeamInvitation:
    """Decline a pending invitation."""
    _guard_invitation_actionable(invitation, user)

    invitation.status = InvitationStatus.DECLINED
    invitation.save(update_fields=["status"])

    if invitation.invited_by is not None:
        notify(
            invitation.invited_by,
            title=f"{user} declined your invitation",
            message=f"{user} declined the invitation to {invitation.team.name}.",
            level=NotificationLevel.INFO,
            category=NotificationCategory.TEAM,
        )
    return invitation


@transaction.atomic
def revoke_invitation(invitation: TeamInvitation, actor) -> TeamInvitation:
    """Revoke a pending invitation (management action)."""
    if not invitation.team.has_management_rights(actor):
        msg = "You do not have permission to revoke this invitation."
        raise TeamPermissionError(msg)
    if invitation.status != InvitationStatus.PENDING:
        msg = "This invitation is no longer pending."
        raise InvitationError(msg)

    invitation.status = InvitationStatus.REVOKED
    invitation.save(update_fields=["status"])
    return invitation


@transaction.atomic
def remove_member(team: Team, actor, target_user) -> None:
    """Remove a member from the team (management action)."""
    if not team.has_management_rights(actor):
        msg = "You do not have permission to remove members from this team."
        raise TeamPermissionError(msg)

    membership = get_user_membership(target_user)
    if membership is None or membership.team_id != team.id:
        msg = "This user is not a member of the team."
        raise TeamError(msg)
    if membership.role == TeamRole.OWNER:
        msg = "The team owner cannot be removed. Transfer ownership first."
        raise TeamPermissionError(msg)

    membership.delete()
    notify(
        target_user,
        title=f"You were removed from {team.name}",
        message=f"You are no longer a member of {team.name}.",
        level=NotificationLevel.WARNING,
        category=NotificationCategory.TEAM,
    )


@transaction.atomic
def leave_team(user) -> None:
    """Leave the current team. The owner must transfer ownership first."""
    membership = get_user_membership(user)
    if membership is None:
        msg = "You are not a member of any team."
        raise TeamError(msg)
    if membership.role == TeamRole.OWNER:
        msg = "As the owner you must transfer ownership before leaving."
        raise TeamPermissionError(msg)

    team = membership.team
    membership.delete()

    owner = team.owner
    if owner is not None:
        notify(
            owner,
            title=f"{user} left {team.name}",
            message=f"{user} is no longer a member of {team.name}.",
            level=NotificationLevel.INFO,
            category=NotificationCategory.TEAM,
        )


@transaction.atomic
def transfer_ownership(team: Team, actor, new_owner) -> None:
    """Transfer ownership of ``team`` from ``actor`` to ``new_owner``."""
    actor_membership = get_user_membership(actor)
    if (
        actor_membership is None
        or actor_membership.team_id != team.id
        or actor_membership.role != TeamRole.OWNER
    ):
        msg = "Only the current owner can transfer ownership."
        raise TeamPermissionError(msg)

    new_membership = get_user_membership(new_owner)
    if new_membership is None or new_membership.team_id != team.id:
        msg = "The new owner must be a member of the team."
        raise TeamError(msg)

    new_membership.role = TeamRole.OWNER
    new_membership.save(update_fields=["role"])
    actor_membership.role = TeamRole.MANAGER
    actor_membership.save(update_fields=["role"])

    notify(
        new_owner,
        title=f"You are now the owner of {team.name}",
        message=f"{actor} transferred ownership of {team.name} to you.",
        level=NotificationLevel.SUCCESS,
        category=NotificationCategory.TEAM,
        url=_absolute_url(team.get_absolute_url),
    )


@transaction.atomic
def delete_team(team: Team, actor) -> None:
    """Delete a team. Only the owner may do this."""
    if team.owner != actor:
        msg = "Only the team owner can delete the team."
        raise TeamPermissionError(msg)
    team.delete()


def _guard_invitation_actionable(invitation: TeamInvitation, user) -> None:
    """Validate that ``user`` may act on the pending ``invitation``."""
    if invitation.invited_user_id != user.id:
        msg = "This invitation was not addressed to you."
        raise TeamPermissionError(msg)
    if invitation.status != InvitationStatus.PENDING:
        msg = "This invitation is no longer pending."
        raise InvitationError(msg)
    if invitation.is_expired:
        invitation.status = InvitationStatus.EXPIRED
        invitation.save(update_fields=["status"])
        msg = "This invitation has expired."
        raise InvitationError(msg)
