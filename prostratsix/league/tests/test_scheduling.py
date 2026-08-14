from __future__ import annotations

import itertools

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


def _approved_team(season, name, staff):
    owner = UserFactory()
    team = team_services.create_team(owner, name=name)
    registration = services.register_team_for_season(season, team, owner)
    services.approve_registration(registration, staff)
    return team


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


def _teams(season, staff, count):
    return [_approved_team(season, f"Team {i}", staff) for i in range(count)]


class TestGenerateSchedule:
    def test_rejects_fewer_than_two_teams(self, staff):
        season = create_season()
        _teams(season, staff, 1)
        with pytest.raises(services.LeagueError):
            services.generate_schedule(season)

    def test_even_team_count_single_round_robin(self, staff):
        season = create_season(double_round_robin=False)
        teams = _teams(season, staff, 4)
        services.generate_schedule(season)

        matches = list(Match.objects.filter(season=season))
        assert len(matches) == 6  # 4 * 3 / 2

        played_pairs = {frozenset({m.home_team_id, m.away_team_id}) for m in matches}
        expected_pairs = {
            frozenset(pair) for pair in itertools.combinations([t.id for t in teams], 2)
        }
        assert played_pairs == expected_pairs

    def test_double_round_robin_plays_each_pair_twice(self, staff):
        season = create_season(double_round_robin=True)
        teams = _teams(season, staff, 4)
        services.generate_schedule(season)

        matches = list(Match.objects.filter(season=season))
        assert len(matches) == 12  # 4 * 3 (each ordered pair once)
        for t1, t2 in itertools.combinations([t.id for t in teams], 2):
            home_away = matches_between(matches, t1, t2)
            assert home_away == 1
            assert matches_between(matches, t2, t1) == 1

    def test_odd_team_count_has_no_self_matches_and_correct_count(self, staff):
        season = create_season(double_round_robin=False)
        _teams(season, staff, 5)
        services.generate_schedule(season)

        matches = list(Match.objects.filter(season=season))
        assert len(matches) == 10  # 5 * 4 / 2
        for m in matches:
            assert m.home_team_id != m.away_team_id

    def test_no_team_plays_twice_in_the_same_round(self, staff):
        season = create_season(double_round_robin=True)
        _teams(season, staff, 5)
        services.generate_schedule(season)

        matches = list(Match.objects.filter(season=season))
        rounds: dict[int, list[Match]] = {}
        for m in matches:
            rounds.setdefault(m.round_number, []).append(m)
        for round_matches in rounds.values():
            team_ids = [
                t for m in round_matches for t in (m.home_team_id, m.away_team_id)
            ]
            assert len(team_ids) == len(set(team_ids))

    def test_regenerating_before_results_replaces_schedule(self, staff):
        season = create_season(double_round_robin=False)
        _teams(season, staff, 4)
        services.generate_schedule(season)
        first_count = Match.objects.filter(season=season).count()

        services.generate_schedule(season)
        assert Match.objects.filter(season=season).count() == first_count

    def test_cannot_regenerate_after_a_confirmed_result(self, staff):
        season = create_season(double_round_robin=False)
        _teams(season, staff, 4)
        services.generate_schedule(season)

        match = Match.objects.filter(season=season).first()
        assert match is not None
        match.status = MatchStatus.CONFIRMED
        match.score_home, match.score_away = 1, 0
        match.save()

        with pytest.raises(services.LeagueError):
            services.generate_schedule(season)


def matches_between(matches, home_id, away_id):
    return sum(
        1 for m in matches if m.home_team_id == home_id and m.away_team_id == away_id
    )
