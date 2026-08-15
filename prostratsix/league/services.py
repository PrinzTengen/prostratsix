from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import transaction
from django.urls import NoReverseMatch
from django.utils import timezone

from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.services import notify
from prostratsix.teams.services import get_user_team
from prostratsix.users.models import User

from .models import Match
from .models import MatchStage
from .models import MatchStatus
from .models import RegistrationStatus
from .models import Season
from .models import SeasonRegistration
from .models import SeasonStatus

if TYPE_CHECKING:
    from prostratsix.teams.models import Team


class LeagueError(Exception):
    """Base class for league service errors."""


class LeaguePermissionError(LeagueError):
    """Raised when the acting user lacks the required rights."""


class MatchError(LeagueError):
    """Raised for invalid match state transitions."""


class MatchPermissionError(MatchError):
    """Raised when the acting user lacks the required rights over a match."""


def _absolute_url(get_path) -> str:
    """Build an absolute URL from a ``reverse``-based callable.

    Mirrors ``teams.services._absolute_url`` so notifications degrade
    gracefully while ``league`` URL routes are not yet registered.
    """
    try:
        path = get_path()
    except NoReverseMatch:
        return ""
    site = Site.objects.get_current()
    return f"{settings.DJANGO_SITE_SCHEME}://{site.domain}{path}"


def _notify_managers(team, **kwargs) -> None:
    for manager in team.managers:
        notify(manager, **kwargs)


def _score_line(
    match: Match, score_home: int | None = None, score_away: int | None = None,
) -> str:
    home_score = match.score_home if score_home is None else score_home
    away_score = match.score_away if score_away is None else score_away
    return f"{match.home_team} {home_score}:{away_score} {match.away_team}"


# ---------------------------------------------------------------------------
# Season registration
# ---------------------------------------------------------------------------


@transaction.atomic
def register_team_for_season(season: Season, team, user) -> SeasonRegistration:
    """Register ``team`` for ``season``. Requires management rights on the team."""
    if not team.has_management_rights(user):
        msg = "You do not have permission to register this team."
        raise LeaguePermissionError(msg)
    if season.status != SeasonStatus.REGISTRATION_OPEN:
        msg = "Registration is not open for this season."
        raise LeagueError(msg)
    if (
        season.registrations.filter(team=team)
        .exclude(
            status=RegistrationStatus.WITHDRAWN,
        )
        .exists()
    ):
        msg = "This team is already registered for the season."
        raise LeagueError(msg)
    open_slots = (
        season.max_teams
        - season.registrations.filter(
            status__in=[RegistrationStatus.PENDING, RegistrationStatus.APPROVED],
        ).count()
    )
    if open_slots <= 0:
        msg = "This season has no open slots left."
        raise LeagueError(msg)

    registration = SeasonRegistration.objects.create(
        season=season,
        team=team,
        requested_by=user,
    )

    for staff in User.objects.filter(is_staff=True):
        notify(
            staff,
            title=f"New registration: {team.name} for {season.name}",
            message=f"{team.name} requested to join {season.name}.",
            level=NotificationLevel.INFO,
            category=NotificationCategory.LEAGUE,
            url=_absolute_url(season.get_absolute_url),
        )
    return registration


@transaction.atomic
def approve_registration(
    registration: SeasonRegistration,
    decided_by,
) -> SeasonRegistration:
    """Approve a pending registration. Requires ``decided_by.is_staff``."""
    if not decided_by.is_staff:
        msg = "You do not have permission to approve registrations."
        raise LeaguePermissionError(msg)
    if registration.status != RegistrationStatus.PENDING:
        msg = "This registration is no longer pending."
        raise LeagueError(msg)

    registration.status = RegistrationStatus.APPROVED
    registration.decided_by = decided_by
    registration.decided_at = timezone.now()
    registration.save(update_fields=["status", "decided_by", "decided_at"])

    _notify_managers(
        registration.team,
        title=f"{registration.team.name} is registered for {registration.season.name}",
        message=f"Your registration for {registration.season.name} was approved.",
        level=NotificationLevel.SUCCESS,
        category=NotificationCategory.LEAGUE,
        url=_absolute_url(registration.season.get_absolute_url),
    )
    return registration


@transaction.atomic
def reject_registration(
    registration: SeasonRegistration,
    decided_by,
    reason: str = "",
) -> SeasonRegistration:
    """Reject a pending registration. Requires ``decided_by.is_staff``."""
    if not decided_by.is_staff:
        msg = "You do not have permission to reject registrations."
        raise LeaguePermissionError(msg)
    if registration.status != RegistrationStatus.PENDING:
        msg = "This registration is no longer pending."
        raise LeagueError(msg)

    registration.status = RegistrationStatus.REJECTED
    registration.decided_by = decided_by
    registration.decided_at = timezone.now()
    registration.save(update_fields=["status", "decided_by", "decided_at"])

    message = f"Your registration for {registration.season.name} was rejected."
    if reason:
        message += f" Reason: {reason}"
    _notify_managers(
        registration.team,
        title=f"Registration rejected for {registration.season.name}",
        message=message,
        level=NotificationLevel.WARNING,
        category=NotificationCategory.LEAGUE,
    )
    return registration


def withdraw_registration(registration: SeasonRegistration, user) -> SeasonRegistration:
    """Withdraw a pending or approved registration (team management action)."""
    if not registration.team.has_management_rights(user):
        msg = "You do not have permission to withdraw this registration."
        raise LeaguePermissionError(msg)
    if registration.status not in (
        RegistrationStatus.PENDING,
        RegistrationStatus.APPROVED,
    ):
        msg = "This registration cannot be withdrawn."
        raise LeagueError(msg)

    registration.status = RegistrationStatus.WITHDRAWN
    registration.save(update_fields=["status"])
    return registration


# ---------------------------------------------------------------------------
# Schedule generation (circle-method round-robin)
# ---------------------------------------------------------------------------


def _round_robin_pairs(teams: list) -> list[list[tuple]]:
    """Circle-method round-robin: returns one list of (home, away) pairs per round.

    Odd team counts get a ``None`` bye slot that produces no match.
    """
    roster = list(teams)
    if len(roster) % 2 == 1:
        roster.append(None)
    n = len(roster)
    fixed, rotating = roster[0], roster[1:]

    rounds = []
    for round_index in range(n - 1):
        round_teams = [fixed, *rotating]
        pairs = []
        for i in range(n // 2):
            t1, t2 = round_teams[i], round_teams[n - 1 - i]
            if t1 is None or t2 is None:
                continue
            pairs.append((t1, t2) if round_index % 2 == 0 else (t2, t1))
        rounds.append(pairs)
        rotating = [rotating[-1], *rotating[:-1]]
    return rounds


@transaction.atomic
def generate_schedule(season: Season) -> list[Match]:
    """Generate the full regular-season schedule for ``season``.

    Only safe to call while no match of the season has been confirmed yet —
    regenerating after results exist would silently rewrite history.
    """
    if season.matches.filter(status=MatchStatus.CONFIRMED).exists():
        msg = "Cannot regenerate: some matches already have a confirmed result."
        raise LeagueError(msg)

    teams = season.approved_teams
    if len(teams) < 2:  # noqa: PLR2004
        msg = "At least two approved teams are required to generate a schedule."
        raise LeagueError(msg)

    rounds = _round_robin_pairs(teams)
    if season.double_round_robin:
        rounds = rounds + [[(away, home) for home, away in r] for r in rounds]

    season.matches.exclude(status=MatchStatus.CONFIRMED).delete()
    matches = [
        Match(season=season, round_number=round_number, home_team=home, away_team=away)
        for round_number, pairs in enumerate(rounds, start=1)
        for home, away in pairs
    ]
    Match.objects.bulk_create(matches)
    return list(season.matches.order_by("round_number", "id"))


# ---------------------------------------------------------------------------
# Match reporting / confirmation / disputes
# ---------------------------------------------------------------------------


@transaction.atomic
def report_result(match: Match, team, score_home: int, score_away: int, user) -> Match:
    """Report a result for ``match`` on behalf of ``team``."""
    if team.id not in (match.home_team_id, match.away_team_id):
        msg = "This team is not part of the match."
        raise MatchPermissionError(msg)
    if not team.has_management_rights(user):
        msg = "You do not have permission to report results for this team."
        raise MatchPermissionError(msg)
    if match.status != MatchStatus.SCHEDULED:
        msg = "A result can only be reported for a scheduled match."
        raise MatchError(msg)

    match.score_home = score_home
    match.score_away = score_away
    match.status = MatchStatus.AWAITING_CONFIRMATION
    match.reported_by = user
    match.reported_at = timezone.now()
    match.save(
        update_fields=[
            "score_home",
            "score_away",
            "status",
            "reported_by",
            "reported_at",
        ],
    )

    opponent = match.away_team if team.id == match.home_team_id else match.home_team
    _notify_managers(
        opponent,
        title=f"Result reported: {_score_line(match, score_home, score_away)}",
        message=f"{team.name} reported this result. Please confirm or dispute it.",
        level=NotificationLevel.INFO,
        category=NotificationCategory.LEAGUE,
        url=_absolute_url(match.get_absolute_url),
    )
    return match


def get_confirming_team(match: Match):
    """Return the team expected to confirm/dispute the reported result."""
    reporter_team = get_user_team(match.reported_by) if match.reported_by_id else None
    if reporter_team is None or reporter_team.id not in (
        match.home_team_id,
        match.away_team_id,
    ):
        msg = "The reporting team could not be determined."
        raise MatchError(msg)
    if reporter_team.id == match.home_team_id:
        return match.away_team
    return match.home_team


def _guard_confirmable(match: Match, user) -> None:
    if match.status != MatchStatus.AWAITING_CONFIRMATION:
        msg = "This match has no result awaiting confirmation."
        raise MatchError(msg)
    confirming_team = get_confirming_team(match)
    if not confirming_team.has_management_rights(user):
        msg = "Only the opposing team can confirm or dispute this result."
        raise MatchPermissionError(msg)


@transaction.atomic
def confirm_result(match: Match, user) -> Match:
    """Confirm a reported result. Only the team that did not report it may do this."""
    _guard_confirmable(match, user)

    match.status = MatchStatus.CONFIRMED
    match.confirmed_by = user
    match.confirmed_at = timezone.now()
    match.save(update_fields=["status", "confirmed_by", "confirmed_at"])

    for team in (match.home_team, match.away_team):
        _notify_managers(
            team,
            title=f"Result confirmed: {_score_line(match)}",
            message="Both teams have confirmed this result.",
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.LEAGUE,
            url=_absolute_url(match.get_absolute_url),
        )
    return match


@transaction.atomic
def dispute_result(match: Match, user, reason: str = "") -> Match:
    """Dispute a reported result, escalating it to league staff."""
    _guard_confirmable(match, user)

    match.status = MatchStatus.DISPUTED
    match.dispute_reason = reason
    match.save(update_fields=["status", "dispute_reason"])

    for staff in User.objects.filter(is_staff=True):
        notify(
            staff,
            title=f"Match disputed: {match.home_team} vs {match.away_team}",
            message=reason or "A team disputed the reported result.",
            level=NotificationLevel.WARNING,
            category=NotificationCategory.LEAGUE,
            url=_absolute_url(match.get_absolute_url),
        )
    return match


@transaction.atomic
def resolve_dispute(match: Match, organizer, score_home: int, score_away: int) -> Match:
    """Resolve a disputed match with a final score. League staff only."""
    if not organizer.is_staff:
        msg = "You do not have permission to resolve disputes."
        raise LeaguePermissionError(msg)
    if match.status != MatchStatus.DISPUTED:
        msg = "This match is not disputed."
        raise MatchError(msg)

    match.score_home = score_home
    match.score_away = score_away
    match.status = MatchStatus.CONFIRMED
    match.confirmed_by = organizer
    match.confirmed_at = timezone.now()
    match.save(
        update_fields=[
            "score_home",
            "score_away",
            "status",
            "confirmed_by",
            "confirmed_at",
        ],
    )

    for team in (match.home_team, match.away_team):
        _notify_managers(
            team,
            title=f"Dispute resolved: {_score_line(match, score_home, score_away)}",
            message="League staff resolved the dispute for this match.",
            level=NotificationLevel.INFO,
            category=NotificationCategory.LEAGUE,
            url=_absolute_url(match.get_absolute_url),
        )
    return match


@transaction.atomic
def forfeit_match(match: Match, organizer, forfeiting_team) -> Match:
    """Forfeit a match in favour of the opposing team. League staff only."""
    if not organizer.is_staff:
        msg = "You do not have permission to forfeit matches."
        raise LeaguePermissionError(msg)
    if forfeiting_team.id not in (match.home_team_id, match.away_team_id):
        msg = "This team is not part of the match."
        raise MatchError(msg)
    if match.status in (
        MatchStatus.CONFIRMED,
        MatchStatus.FORFEITED,
        MatchStatus.CANCELLED,
    ):
        msg = "This match can no longer be forfeited."
        raise MatchError(msg)

    if forfeiting_team.id == match.home_team_id:
        match.score_home, match.score_away = 0, 1
    else:
        match.score_home, match.score_away = 1, 0
    match.status = MatchStatus.FORFEITED
    match.confirmed_by = organizer
    match.confirmed_at = timezone.now()
    match.save(
        update_fields=[
            "score_home",
            "score_away",
            "status",
            "confirmed_by",
            "confirmed_at",
        ],
    )
    return match


@transaction.atomic
def cancel_match(match: Match, organizer) -> Match:
    """Cancel a match without a result (e.g. a double bye). League staff only."""
    if not organizer.is_staff:
        msg = "You do not have permission to cancel matches."
        raise LeaguePermissionError(msg)
    if match.status == MatchStatus.CONFIRMED:
        msg = "A confirmed match cannot be cancelled."
        raise MatchError(msg)

    match.status = MatchStatus.CANCELLED
    match.save(update_fields=["status"])
    return match


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


@dataclass
class StandingRow:
    team: Team
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    points: int = 0
    score_for: int = 0
    score_against: int = 0

    @property
    def score_diff(self) -> int:
        return self.score_for - self.score_against


def _apply_result(rows: dict, season: Season, m: Match) -> None:
    home_row, away_row = rows.get(m.home_team_id), rows.get(m.away_team_id)
    if home_row is None or away_row is None:
        return  # team withdrew after playing; not part of the current standings
    # Confirmed matches always carry a final score.
    assert m.score_home is not None
    assert m.score_away is not None
    home_row.played += 1
    away_row.played += 1
    home_row.score_for += m.score_home
    home_row.score_against += m.score_away
    away_row.score_for += m.score_away
    away_row.score_against += m.score_home
    if m.score_home > m.score_away:
        home_row.wins += 1
        home_row.points += season.points_win
        away_row.losses += 1
        away_row.points += season.points_loss
    elif m.score_away > m.score_home:
        away_row.wins += 1
        away_row.points += season.points_win
        home_row.losses += 1
        home_row.points += season.points_loss
    else:
        home_row.draws += 1
        away_row.draws += 1
        home_row.points += season.points_draw
        away_row.points += season.points_draw


def _head_to_head_order(
    group: list[StandingRow],
    matches: list[Match],
) -> list[StandingRow]:
    """Order a two-team tie by their direct-match wins, falling back to score diff."""
    t1, t2 = group[0].team, group[1].team
    t1_wins = t2_wins = 0
    for m in matches:
        teams = {m.home_team_id, m.away_team_id}
        if teams != {t1.id, t2.id}:
            continue
        # Confirmed matches always carry a final score.
        assert m.score_home is not None
        assert m.score_away is not None
        if m.score_home == m.score_away:
            continue
        winner_id = m.home_team_id if m.score_home > m.score_away else m.away_team_id
        if winner_id == t1.id:
            t1_wins += 1
        else:
            t2_wins += 1
    if t1_wins == t2_wins:
        return sorted(group, key=lambda r: (-r.score_diff, r.team.name))
    first_team_id = t1.id if t1_wins > t2_wins else t2.id
    return sorted(group, key=lambda r: r.team.id != first_team_id)


def get_standings(season: Season) -> list[StandingRow]:
    """Compute the current standings for ``season`` from confirmed regular matches.

    Computed on demand rather than stored, so it can never drift out of sync
    with the underlying match results.
    """
    rows = {team.id: StandingRow(team=team) for team in season.approved_teams}
    matches = list(
        season.matches.filter(
            status=MatchStatus.CONFIRMED,
            stage=MatchStage.REGULAR,
        ).select_related("home_team", "away_team"),
    )
    for m in matches:
        _apply_result(rows, season, m)

    ordered_by_points = sorted(rows.values(), key=lambda r: -r.points)
    result: list[StandingRow] = []
    for _points, group_iter in groupby(ordered_by_points, key=lambda r: r.points):
        group = list(group_iter)
        if len(group) == 2:  # noqa: PLR2004
            group = _head_to_head_order(group, matches)
        else:
            group.sort(key=lambda r: (-r.score_diff, r.team.name))
        result.extend(group)
    return result
