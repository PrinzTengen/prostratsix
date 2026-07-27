from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from prostratsix.leagues import services
from prostratsix.leagues import tasks
from prostratsix.leagues.models import LeagueStatus
from prostratsix.leagues.tests.factories import LeagueFactory
from prostratsix.leagues.tests.factories import team_with_players
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory

pytestmark = pytest.mark.django_db

ROSTER_SIZE = 5


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


def past(**offset):
    return timezone.now() - timedelta(**offset)


def future(**offset):
    return timezone.now() + timedelta(**offset)


def a_league_ready_to_start(**fields):
    """A closed league whose start time has come, with one registered team."""
    league = LeagueFactory(**fields)
    team = team_with_players(ROSTER_SIZE)
    services.register_team(league, team.owner, team)
    league.status = LeagueStatus.REGISTRATION_CLOSED
    league.starts_at = past(minutes=1)
    league.save(update_fields=["status", "starts_at"])
    return league


class TestSyncLeagueStatuses:
    def test_a_draft_opens_when_its_registration_window_starts(self):
        league = LeagueFactory(
            status=LeagueStatus.DRAFT,
            registration_opens_at=past(minutes=1),
            registration_closes_at=future(days=7),
        )

        assert tasks.sync_league_statuses()["opened"] == 1

        league.refresh_from_db()
        assert league.status == LeagueStatus.REGISTRATION_OPEN

    def test_an_open_league_closes_when_its_window_ends(self):
        league = LeagueFactory(
            registration_opens_at=past(days=7),
            registration_closes_at=past(minutes=1),
        )

        assert tasks.sync_league_statuses()["closed"] == 1

        league.refresh_from_db()
        assert league.status == LeagueStatus.REGISTRATION_CLOSED

    def test_a_closed_league_starts_and_notifies_its_players(
        self,
        django_capture_on_commit_callbacks,
    ):
        """start_league() notifies on commit — see test_services.py for the fixture."""
        league = a_league_ready_to_start()
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            result = tasks.sync_league_statuses()

        assert result["started"] == 1
        league.refresh_from_db()
        assert league.status == LeagueStatus.RUNNING
        assert (
            Notification.objects.filter(
                category=NotificationCategory.LEAGUE,
            ).count()
            == ROSTER_SIZE
        )

    def test_a_league_that_is_not_due_is_left_alone(self):
        draft = LeagueFactory(
            status=LeagueStatus.DRAFT,
            registration_opens_at=future(days=1),
            registration_closes_at=future(days=8),
        )
        open_league = LeagueFactory()
        not_started = LeagueFactory(
            status=LeagueStatus.REGISTRATION_CLOSED,
            starts_at=future(days=1),
        )

        assert tasks.sync_league_statuses() == {
            "opened": 0,
            "closed": 0,
            "started": 0,
        }

        for league, status in (
            (draft, LeagueStatus.DRAFT),
            (open_league, LeagueStatus.REGISTRATION_OPEN),
            (not_started, LeagueStatus.REGISTRATION_CLOSED),
        ):
            league.refresh_from_db()
            assert league.status == status

    def test_a_league_without_a_start_date_never_starts_by_itself(self):
        """starts_at is nullable, and __lte drops the NULL rows — deliberately.

        A league nobody scheduled must not begin on its own; an admin starts it
        from the changelist action.
        """
        league = LeagueFactory(
            status=LeagueStatus.REGISTRATION_CLOSED,
            starts_at=None,
        )

        assert tasks.sync_league_statuses()["started"] == 0

        league.refresh_from_db()
        assert league.status == LeagueStatus.REGISTRATION_CLOSED

    def test_a_league_left_behind_catches_up_in_one_run(self):
        """The worker was down for a week: draft in, running out.

        The three steps run in order and each queryset is evaluated when its step
        begins, so the league falls through all of them in a single sweep instead
        of needing three separate runs — fifteen minutes of a league that should
        have started days ago.
        """
        league = LeagueFactory(
            status=LeagueStatus.DRAFT,
            registration_opens_at=past(days=8),
            registration_closes_at=past(days=1),
            starts_at=past(hours=1),
        )

        result = tasks.sync_league_statuses()

        assert result == {"opened": 1, "closed": 1, "started": 1}
        league.refresh_from_db()
        assert league.status == LeagueStatus.RUNNING

    def test_a_second_run_has_nothing_left_to_do(
        self,
        django_capture_on_commit_callbacks,
    ):
        """Idempotency, where it is actually observable.

        Two overlapping runs must not start the same league twice and send every
        player a second round of notifications. The guarantee lives in
        start_league(), which re-checks the status after the row lock — the task
        only filters roughly, and it is *not* the task's job to hold that line.
        """
        a_league_ready_to_start()
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            first = tasks.sync_league_statuses()
            second = tasks.sync_league_statuses()

        assert first["started"] == 1
        assert second == {"opened": 0, "closed": 0, "started": 0}
        assert (
            Notification.objects.filter(
                category=NotificationCategory.LEAGUE,
            ).count()
            == ROSTER_SIZE
        )

    def test_one_league_that_refuses_does_not_take_the_others_down(
        self,
        monkeypatch,
    ):
        """A sweep that dies on the first bad row leaves every later league stuck."""
        doomed = a_league_ready_to_start()
        healthy = a_league_ready_to_start()
        original = services.start_league

        def refuse_one(league):
            if league.pk == doomed.pk:
                msg = "no"
                raise services.LeagueError(msg)
            return original(league)

        monkeypatch.setattr(services, "start_league", refuse_one)

        assert tasks.sync_league_statuses()["started"] == 1

        doomed.refresh_from_db()
        healthy.refresh_from_db()
        assert doomed.status == LeagueStatus.REGISTRATION_CLOSED
        assert healthy.status == LeagueStatus.RUNNING

    def test_an_unexpected_error_does_not_take_the_others_down_either(
        self,
        monkeypatch,
    ):
        """Not only LeagueError: whatever one row throws, the sweep carries on."""
        doomed = a_league_ready_to_start()
        healthy = a_league_ready_to_start()
        original = services.start_league

        def blow_up(league):
            if league.pk == doomed.pk:
                msg = "boom"
                raise RuntimeError(msg)
            return original(league)

        monkeypatch.setattr(services, "start_league", blow_up)

        assert tasks.sync_league_statuses()["started"] == 1

        healthy.refresh_from_db()
        assert healthy.status == LeagueStatus.RUNNING
