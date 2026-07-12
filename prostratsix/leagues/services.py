from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.sites.models import Site
from django.db import transaction
from django.urls import NoReverseMatch
from django.utils.translation import gettext_lazy as _

from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.services import notify_many
from prostratsix.teams.models import TeamMembership
from prostratsix.teams.services import get_user_team

from .models import League
from .models import LeagueRegistration
from .models import LeagueRosterEntry
from .models import LeagueStatus
from .models import RegistrationStatus

if TYPE_CHECKING:
    from prostratsix.teams.models import Team

# A league in one of these states is done being scheduled: it can no longer be
# reopened, closed, started, or withdrawn from.
TERMINAL_STATUSES = (LeagueStatus.FINISHED, LeagueStatus.CANCELLED)


class LeagueError(Exception):
    """Base class for league service errors."""


class LeaguePermissionError(LeagueError):
    """Raised when the acting user lacks management rights for the team."""


class RegistrationClosedError(LeagueError):
    """Raised when the league's registration window is not open."""


class LeagueFullError(LeagueError):
    """Raised when the league has no free slots left."""


class RosterTooSmallError(LeagueError):
    """Raised when the team has fewer members than ``min_roster_size``."""


class AlreadyRegisteredError(LeagueError):
    """Raised when the team already holds a confirmed registration."""


class NotRegisteredError(LeagueError):
    """Raised when the team has no confirmed registration to act on."""


# NOTE: duplicated from prostratsix/teams/services.py — same function, same
# docstring. Both belong in a shared prostratsix/core/utils.py; pulling them
# together means touching the teams app, which is out of scope here. Keep the
# two in sync until then.
def _absolute_url(get_path) -> str:
    """Build an absolute URL from a ``reverse``-based callable.

    Returns an empty string while the ``leagues`` URL routes are not yet
    registered, so notifications can still be created without them.
    """
    try:
        path = get_path()
    except NoReverseMatch:
        return ""
    site = Site.objects.get_current()
    return f"https://{site.domain}{path}"


def get_team_registration(league: League, team: Team) -> LeagueRegistration | None:
    """Return the team's confirmed registration for ``league``, or ``None``.

    Withdrawn and disqualified rows are history and never returned here — the
    question this answers is "is this team in the league right now?".
    """
    return league.registrations.filter(
        team=team,
        status=RegistrationStatus.CONFIRMED,
    ).first()


def get_user_registration(league: League, user) -> LeagueRegistration | None:
    """Return the confirmed registration of the user's team, or ``None``.

    Convenience for the views and templates: a user without a team has no
    registration either.
    """
    team = get_user_team(user)
    if team is None:
        return None
    return get_team_registration(league, team)


def register_team(league: League, actor, team: Team) -> LeagueRegistration:
    """Register ``team`` for ``league`` and freeze its current roster.

    Every member of the team is notified once the registration is committed.

    Raises:
        LeaguePermissionError: ``actor`` has no management rights for ``team``.
            Checked first, before the league is even looked at, so an outsider
            cannot probe a league's status or capacity through the error he
            gets back.
        RegistrationClosedError: the league is not accepting registrations —
            wrong status, or ``registration_opens_at``/``registration_closes_at``
            puts it outside the window.
        AlreadyRegisteredError: the team already holds a confirmed registration.
        RosterTooSmallError: the team has fewer than ``league.min_roster_size``
            members (checked with ``>=``, so exactly the minimum is fine).
        LeagueFullError: all ``max_teams`` slots are taken. Deliberately its own
            error rather than a RegistrationClosedError: "the league is full" and
            "registration has closed" call for different things from the user —
            wait for a withdrawal versus give up.
    """
    if not team.has_management_rights(actor):
        msg = _("You do not have permission to register this team.")
        raise LeaguePermissionError(msg)

    with transaction.atomic():
        # Serialises every concurrent registration for THIS league. Without the
        # lock two teams clicking on the last free slot at the same time both
        # pass the is_full check and overbook the league; a UniqueConstraint
        # cannot catch that, because this is about a count, not a duplicate.
        #
        # How long the lock is actually held depends on the caller, and that is
        # worth knowing: the project sets ATOMIC_REQUESTS = True
        # (config/settings/base.py), so inside a request this atomic() block is
        # only a savepoint, and PostgreSQL releases row locks when the OUTERMOST
        # transaction commits — at the end of the request, template rendering
        # included, not at the end of this block. Called outside a request (Celery
        # task, management command, shell) this block is the outermost transaction
        # and the lock ends with it. Either way nothing slow (e-mail, HTTP, Celery
        # enqueue) belongs in here, which is what the on_commit below is for.
        #
        # On SQLite select_for_update() is a silent no-op, see
        # docs/plans/research/phase-2-leagues.md §2.1.
        league = League.objects.select_for_update().get(pk=league.pk)

        if not league.is_registration_window_open:
            msg = _("Registration for %(league)s is not open.") % {
                "league": league.name,
            }
            raise RegistrationClosedError(msg)

        if get_team_registration(league, team) is not None:
            msg = _("%(team)s is already registered for %(league)s.") % {
                "team": team.name,
                "league": league.name,
            }
            raise AlreadyRegisteredError(msg)

        members = list(
            TeamMembership.objects.filter(team=team).select_related("user"),
        )
        if len(members) < league.min_roster_size:
            msg = _(
                "%(league)s requires at least %(required)d players, "
                "%(team)s has %(actual)d.",
            ) % {
                "league": league.name,
                "required": league.min_roster_size,
                "team": team.name,
                "actual": len(members),
            }
            raise RosterTooSmallError(msg)

        # The re-check after the lock is the actual protection: whoever got here
        # first has committed by now, and READ COMMITTED hands us their result.
        if league.is_full:
            msg = _("%(league)s is full.") % {"league": league.name}
            raise LeagueFullError(msg)

        registration = LeagueRegistration.objects.create(
            league=league,
            team=team,
            registered_by=actor,
            status=RegistrationStatus.CONFIRMED,
        )
        LeagueRosterEntry.objects.bulk_create(
            [
                LeagueRosterEntry(
                    registration=registration,
                    user=membership.user,
                    role=membership.role,
                )
                for membership in members
            ],
        )

        # Fully loaded User objects, never a .only()/.defer() queryset —
        # notify_many() reads user.email and would go N+1 on a deferred field.
        players = [membership.user for membership in members]
        title = _("Your team is competing in %(league)s") % {"league": league.name}
        message = _("%(team)s has been registered for %(league)s.") % {
            "team": team.name,
            "league": league.name,
        }
        # Outside the lock, after the commit: the notification inserts and the
        # Celery enqueues must not stretch the League row lock, and a worker that
        # picked the e-mail task up before this transaction committed would not
        # see the notification row at all — the mail would be lost in silence.
        transaction.on_commit(
            lambda: notify_many(
                players,
                title=title,
                message=message,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.LEAGUE,
                url=_absolute_url(league.get_absolute_url),
            ),
        )

    return registration


def withdraw_team(league: League, actor, team: Team) -> LeagueRegistration:
    """Withdraw ``team`` from ``league`` and notify its players.

    The roster snapshot stays behind as history — the registration only flips to
    ``WITHDRAWN``, freeing the slot and letting the team register again later.

    Raises:
        LeaguePermissionError: ``actor`` has no management rights for ``team``
            (checked first, same reasoning as in :func:`register_team`).
        LeagueError: the league is already running or over — a team cannot walk
            out of a season that has started.
        NotRegisteredError: the team holds no confirmed registration.
    """
    if not team.has_management_rights(actor):
        msg = _("You do not have permission to withdraw this team.")
        raise LeaguePermissionError(msg)

    with transaction.atomic():
        # Same row lock as register_team(): a withdrawal frees a slot, so it has
        # to be serialised against the registrations competing for one.
        league = League.objects.select_for_update().get(pk=league.pk)

        if league.status == LeagueStatus.RUNNING or league.status in TERMINAL_STATUSES:
            msg = _("%(league)s has already started, teams can no longer withdraw.") % {
                "league": league.name,
            }
            raise LeagueError(msg)

        registration = get_team_registration(league, team)
        if registration is None:
            msg = _("%(team)s is not registered for %(league)s.") % {
                "team": team.name,
                "league": league.name,
            }
            raise NotRegisteredError(msg)

        registration.status = RegistrationStatus.WITHDRAWN
        registration.save(update_fields=["status"])

        players = _roster_players(registration)
        title = _("Your team withdrew from %(league)s") % {"league": league.name}
        message = _("%(team)s is no longer taking part in %(league)s.") % {
            "team": team.name,
            "league": league.name,
        }
        # See register_team() for why this runs after the commit and not inside
        # the lock.
        transaction.on_commit(
            lambda: notify_many(
                players,
                title=title,
                message=message,
                level=NotificationLevel.WARNING,
                category=NotificationCategory.LEAGUE,
                url=_absolute_url(league.get_absolute_url),
            ),
        )

    return registration


def open_registration(league: League) -> League:
    """Open the league for registrations.

    Idempotent: a league that is already open is returned untouched.

    Raises:
        LeagueError: the league is running, finished or cancelled.
    """
    return _transition(league, LeagueStatus.REGISTRATION_OPEN)


def close_registration(league: League) -> League:
    """Close the league for registrations.

    Idempotent: a league that is already closed is returned untouched.

    Raises:
        LeagueError: the league is running, finished or cancelled.
    """
    return _transition(league, LeagueStatus.REGISTRATION_CLOSED)


def start_league(league: League) -> League:
    """Start the league and notify every player of every registered team.

    Idempotent, and that is the whole point: two overlapping runs of the Celery
    task that starts due leagues must not start one twice and send every player
    a second round of notifications. The status is therefore re-checked *after*
    the row lock, not before — a check outside the lock is exactly the race it is
    supposed to prevent.

    Raises:
        LeagueError: the league is finished or cancelled.
    """
    with transaction.atomic():
        league = League.objects.select_for_update().get(pk=league.pk)

        if league.status == LeagueStatus.RUNNING:
            # Someone got here first. Not an error — just nothing left to do.
            return league
        if league.status in TERMINAL_STATUSES:
            msg = _("%(league)s is %(status)s and cannot be started.") % {
                "league": league.name,
                "status": league.get_status_display().lower(),
            }
            raise LeagueError(msg)

        league.status = LeagueStatus.RUNNING
        league.save(update_fields=["status"])

        players = _confirmed_players(league)
        title = _("%(league)s has started") % {"league": league.name}
        message = _("The league %(league)s is now running. Good luck!") % {
            "league": league.name,
        }
        # See register_team() for why this runs after the commit and not inside
        # the lock.
        transaction.on_commit(
            lambda: notify_many(
                players,
                title=title,
                message=message,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.LEAGUE,
                url=_absolute_url(league.get_absolute_url),
            ),
        )

    return league


def _transition(league: League, target: str) -> League:
    """Move ``league`` to ``target``, under the same row lock as the rest."""
    with transaction.atomic():
        league = League.objects.select_for_update().get(pk=league.pk)

        if league.status == target:
            return league
        if league.status == LeagueStatus.RUNNING or league.status in TERMINAL_STATUSES:
            msg = _(
                "%(league)s is %(status)s, its registration cannot be changed.",
            ) % {
                "league": league.name,
                "status": league.get_status_display().lower(),
            }
            raise LeagueError(msg)

        league.status = target
        league.save(update_fields=["status"])

    return league


def _roster_players(registration: LeagueRegistration) -> list:
    """Return the users on a registration's roster snapshot."""
    entries = registration.roster.select_related("user").order_by("id")
    return [entry.user for entry in entries]


def _confirmed_players(league: League) -> list:
    """Return every player on the roster of every confirmed registration.

    Deduplicated by user: the same person can appear on two snapshots after a
    team switch, and nobody wants the league-start notification twice.
    """
    entries = (
        LeagueRosterEntry.objects.filter(
            registration__league=league,
            registration__status=RegistrationStatus.CONFIRMED,
        )
        .select_related("user")
        .order_by("id")
    )
    players: dict[int, object] = {}
    for entry in entries:
        players.setdefault(entry.user_id, entry.user)
    return list(players.values())
