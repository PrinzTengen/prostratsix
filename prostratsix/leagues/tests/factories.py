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


class PlayerFactory(UserFactory):
    """A user whose username cannot collide with another one.

    UserFactory draws its username from Faker and sets
    ``django_get_or_create = ["username"]``, so a collision does not raise — it
    quietly hands back the *existing* user. Measured: asking for 300 users gives
    299 distinct ones often enough to matter.

    In this app that is poison. team_with_players() would hand one user a second
    TeamMembership (a OneToOneField — IntegrityError), and an "outsider" in a
    permission test can come back as a member of the very team it is supposed to
    have no rights on, which fails the test for a reason that has nothing to do
    with the code. That was seen once, exactly like that, before the sequence
    below went in.

    The shared UserFactory is used by every other app's tests too, so fixing it
    there is its own ticket; the league tests just stop relying on Faker for
    uniqueness.
    """

    username = Sequence(lambda n: f"league-player-{n}")


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
        user=PlayerFactory(),
        role=TeamRole.OWNER,
    )
    for _index in range(size - 1):
        TeamMembership.objects.create(
            team=team,
            user=PlayerFactory(),
            role=TeamRole.PLAYER,
        )
    return team
