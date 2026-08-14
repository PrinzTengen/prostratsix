from __future__ import annotations

import pytest

from prostratsix.league import services
from prostratsix.league.models import Match
from prostratsix.league.models import MatchStatus
from prostratsix.league.tests.factories import create_season
from prostratsix.teams import services as team_services
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


def _approved_team(season, name, staff):
    owner = UserFactory()
    team = team_services.create_team(owner, name=name)
    registration = services.register_team_for_season(season, team, owner)
    services.approve_registration(registration, staff)
    return team


def _confirmed_match(season, home, away, score, round_number=1):
    score_home, score_away = score
    return Match.objects.create(
        season=season,
        round_number=round_number,
        home_team=home,
        away_team=away,
        status=MatchStatus.CONFIRMED,
        score_home=score_home,
        score_away=score_away,
    )


class TestStandingsPoints:
    def test_default_points_for_win_and_loss(self, staff):
        season = create_season()
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        _confirmed_match(season, team_a, team_b, (7, 3))

        standings = {row.team.id: row for row in services.get_standings(season)}
        assert standings[team_a.id].points == 3
        assert standings[team_a.id].wins == 1
        assert standings[team_a.id].score_diff == 4
        assert standings[team_b.id].points == 0
        assert standings[team_b.id].losses == 1

    def test_draw_awards_draw_points_to_both(self, staff):
        season = create_season()
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        _confirmed_match(season, team_a, team_b, (5, 5))

        standings = {row.team.id: row for row in services.get_standings(season)}
        assert standings[team_a.id].points == 1
        assert standings[team_b.id].points == 1
        assert standings[team_a.id].draws == 1

    def test_custom_points_system(self, staff):
        season = create_season(points_win=2, points_draw=1, points_loss=0)
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        _confirmed_match(season, team_a, team_b, (7, 3))

        standings = {row.team.id: row for row in services.get_standings(season)}
        assert standings[team_a.id].points == 2

    def test_only_confirmed_matches_count(self, staff):
        season = create_season()
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        Match.objects.create(
            season=season,
            round_number=1,
            home_team=team_a,
            away_team=team_b,
            status=MatchStatus.SCHEDULED,
        )
        Match.objects.create(
            season=season,
            round_number=2,
            home_team=team_a,
            away_team=team_b,
            status=MatchStatus.DISPUTED,
            score_home=7,
            score_away=3,
        )

        standings = {row.team.id: row for row in services.get_standings(season)}
        assert standings[team_a.id].played == 0
        assert standings[team_a.id].points == 0


class TestStandingsTiebreakers:
    def test_two_way_tie_uses_head_to_head_over_score_diff(self, staff):
        season = create_season()
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        team_c = _approved_team(season, "C", staff)
        team_d = _approved_team(season, "D", staff)

        # A and B end up level on points (3 each), but B has the better
        # overall score diff (+8 vs -8). A still beat B head-to-head, and
        # that must decide the order over the raw score diff.
        _confirmed_match(
            season, team_a, team_b, (1, 0), round_number=1,
        )  # A beats B h2h
        _confirmed_match(
            season,
            team_c,
            team_a,
            (9, 0),
            round_number=2,
        )  # A takes a blowout loss
        _confirmed_match(
            season,
            team_b,
            team_d,
            (9, 0),
            round_number=3,
        )  # B takes a blowout win
        _confirmed_match(
            season,
            team_c,
            team_d,
            (5, 0),
            round_number=4,
        )  # keeps C/D out of the tie

        standings = services.get_standings(season)
        tied = [row for row in standings if row.team.id in (team_a.id, team_b.id)]
        assert tied[0].points == tied[1].points == 3
        assert tied[0].team.id == team_a.id
        assert tied[1].team.id == team_b.id

    def test_more_than_two_way_tie_uses_score_diff(self, staff):
        season = create_season()
        team_a = _approved_team(season, "A", staff)
        team_b = _approved_team(season, "B", staff)
        team_c = _approved_team(season, "C", staff)
        sink = _approved_team(season, "Sink", staff)

        # A, B and C each beat `sink` once (same points, three-way tie),
        # with different score diffs that must decide the order.
        _confirmed_match(season, team_a, sink, (7, 0), round_number=1)
        _confirmed_match(season, team_b, sink, (7, 2), round_number=2)
        _confirmed_match(season, team_c, sink, (7, 5), round_number=3)

        standings = services.get_standings(season)
        contenders = [
            row for row in standings if row.team.id in (team_a.id, team_b.id, team_c.id)
        ]
        assert [row.team.id for row in contenders] == [team_a.id, team_b.id, team_c.id]
