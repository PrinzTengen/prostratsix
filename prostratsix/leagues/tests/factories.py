from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from factory import LazyFunction
from factory import Sequence
from factory.django import DjangoModelFactory

from prostratsix.leagues.models import DEFAULT_MIN_ROSTER_SIZE
from prostratsix.leagues.models import League
from prostratsix.leagues.models import LeagueStatus
from prostratsix.teams.models import Team
from prostratsix.teams.models import TeamMembership
from prostratsix.teams.models import TeamRole
from prostratsix.users.tests.factories import UserFactory


class LeagueFactory(DjangoModelFactory[League]):
    """A league that is open for registration right now."""

    name = Sequence(lambda n: f"League {n}")
    status = LeagueStatus.REGISTRATION_OPEN
    max_teams = 8
    registration_opens_at = LazyFunction(
        lambda: timezone.now() - timedelta(days=1),
    )
    registration_closes_at = LazyFunction(
        lambda: timezone.now() + timedelta(days=7),
    )
    starts_at = LazyFunction(lambda: timezone.now() + timedelta(days=14))

    class Meta:
        model = League


class TeamFactory(DjangoModelFactory[Team]):
    name = Sequence(lambda n: f"Team {n}")

    class Meta:
        model = Team


def team_with_players(size: int = DEFAULT_MIN_ROSTER_SIZE, **fields) -> Team:
    """Create a team with ``size`` members: one owner and the rest players.

    ``TeamMembership.user`` is a OneToOneField, so every member is a fresh user
    — a user belongs to exactly one team.
    """
    team = TeamFactory(**fields)
    TeamMembership.objects.create(
        team=team,
        user=UserFactory(),
        role=TeamRole.OWNER,
    )
    for _index in range(size - 1):
        TeamMembership.objects.create(
            team=team,
            user=UserFactory(),
            role=TeamRole.PLAYER,
        )
    return team
