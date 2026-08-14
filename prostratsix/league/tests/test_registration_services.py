from __future__ import annotations

import pytest

from prostratsix.league import services
from prostratsix.league.models import RegistrationStatus
from prostratsix.league.models import SeasonStatus
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
def owner():
    return UserFactory()


@pytest.fixture
def team(owner):
    return team_services.create_team(owner, name="Alpha")


@pytest.fixture
def season():
    return create_season()


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


class TestRegisterTeamForSeason:
    def test_owner_can_register(self, season, team, owner, staff):
        registration = services.register_team_for_season(season, team, owner)
        assert registration.status == RegistrationStatus.PENDING
        assert Notification.objects.filter(
            recipient=staff,
            category=NotificationCategory.LEAGUE,
        ).exists()

    def test_non_manager_cannot_register(self, season, team):
        player = UserFactory()
        team.memberships.create(user=player, role=TeamRole.PLAYER)
        with pytest.raises(services.LeaguePermissionError):
            services.register_team_for_season(season, team, player)

    def test_rejects_when_registration_closed(self, team, owner):
        season = create_season(status=SeasonStatus.DRAFT)
        with pytest.raises(services.LeagueError):
            services.register_team_for_season(season, team, owner)

    def test_rejects_when_season_full(self, owner):
        season = create_season(max_teams=1)
        team = team_services.create_team(owner, name="Alpha")
        services.register_team_for_season(season, team, owner)

        other_owner = UserFactory()
        other_team = team_services.create_team(other_owner, name="Beta")
        with pytest.raises(services.LeagueError):
            services.register_team_for_season(season, other_team, other_owner)

    def test_rejects_duplicate_registration(self, season, team, owner):
        services.register_team_for_season(season, team, owner)
        with pytest.raises(services.LeagueError):
            services.register_team_for_season(season, team, owner)

    def test_allows_reregistration_after_withdrawal(self, season, team, owner):
        registration = services.register_team_for_season(season, team, owner)
        services.withdraw_registration(registration, owner)
        new_registration = services.register_team_for_season(season, team, owner)
        assert new_registration.status == RegistrationStatus.PENDING


class TestApproveRejectRegistration:
    def test_staff_can_approve(self, season, team, owner, staff):
        registration = services.register_team_for_season(season, team, owner)
        approved = services.approve_registration(registration, staff)
        assert approved.status == RegistrationStatus.APPROVED
        assert approved.decided_by == staff
        assert team in season.approved_teams

    def test_non_staff_cannot_approve(self, season, team, owner):
        registration = services.register_team_for_season(season, team, owner)
        with pytest.raises(services.LeaguePermissionError):
            services.approve_registration(registration, owner)

    def test_staff_can_reject(self, season, team, owner, staff):
        registration = services.register_team_for_season(season, team, owner)
        rejected = services.reject_registration(
            registration,
            staff,
            reason="Not eligible",
        )
        assert rejected.status == RegistrationStatus.REJECTED

    def test_cannot_decide_twice(self, season, team, owner, staff):
        registration = services.register_team_for_season(season, team, owner)
        services.approve_registration(registration, staff)
        with pytest.raises(services.LeagueError):
            services.approve_registration(registration, staff)


class TestWithdrawRegistration:
    def test_owner_can_withdraw_pending(self, season, team, owner):
        registration = services.register_team_for_season(season, team, owner)
        withdrawn = services.withdraw_registration(registration, owner)
        assert withdrawn.status == RegistrationStatus.WITHDRAWN

    def test_non_manager_cannot_withdraw(self, season, team, owner):
        registration = services.register_team_for_season(season, team, owner)
        outsider = UserFactory()
        with pytest.raises(services.LeaguePermissionError):
            services.withdraw_registration(registration, outsider)

    def test_cannot_withdraw_rejected(self, season, team, owner, staff):
        registration = services.register_team_for_season(season, team, owner)
        services.reject_registration(registration, staff)
        with pytest.raises(services.LeagueError):
            services.withdraw_registration(registration, owner)
