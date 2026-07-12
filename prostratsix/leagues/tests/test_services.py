from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from django.core import mail
from django.db import IntegrityError
from django.db import connection
from django.db import connections
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from prostratsix.leagues import services
from prostratsix.leagues.models import SLOTS_TAKEN_ANNOTATION
from prostratsix.leagues.models import League
from prostratsix.leagues.models import LeagueRegistration
from prostratsix.leagues.models import LeagueRosterEntry
from prostratsix.leagues.models import LeagueStatus
from prostratsix.leagues.models import RegistrationStatus
from prostratsix.leagues.tests.factories import LeagueFactory
from prostratsix.leagues.tests.factories import team_with_players
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.models import NotificationPreference
from prostratsix.teams.models import TeamMembership
from prostratsix.teams.models import TeamRole
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ROSTER_SIZE = 5

# Nothing in the race waits on wall-clock time except the handover: the window the
# loser gets to reach the row lock while the winner is frozen. Generous, because it
# only ever costs this one test half a second, and too short would mean the loser
# does its capacity check after the winner committed — a green test with no lock.
RACE_HANDOVER_SECONDS = 0.5
RACE_TIMEOUT_SECONDS = 10

# select_for_update() does not raise on SQLite — it is compiled away into a plain
# SELECT (has_select_for_update = False, see the Django SQL compiler). A race test
# on SQLite therefore does not fail loudly, it passes for the wrong reason. CI runs
# the suite against a real PostgreSQL via docker-compose.local.yml, where the lock
# exists and the test means something.
only_postgres = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="select_for_update() is a no-op on SQLite; this test would pass for the "
    "wrong reason (see docs/plans/research/phase-2-leagues.md §2.1).",
)


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def league():
    return LeagueFactory()


@pytest.fixture
def team():
    return team_with_players(ROSTER_SIZE)


def league_notifications(**filters):
    return Notification.objects.filter(
        category=NotificationCategory.LEAGUE,
        **filters,
    )


class TestRegisterTeam:
    def test_owner_registers_and_the_roster_is_frozen(self, league, team):
        registration = services.register_team(league, team.owner, team)

        assert registration.status == RegistrationStatus.CONFIRMED
        assert registration.registered_by == team.owner
        assert league.slots_taken == 1

        roster = registration.roster.all()
        assert roster.count() == ROSTER_SIZE
        assert {entry.user for entry in roster} == {
            membership.user for membership in team.memberships.all()
        }
        # The role travels with the snapshot, it is not looked up live.
        owner_entry = roster.get(user=team.owner)
        assert owner_entry.role == TeamRole.OWNER
        assert roster.filter(role=TeamRole.PLAYER).count() == ROSTER_SIZE - 1

    def test_a_manager_may_register_too(self, league, team):
        manager = team.memberships.filter(role=TeamRole.PLAYER).first()
        manager.role = TeamRole.MANAGER
        manager.save(update_fields=["role"])

        registration = services.register_team(league, manager.user, team)

        assert registration.status == RegistrationStatus.CONFIRMED

    def test_notifies_every_player(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """All five players get a LEAGUE notification.

        register_team() hands the notifications to transaction.on_commit(), and
        under pytest.mark.django_db that callback never fires: the test runs in a
        transaction that is rolled back, never committed. Without this fixture the
        assertion below reads zero — and pulling the on_commit() out of the service
        to "fix" it would be exactly the wrong move: it is what keeps a Celery
        worker from picking the e-mail task up before the registration is committed
        and losing the mail without a trace.
        """
        with django_capture_on_commit_callbacks(execute=True):
            services.register_team(league, team.owner, team)

        assert league_notifications().count() == ROSTER_SIZE
        notification = league_notifications(recipient=team.owner).get()
        assert notification.level == NotificationLevel.SUCCESS
        assert league.name in notification.title

    def test_a_roster_below_the_minimum_is_rejected(self, league):
        small_team = team_with_players(ROSTER_SIZE - 1)

        with pytest.raises(services.RosterTooSmallError):
            services.register_team(league, small_team.owner, small_team)

        assert not LeagueRegistration.objects.exists()
        assert not LeagueRosterEntry.objects.exists()

    def test_exactly_the_minimum_roster_is_enough(self, team):
        league = LeagueFactory(min_roster_size=ROSTER_SIZE)

        registration = services.register_team(league, team.owner, team)

        assert registration.status == RegistrationStatus.CONFIRMED

    def test_a_non_manager_cannot_register(self, league, team):
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user

        with pytest.raises(services.LeaguePermissionError):
            services.register_team(league, player, team)

        with pytest.raises(services.LeaguePermissionError):
            services.register_team(league, UserFactory(), team)

        assert not LeagueRegistration.objects.exists()

    def test_permission_is_checked_before_capacity(self, team):
        """An outsider must not be able to probe the league through the errors.

        The permission guard runs before the league row is even read, so someone
        without management rights learns nothing about the league's state — not
        that it is full, not that registration has closed. Swap the two guards and
        this test turns red while every other one here stays green.
        """
        full_league = LeagueFactory(max_teams=1)
        first = team_with_players(ROSTER_SIZE)
        services.register_team(full_league, first.owner, first)
        closed_league = LeagueFactory(status=LeagueStatus.REGISTRATION_CLOSED)
        outsider = UserFactory()

        with pytest.raises(services.LeaguePermissionError):
            services.register_team(full_league, outsider, team)
        with pytest.raises(services.LeaguePermissionError):
            services.register_team(closed_league, outsider, team)

    def test_registration_before_the_window_opens_is_rejected(self, team):
        league = LeagueFactory(
            registration_opens_at=timezone.now() + timedelta(days=1),
            registration_closes_at=timezone.now() + timedelta(days=8),
        )

        with pytest.raises(services.RegistrationClosedError):
            services.register_team(league, team.owner, team)

    def test_registration_after_the_window_closed_is_rejected(self, team):
        league = LeagueFactory(
            registration_opens_at=timezone.now() - timedelta(days=8),
            registration_closes_at=timezone.now() - timedelta(days=1),
        )

        with pytest.raises(services.RegistrationClosedError):
            services.register_team(league, team.owner, team)

    def test_a_draft_league_does_not_accept_registrations(self, team):
        league = LeagueFactory(status=LeagueStatus.DRAFT)

        with pytest.raises(services.RegistrationClosedError):
            services.register_team(league, team.owner, team)

    def test_a_full_league_raises_league_full_error(self, team):
        """Full is its own error, not a RegistrationClosedError.

        The decision, recorded here because both are defensible: "the league is
        full" and "registration has closed" call for different things from the
        user — wait for someone to withdraw, or give up — so they get different
        exceptions and the view can word the message accordingly.
        """
        league = LeagueFactory(max_teams=1)
        first = team_with_players(ROSTER_SIZE)
        services.register_team(league, first.owner, first)

        with pytest.raises(services.LeagueFullError):
            services.register_team(league, team.owner, team)

        assert league.slots_taken == 1

    def test_registering_twice_is_rejected(self, league, team):
        services.register_team(league, team.owner, team)

        with pytest.raises(services.AlreadyRegisteredError):
            services.register_team(league, team.owner, team)

        assert league.slots_taken == 1

    def test_the_database_rejects_a_second_confirmed_registration(self, league, team):
        """The partial UniqueConstraint as the last line of defence.

        The service guard above is the primary path; this one holds even if that
        guard is ever lost — and it is the only thing that would catch the
        constraint being frozen on a renamed status value in the migration.
        """
        services.register_team(league, team.owner, team)

        with pytest.raises(IntegrityError), transaction.atomic():
            LeagueRegistration.objects.create(
                league=league,
                team=team,
                status=RegistrationStatus.CONFIRMED,
            )


class TestWithdrawTeam:
    def test_withdraw_frees_the_slot_and_keeps_the_snapshot(self, league, team):
        registration = services.register_team(league, team.owner, team)

        services.withdraw_team(league, team.owner, team)

        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.WITHDRAWN
        assert league.slots_taken == 0
        # The snapshot survives as history — that is the point of it.
        assert registration.roster.count() == ROSTER_SIZE

    def test_a_withdrawn_team_can_register_again(self, league, team):
        """The partial constraint only covers confirmed rows.

        Registering again after a withdrawal must not hit
        unique_confirmed_registration — the withdrawn row drops out of the partial
        index the moment its status changes.
        """
        services.register_team(league, team.owner, team)
        services.withdraw_team(league, team.owner, team)

        registration = services.register_team(league, team.owner, team)

        assert registration.status == RegistrationStatus.CONFIRMED
        assert league.registrations.count() == 2
        assert league.slots_taken == 1

    def test_withdraw_notifies_every_player(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """See test_notifies_every_player for why the fixture is needed."""
        services.register_team(league, team.owner, team)
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            services.withdraw_team(league, team.owner, team)

        assert league_notifications().count() == ROSTER_SIZE
        assert (
            league_notifications(level=NotificationLevel.WARNING).count() == ROSTER_SIZE
        )

    def test_a_non_manager_cannot_withdraw(self, league, team):
        services.register_team(league, team.owner, team)
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user

        with pytest.raises(services.LeaguePermissionError):
            services.withdraw_team(league, player, team)

        assert league.slots_taken == 1

    def test_a_team_that_is_not_registered_cannot_withdraw(self, league, team):
        with pytest.raises(services.NotRegisteredError):
            services.withdraw_team(league, team.owner, team)

    def test_cannot_withdraw_once_the_league_runs(self, league, team):
        services.register_team(league, team.owner, team)
        services.start_league(league)

        with pytest.raises(services.LeagueError):
            services.withdraw_team(league, team.owner, team)

        assert league.slots_taken == 1

    @pytest.mark.parametrize(
        "status",
        [LeagueStatus.FINISHED, LeagueStatus.CANCELLED],
    )
    def test_cannot_withdraw_from_a_league_that_is_over(self, league, team, status):
        """The docstring of withdraw_team() promises this, so it gets a test.

        Running was covered, finished and cancelled were not — the branch could be
        deleted and every other test stayed green, leaving a team free to erase
        itself from a season that is already in the books.
        """
        services.register_team(league, team.owner, team)
        league.status = status
        league.save(update_fields=["status"])

        with pytest.raises(services.LeagueError) as error:
            services.withdraw_team(league, team.owner, team)

        # Not a NotRegisteredError that happens to be a LeagueError as well.
        assert type(error.value) is services.LeagueError
        assert services.get_team_registration(league, team) is not None


class TestEmailPreferences:
    def test_the_league_opt_out_silences_the_mail_not_the_notification(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """See test_notifies_every_player for why the fixture is needed."""
        for membership in team.memberships.all():
            NotificationPreference.objects.create(
                user=membership.user,
                email_league=False,
            )
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            services.register_team(league, team.owner, team)

        assert len(mail.outbox) == 0
        assert league_notifications().count() == ROSTER_SIZE

    def test_players_without_an_opt_out_do_get_the_mail(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """Guards the test above from passing for the wrong reason."""
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            services.register_team(league, team.owner, team)

        assert len(mail.outbox) == ROSTER_SIZE


class TestStartLeague:
    def test_start_notifies_every_player_of_every_team(
        self,
        league,
        django_capture_on_commit_callbacks,
    ):
        """See test_notifies_every_player for why the fixture is needed."""
        teams = [team_with_players(ROSTER_SIZE) for _ in range(2)]
        for team in teams:
            services.register_team(league, team.owner, team)
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            services.start_league(league)

        league.refresh_from_db()
        assert league.status == LeagueStatus.RUNNING
        assert league_notifications().count() == 2 * ROSTER_SIZE

    def test_withdrawn_teams_are_not_notified(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        gone = team_with_players(ROSTER_SIZE)
        services.register_team(league, team.owner, team)
        services.register_team(league, gone.owner, gone)
        services.withdraw_team(league, gone.owner, gone)
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            services.start_league(league)

        assert league_notifications().count() == ROSTER_SIZE
        assert not league_notifications(recipient=gone.owner).exists()

    def test_starting_twice_notifies_only_once(
        self,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """Idempotency, and it is not cosmetic.

        Two overlapping runs of the periodic task that starts due leagues would
        otherwise start the same league twice and send every player a second round
        of notifications. The status is re-checked after the row lock, so the
        second call finds the league running and returns without doing anything.
        """
        services.register_team(league, team.owner, team)
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            services.start_league(league)
            services.start_league(league)

        assert league_notifications().count() == ROSTER_SIZE

    def test_a_player_on_two_rosters_is_notified_once(
        self,
        league,
        django_capture_on_commit_callbacks,
    ):
        """A player who changed teams mid-season sits on two snapshots.

        The snapshots are frozen, so leaving a team does not take the player off
        its roster. Both his old and his new team are in the league, and he must
        still be told exactly once that it started.
        """
        old_team = team_with_players(ROSTER_SIZE)
        services.register_team(league, old_team.owner, old_team)
        new_team = team_with_players(ROSTER_SIZE)

        switcher = old_team.memberships.filter(role=TeamRole.PLAYER).first().user
        switcher.team_membership.delete()
        TeamMembership.objects.create(
            team=new_team,
            user=switcher,
            role=TeamRole.PLAYER,
        )
        services.register_team(league, new_team.owner, new_team)
        Notification.objects.all().delete()

        with django_capture_on_commit_callbacks(execute=True):
            services.start_league(league)

        # Proves the setup really produced the double entry, i.e. the test is not
        # vacuous: 5 on the old roster + 6 on the new one, the switcher on both.
        assert LeagueRosterEntry.objects.filter(user=switcher).count() == 2
        assert league_notifications(recipient=switcher).count() == 1
        assert league_notifications().count() == 2 * ROSTER_SIZE

    def test_a_finished_league_cannot_be_started(self, league):
        league.status = LeagueStatus.FINISHED
        league.save(update_fields=["status"])

        with pytest.raises(services.LeagueError):
            services.start_league(league)


class TestSlotsAnnotation:
    def test_an_annotated_queryset_costs_no_count_per_league(
        self,
        league,
        team,
        django_assert_num_queries,
    ):
        """slots_taken, is_full and is_registration_open ride on the annotation.

        Without this, a league list that shows "3 / 8" and a register button costs
        a COUNT per row, and the annotation the admin already adds would be dead
        weight. One query for the whole page, including the properties.
        """
        services.register_team(league, team.owner, team)
        annotated = League.objects.annotate(
            **{
                SLOTS_TAKEN_ANNOTATION: Count(
                    "registrations",
                    filter=Q(registrations__status=RegistrationStatus.CONFIRMED),
                ),
            },
        )

        with django_assert_num_queries(1):
            leagues = list(annotated)
            assert [entry.slots_taken for entry in leagues] == [1]
            assert leagues[0].slots_free == leagues[0].max_teams - 1
            assert not leagues[0].is_full
            assert leagues[0].is_registration_open

    def test_without_the_annotation_the_league_counts_for_itself(self, league, team):
        """The fallback still has to work — the services rely on it.

        register_team() re-reads the league with select_for_update().get(), which
        yields an unannotated row on purpose: the capacity check after the lock has
        to count for real, not reuse a number from before the lock.
        """
        services.register_team(league, team.owner, team)

        assert League.objects.get(pk=league.pk).slots_taken == 1


class TestRegistrationWindow:
    def test_open_registration(self):
        league = LeagueFactory(status=LeagueStatus.DRAFT)

        services.open_registration(league)

        league.refresh_from_db()
        assert league.status == LeagueStatus.REGISTRATION_OPEN
        assert league.is_registration_open

    def test_close_registration(self, league):
        services.close_registration(league)

        league.refresh_from_db()
        assert league.status == LeagueStatus.REGISTRATION_CLOSED
        assert not league.is_registration_open

    def test_reopening_an_already_open_league_writes_nothing(self, league):
        """Idempotency, and this time the test can actually see it.

        The previous version only asserted the status that came back — which is
        REGISTRATION_OPEN whether the early return is there or not, so it stayed
        green with the idempotency deleted. What has to hold is that a no-op does
        not touch the row.
        """
        with CaptureQueriesContext(connection) as captured:
            result = services.open_registration(league)

        assert result.status == LeagueStatus.REGISTRATION_OPEN
        updates = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith("UPDATE")
        ]
        assert updates == []

    def test_a_running_league_cannot_reopen_its_registration(self, league):
        services.start_league(league)

        with pytest.raises(services.LeagueError):
            services.open_registration(league)

    @pytest.mark.parametrize(
        "status",
        [LeagueStatus.FINISHED, LeagueStatus.CANCELLED],
    )
    def test_a_league_that_is_over_cannot_change_its_registration(self, league, status):
        """Same untested branch as in withdraw_team(), in _transition() this time."""
        league.status = status
        league.save(update_fields=["status"])

        with pytest.raises(services.LeagueError):
            services.open_registration(league)
        with pytest.raises(services.LeagueError):
            services.close_registration(league)

        league.refresh_from_db()
        assert league.status == status


class TestGetRegistration:
    def test_returns_the_confirmed_registration(self, league, team):
        assert services.get_team_registration(league, team) is None

        registration = services.register_team(league, team.owner, team)

        assert services.get_team_registration(league, team) == registration
        assert services.get_user_registration(league, team.owner) == registration

    def test_a_withdrawn_registration_is_history(self, league, team):
        services.register_team(league, team.owner, team)
        services.withdraw_team(league, team.owner, team)

        assert services.get_team_registration(league, team) is None

    def test_a_user_without_a_team_has_no_registration(self, league):
        assert services.get_user_registration(league, UserFactory()) is None


@only_postgres
def test_two_teams_racing_for_the_last_slot(transactional_db, monkeypatch):
    """Exactly one of two simultaneous registrations may win the last slot.

    Without the select_for_update() lock in register_team() both threads read
    slots_taken == 0, both pass the is_full check and the league ends up
    overbooked. A UniqueConstraint cannot catch that: this is about a count, not a
    duplicate row.

    The race is choreographed, not hoped for. Starting both threads at a barrier
    and letting them run is a *probabilistic* detector: the critical section is
    about a millisecond long, and a thread that happens to lag two statements
    behind reads slots_taken == 1, raises LeagueFullError and turns the test green
    even though the lock is gone. So instead the winner is frozen with a monkeypatch
    *after* it has counted the slots and *before* it inserts:

    * with the lock, the loser is still stuck on ``SELECT ... FOR UPDATE`` at that
      point, and once the winner commits it counts 1 and is rejected;
    * without the lock, the loser sails past its own count (which is still 0,
      nothing is committed yet), inserts, and the league ends up with two teams in
      one slot — the assertions below fail.

    The freeze has to sit after the count, not directly after the ``FOR UPDATE``:
    freeze it earlier and an unlocked winner would do its counting *after* the loser
    committed, see 1, and raise LeagueFullError itself — green again, for the wrong
    reason.

    transactional_db, not django_db: the plain db fixture wraps the test in a
    transaction that is never committed, so the second thread would block forever on
    a lock the first one never releases. Threads also each need their own connection
    — Django's are thread-local. It is opened before the choreography starts (the
    TCP handshake must not be part of the race) and closed again afterwards, or the
    flush at the end of the test hangs on it.

    NOT YET RUN AGAINST POSTGRESQL. Neither Docker nor a local PostgreSQL was
    available on the development machine, and on SQLite the test is skipped for the
    reason above. Its choreography was verified against SQLite, which behaves
    exactly like the missing lock (see the commit message), where it fails as it
    should. The first CI run is what confirms it passes with the lock in place —
    treat a failure there as a real finding about this test, not about the service.
    """
    league = LeagueFactory(max_teams=1)
    team_a = team_with_players(ROSTER_SIZE)
    team_b = team_with_players(ROSTER_SIZE)

    counted = threading.Event()
    resume = threading.Event()
    frozen: list[bool] = []
    original_is_full = League.is_full.fget

    def is_full_then_freeze(self) -> bool:
        # Runs inside register_team(), under the row lock: the COUNT has happened,
        # the INSERT has not. Only the first caller is frozen — the second one has
        # to be free to run into the lock.
        full = original_is_full(self)
        if not frozen:
            frozen.append(True)
            counted.set()
            resume.wait(timeout=RACE_TIMEOUT_SECONDS)
        return full

    monkeypatch.setattr(League, "is_full", property(is_full_then_freeze))

    results: dict[str, str] = {}

    def register(team, key):
        # Connect before the race, not during it.
        connections["default"].ensure_connection()
        try:
            services.register_team(league, team.owner, team)
        except services.LeagueError as error:
            results[key] = type(error).__name__
        else:
            results[key] = "registered"
        finally:
            connections.close_all()

    winner = threading.Thread(target=register, args=(team_a, "a"))
    winner.start()
    assert counted.wait(timeout=RACE_TIMEOUT_SECONDS), (
        "the first thread never reached the capacity check"
    )

    loser = threading.Thread(target=register, args=(team_b, "b"))
    loser.start()
    # Long enough for the loser to reach its own SELECT ... FOR UPDATE and block
    # there. Without the lock it gets all the way past its capacity check in this
    # window, which is exactly the overbooking this test is here to catch.
    time.sleep(RACE_HANDOVER_SECONDS)
    resume.set()

    for thread in (winner, loser):
        thread.join(timeout=RACE_TIMEOUT_SECONDS)

    assert sorted(results.values()) == ["LeagueFullError", "registered"]
    assert (
        LeagueRegistration.objects.filter(
            status=RegistrationStatus.CONFIRMED,
        ).count()
        == 1
    )
    assert league.slots_taken == 1
