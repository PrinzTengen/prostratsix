from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DEFAULT_DB_ALIAS
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from prostratsix.leagues import services
from prostratsix.leagues import views
from prostratsix.leagues.models import League
from prostratsix.leagues.models import LeagueRegistration
from prostratsix.leagues.models import LeagueStatus
from prostratsix.leagues.models import RegistrationStatus
from prostratsix.leagues.tests.factories import LeagueFactory
from prostratsix.leagues.tests.factories import PlayerFactory
from prostratsix.leagues.tests.factories import team_with_players
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.teams.models import TeamRole

pytestmark = pytest.mark.django_db

ROSTER_SIZE = 5
HTMX_HEADERS = {"hx-request": "true"}
METHOD_NOT_ALLOWED = 405


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


def register_url(league: League) -> str:
    return reverse("leagues:register", kwargs={"slug": league.slug})


def withdraw_url(league: League) -> str:
    return reverse("leagues:withdraw", kwargs={"slug": league.slug})


class TestListView:
    def test_the_directory_is_public(self, client, league):
        response = client.get(reverse("leagues:list"))

        assert response.status_code == 200
        assert list(response.context["leagues"]) == [league]

    def test_a_draft_league_is_not_listed(self, client, league):
        """A draft is a league the admin is still writing — not an announcement."""
        LeagueFactory(status=LeagueStatus.DRAFT)

        response = client.get(reverse("leagues:list"))

        assert list(response.context["leagues"]) == [league]

    def test_the_leagues_a_team_can_still_join_come_first(self, client):
        finished = LeagueFactory(status=LeagueStatus.FINISHED)
        running = LeagueFactory(status=LeagueStatus.RUNNING)
        open_now = LeagueFactory(status=LeagueStatus.REGISTRATION_OPEN)
        closed = LeagueFactory(status=LeagueStatus.REGISTRATION_CLOSED)

        response = client.get(reverse("leagues:list"))

        assert list(response.context["leagues"]) == [
            open_now,
            closed,
            running,
            finished,
        ]

    def test_the_list_does_not_pay_a_count_per_league(
        self,
        client,
        django_assert_num_queries,
    ):
        """The query count must not grow with the number of leagues on the page.

        Every card prints "1 / 8" and asks is_registration_open, and both read
        League.slots_taken. Without the annotation from with_slots_taken() that is
        a COUNT per league — four extra queries here, and one per league on a real
        page. Drop the annotation from LeagueListView.get_queryset() and this test
        goes red on its own.
        """
        url = reverse("leagues:list")
        first = LeagueFactory()
        first_team = team_with_players(ROSTER_SIZE)
        services.register_team(first, first_team.owner, first_team)

        with CaptureQueriesContext(connection) as baseline:
            client.get(url)

        for _index in range(4):
            other = LeagueFactory()
            other_team = team_with_players(ROSTER_SIZE)
            services.register_team(other, other_team.owner, other_team)

        with django_assert_num_queries(len(baseline.captured_queries)):
            response = client.get(url)

        # Guards the assertion above from passing on an empty page.
        assert len(response.context["leagues"]) == 5


class TestDetailView:
    def test_the_league_page_is_public_and_lists_the_teams(self, client, league, team):
        services.register_team(league, team.owner, team)

        response = client.get(league.get_absolute_url())

        assert response.status_code == 200
        assert [
            registration.team for registration in response.context["registrations"]
        ] == [team]
        assert team.name in response.content.decode()

    def test_a_withdrawn_team_is_no_longer_a_participant(self, client, league, team):
        services.register_team(league, team.owner, team)
        services.withdraw_team(league, team.owner, team)

        response = client.get(league.get_absolute_url())

        assert list(response.context["registrations"]) == []

    def test_an_anonymous_visitor_is_told_to_log_in(self, client, league):
        response = client.get(league.get_absolute_url())

        assert response.context["can_register"] is False
        assert "Melde dich an" in str(response.context["blocking_reason"])

    def test_a_user_without_a_team_is_told_so(self, client, league):
        client.force_login(PlayerFactory())

        response = client.get(league.get_absolute_url())

        assert response.context["can_register"] is False
        assert "Team" in str(response.context["blocking_reason"])

    def test_a_plain_player_may_not_register_the_team(self, client, league, team):
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user
        client.force_login(player)

        response = client.get(league.get_absolute_url())

        assert response.context["can_register"] is False
        assert "Manager" in str(response.context["blocking_reason"])

    def test_the_owner_of_a_big_enough_team_may_register(self, client, league, team):
        client.force_login(team.owner)

        response = client.get(league.get_absolute_url())

        assert response.context["can_register"] is True
        assert response.context["blocking_reason"] == ""
        assert register_url(league) in response.content.decode()

    def test_a_roster_below_the_minimum_says_how_many_are_missing(self, client, league):
        """The whole point of blocking_reason: the number, not a grey button."""
        small = team_with_players(ROSTER_SIZE - 1)
        client.force_login(small.owner)

        response = client.get(league.get_absolute_url())

        reason = str(response.context["blocking_reason"])
        assert response.context["can_register"] is False
        assert str(league.min_roster_size) in reason
        assert str(ROSTER_SIZE - 1) in reason

    def test_a_full_league_says_it_is_full(self, client, team):
        full = LeagueFactory(max_teams=1)
        first = team_with_players(ROSTER_SIZE)
        services.register_team(full, first.owner, first)
        client.force_login(team.owner)

        response = client.get(full.get_absolute_url())

        assert response.context["can_register"] is False
        assert "voll" in str(response.context["blocking_reason"])

    def test_a_closed_registration_says_so(self, client, team):
        closed = LeagueFactory(status=LeagueStatus.REGISTRATION_CLOSED)
        client.force_login(team.owner)

        response = client.get(closed.get_absolute_url())

        assert response.context["can_register"] is False
        assert "geschlossen" in str(response.context["blocking_reason"])

    def test_a_registration_that_has_not_opened_yet_says_when_it_does(
        self,
        client,
        team,
    ):
        upcoming = LeagueFactory(
            registration_opens_at=timezone.now() + timedelta(days=1),
            registration_closes_at=timezone.now() + timedelta(days=8),
        )
        client.force_login(team.owner)

        response = client.get(upcoming.get_absolute_url())

        assert response.context["can_register"] is False
        assert "öffnet" in str(response.context["blocking_reason"])

    def test_a_registered_team_is_offered_the_withdrawal(self, client, league, team):
        services.register_team(league, team.owner, team)
        client.force_login(team.owner)

        response = client.get(league.get_absolute_url())

        assert response.context["registration"] is not None
        assert response.context["can_withdraw"] is True
        assert response.context["can_register"] is False
        assert withdraw_url(league) in response.content.decode()

    def test_a_plain_player_of_a_registered_team_gets_no_withdraw_button(
        self,
        client,
        league,
        team,
    ):
        """The permission guard runs before the registration is looked up.

        Move it behind, as a shorter panel_state() invites, and the player of a
        registered team is handed a withdraw button that the service then refuses
        — a button that only exists to produce an error message.
        """
        services.register_team(league, team.owner, team)
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user
        client.force_login(player)

        response = client.get(league.get_absolute_url())

        assert response.context["can_withdraw"] is False
        assert "Manager" in str(response.context["blocking_reason"])
        assert withdraw_url(league) not in response.content.decode()

    def test_a_running_league_cannot_be_withdrawn_from(self, client, league, team):
        services.register_team(league, team.owner, team)
        services.start_league(league)
        client.force_login(team.owner)

        response = client.get(league.get_absolute_url())

        assert response.context["can_withdraw"] is False
        assert "Rückzug" in str(response.context["blocking_reason"])
        assert withdraw_url(league) not in response.content.decode()


class TestRegisterView:
    def test_an_anonymous_post_is_sent_to_the_login(self, client, league):
        response = client.post(register_url(league))

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert not LeagueRegistration.objects.exists()

    def test_the_endpoint_takes_no_get(self, client, league, team):
        client.force_login(team.owner)

        response = client.get(register_url(league))

        assert response.status_code == METHOD_NOT_ALLOWED

    def test_the_owner_registers_and_is_sent_back_to_the_league(
        self,
        client,
        league,
        team,
    ):
        client.force_login(team.owner)

        response = client.post(register_url(league), follow=True)

        assert response.redirect_chain[-1][0] == league.get_absolute_url()
        registration = services.get_team_registration(league, team)
        assert registration is not None
        assert registration.registered_by == team.owner
        assert registration.roster.count() == ROSTER_SIZE
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "success"

    def test_the_service_error_reaches_the_user_as_a_message(self, client, team):
        """A closed league: the view must not swallow the service's own words."""
        closed = LeagueFactory(status=LeagueStatus.REGISTRATION_CLOSED)
        client.force_login(team.owner)

        response = client.post(register_url(closed), follow=True)

        assert not LeagueRegistration.objects.exists()
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "error"
        assert closed.name in str(message)

    def test_a_plain_player_is_refused(self, client, league, team):
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user
        client.force_login(player)

        response = client.post(register_url(league), follow=True)

        assert not LeagueRegistration.objects.exists()
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "error"

    def test_a_user_without_a_team_is_refused(self, client, league):
        client.force_login(PlayerFactory())

        response = client.post(register_url(league), follow=True)

        assert not LeagueRegistration.objects.exists()
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "error"

    def test_an_htmx_post_returns_the_panel_and_triggers_the_bell(
        self,
        client,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        """The htmx path: panel back, badge refreshed, no page reload.

        The HX-Trigger header is what the bell listens for on <body>. Without it
        the notification the registration just created would only show up at the
        next 30-second poll.

        django_capture_on_commit_callbacks: register_team() hands the
        notifications to transaction.on_commit(), which never fires under
        pytest.mark.django_db — see test_services.py.
        """
        client.force_login(team.owner)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(register_url(league), headers=HTMX_HEADERS)

        assert response.status_code == 200
        assert response["HX-Trigger"] == "notifications:refresh"

        content = response.content.decode()
        assert 'id="registration-panel"' in content
        assert withdraw_url(league) in content
        # The panel, not the whole page — htmx swaps this into the card.
        assert "<html" not in content

        assert services.get_team_registration(league, team) is not None
        assert (
            Notification.objects.filter(
                category=NotificationCategory.LEAGUE,
            ).count()
            == ROSTER_SIZE
        )

    def test_the_panel_shows_the_slot_count_after_the_registration(
        self,
        client,
        league,
        team,
    ):
        """Read the league back annotated, or the panel is one registration stale."""
        client.force_login(team.owner)

        response = client.post(register_url(league), headers=HTMX_HEADERS)

        free_slots = league.max_teams - 1
        assert f"{free_slots}" in response.content.decode()

    def test_a_failed_htmx_post_says_why_and_leaves_the_bell_alone(
        self,
        client,
        team,
    ):
        """No notification was written, so there is nothing for the bell to fetch."""
        closed = LeagueFactory(status=LeagueStatus.REGISTRATION_CLOSED)
        client.force_login(team.owner)

        response = client.post(register_url(closed), headers=HTMX_HEADERS)

        assert response.status_code == 200
        assert "HX-Trigger" not in response
        assert "alert-danger" in response.content.decode()
        assert not LeagueRegistration.objects.exists()

    def test_an_htmx_post_leaves_no_message_behind_for_the_next_page(
        self,
        client,
        league,
        team,
    ):
        """A django message added here would nobody consume — it would pop up later.

        The panel does not render the message block, so the message would sit in
        the session and surface on whatever page the user opens next, out of any
        context. The htmx branch therefore puts its feedback into the panel.
        """
        client.force_login(team.owner)
        client.post(register_url(league), headers=HTMX_HEADERS)

        response = client.get(league.get_absolute_url())

        assert list(response.context["messages"]) == []


class TestWithdrawView:
    def test_the_owner_withdraws_and_is_sent_back_to_the_league(
        self,
        client,
        league,
        team,
    ):
        services.register_team(league, team.owner, team)
        client.force_login(team.owner)

        response = client.post(withdraw_url(league), follow=True)

        assert response.redirect_chain[-1][0] == league.get_absolute_url()
        assert services.get_team_registration(league, team) is None
        assert LeagueRegistration.objects.get().status == RegistrationStatus.WITHDRAWN
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "success"

    def test_an_htmx_withdrawal_returns_the_panel_and_triggers_the_bell(
        self,
        client,
        league,
        team,
        django_capture_on_commit_callbacks,
    ):
        services.register_team(league, team.owner, team)
        Notification.objects.all().delete()
        client.force_login(team.owner)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(withdraw_url(league), headers=HTMX_HEADERS)

        assert response["HX-Trigger"] == "notifications:refresh"
        content = response.content.decode()
        assert 'id="registration-panel"' in content
        assert register_url(league) in content
        assert (
            Notification.objects.filter(
                category=NotificationCategory.LEAGUE,
            ).count()
            == ROSTER_SIZE
        )

    def test_a_team_that_is_not_registered_is_refused(self, client, league, team):
        client.force_login(team.owner)

        response = client.post(withdraw_url(league), follow=True)

        message = next(iter(response.context["messages"]))
        assert message.level_tag == "error"

    def test_a_plain_player_cannot_withdraw_the_team(self, client, league, team):
        services.register_team(league, team.owner, team)
        player = team.memberships.filter(role=TeamRole.PLAYER).first().user
        client.force_login(player)

        response = client.post(withdraw_url(league), follow=True)

        assert services.get_team_registration(league, team) is not None
        message = next(iter(response.context["messages"]))
        assert message.level_tag == "error"


class TestAtomicRequests:
    """The two POST views must run outside the request-wide transaction.

    ATOMIC_REQUESTS = True (config/settings/base.py) would otherwise turn the
    atomic() block in register_team() into a savepoint, and PostgreSQL holds the
    row lock select_for_update() takes until the OUTERMOST transaction commits —
    the end of the request, template rendering included. Every registration for a
    league would then queue behind the request duration of the one before it,
    which is worst exactly at a deadline.

    The race test in test_services.py calls the service directly and cannot see
    this. These two can.
    """

    def test_both_views_are_marked_non_atomic(self):
        for view in (views.league_register_view, views.league_withdraw_view):
            assert getattr(view, "_non_atomic_requests", set()) == {DEFAULT_DB_ALIAS}

    @pytest.mark.django_db(transaction=True)
    def test_the_service_is_called_outside_a_transaction(self, client, monkeypatch):
        """The one that would notice the decorator being deleted.

        Needs transaction=True: the plain db fixture wraps the test itself in an
        atomic block, so in_atomic_block would be True whatever the view does, and
        the assertion below would pass with the decorator gone.
        """
        league = LeagueFactory()
        team = team_with_players(ROSTER_SIZE)
        client.force_login(team.owner)

        seen: dict[str, bool] = {}
        original = services.register_team

        def spy(*args, **kwargs):
            seen["in_atomic_block"] = connection.in_atomic_block
            return original(*args, **kwargs)

        monkeypatch.setattr(services, "register_team", spy)

        client.post(register_url(league))

        assert seen["in_atomic_block"] is False
