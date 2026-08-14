from __future__ import annotations

import pytest

from prostratsix.league import services
from prostratsix.league.models import Match
from prostratsix.league.models import MatchStatus
from prostratsix.league.tests.factories import create_season
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.teams import services as team_services
from prostratsix.teams.models import TeamRole
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def season():
    return create_season()


@pytest.fixture
def home_owner():
    return UserFactory()


@pytest.fixture
def away_owner():
    return UserFactory()


@pytest.fixture
def home_team(home_owner):
    return team_services.create_team(home_owner, name="Home")


@pytest.fixture
def away_team(away_owner):
    return team_services.create_team(away_owner, name="Away")


@pytest.fixture
def match(season, home_team, away_team):
    return Match.objects.create(
        season=season,
        round_number=1,
        home_team=home_team,
        away_team=away_team,
    )


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


class TestReportResult:
    def test_home_manager_can_report(self, match, home_team, home_owner):
        services.report_result(match, home_team, 7, 3, home_owner)
        match.refresh_from_db()
        assert match.status == MatchStatus.AWAITING_CONFIRMATION
        assert match.reported_by == home_owner
        assert Notification.objects.filter(
            category=NotificationCategory.LEAGUE,
        ).exists()

    def test_outsider_team_cannot_report(self, match, home_owner):
        outsider = team_services.create_team(UserFactory(), name="Outsider")
        with pytest.raises(services.MatchPermissionError):
            services.report_result(match, outsider, 7, 3, home_owner)

    def test_non_manager_cannot_report(self, match, home_team, away_owner):
        # away_owner is not a member of home_team.
        with pytest.raises(services.MatchPermissionError):
            services.report_result(match, home_team, 7, 3, away_owner)

    def test_cannot_report_twice(self, match, home_team, home_owner):
        services.report_result(match, home_team, 7, 3, home_owner)
        with pytest.raises(services.MatchError):
            services.report_result(match, home_team, 7, 3, home_owner)


class TestConfirmResult:
    def test_opposing_team_can_confirm(self, match, home_team, home_owner, away_owner):
        services.report_result(match, home_team, 7, 3, home_owner)
        services.confirm_result(match, away_owner)
        match.refresh_from_db()
        assert match.status == MatchStatus.CONFIRMED
        assert match.confirmed_by == away_owner

    def test_reporting_team_cannot_confirm_own_result(
        self,
        match,
        home_team,
        home_owner,
    ):
        services.report_result(match, home_team, 7, 3, home_owner)
        with pytest.raises(services.MatchPermissionError):
            services.confirm_result(match, home_owner)

    def test_cannot_confirm_without_a_report(self, match, away_owner):
        with pytest.raises(services.MatchError):
            services.confirm_result(match, away_owner)


class TestDisputeResult:
    def test_opposing_team_can_dispute(
        self,
        match,
        home_team,
        home_owner,
        away_owner,
        staff,
    ):
        services.report_result(match, home_team, 7, 3, home_owner)
        services.dispute_result(match, away_owner, reason="Wrong score")
        match.refresh_from_db()
        assert match.status == MatchStatus.DISPUTED
        assert match.dispute_reason == "Wrong score"
        assert Notification.objects.filter(
            recipient=staff,
            category=NotificationCategory.LEAGUE,
        ).exists()


class TestResolveDispute:
    def test_staff_can_resolve(self, match, home_team, home_owner, away_owner, staff):
        services.report_result(match, home_team, 7, 3, home_owner)
        services.dispute_result(match, away_owner, reason="Wrong score")

        services.resolve_dispute(match, staff, 6, 4)
        match.refresh_from_db()
        assert match.status == MatchStatus.CONFIRMED
        assert (match.score_home, match.score_away) == (6, 4)
        assert match.confirmed_by == staff

    def test_non_staff_cannot_resolve(self, match, home_team, home_owner, away_owner):
        services.report_result(match, home_team, 7, 3, home_owner)
        services.dispute_result(match, away_owner)
        with pytest.raises(services.LeaguePermissionError):
            services.resolve_dispute(match, home_owner, 6, 4)

    def test_cannot_resolve_non_disputed_match(self, match, staff):
        with pytest.raises(services.MatchError):
            services.resolve_dispute(match, staff, 6, 4)


class TestForfeitAndCancel:
    def test_staff_can_forfeit(self, match, home_team, staff):
        services.forfeit_match(match, staff, home_team)
        match.refresh_from_db()
        assert match.status == MatchStatus.FORFEITED
        assert (match.score_home, match.score_away) == (0, 1)

    def test_non_staff_cannot_forfeit(self, match, home_team, home_owner):
        with pytest.raises(services.LeaguePermissionError):
            services.forfeit_match(match, home_owner, home_team)

    def test_staff_can_cancel(self, match, staff):
        services.cancel_match(match, staff)
        match.refresh_from_db()
        assert match.status == MatchStatus.CANCELLED

    def test_cannot_cancel_confirmed_match(
        self,
        match,
        home_team,
        home_owner,
        away_owner,
        staff,
    ):
        services.report_result(match, home_team, 7, 3, home_owner)
        services.confirm_result(match, away_owner)
        with pytest.raises(services.MatchError):
            services.cancel_match(match, staff)


class TestTeamRoleGuards:
    def test_substitute_cannot_report(self, match, home_team, home_owner):
        substitute = UserFactory()
        home_team.memberships.create(user=substitute, role=TeamRole.SUBSTITUTE)
        with pytest.raises(services.MatchPermissionError):
            services.report_result(match, home_team, 7, 3, substitute)
