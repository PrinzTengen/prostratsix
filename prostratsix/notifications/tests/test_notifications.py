from __future__ import annotations

from unittest import mock

import pytest
from allauth.account.signals import password_changed
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from prostratsix.notifications.context_processors import BELL_ITEM_LIMIT
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.models import NotificationPreference
from prostratsix.notifications.services import notify
from prostratsix.notifications.services import notify_many
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BELL_TEMPLATE = "notifications/partials/bell.html"

# One bulk_create for the notifications, one SELECT for the preferences — and
# that is it, for any user count. notify_many() never writes preference rows.
MAX_NOTIFY_MANY_QUERIES = 2


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def recipient(client):
    """A logged-in user with a completed profile.

    Without ``profile_completed`` the ProfileCompletionMiddleware redirects
    every request to the profile setup page.
    """
    user = UserFactory(profile_completed=True)
    client.force_login(user)
    return user


def make_notification(user, title="Hello", **kwargs):
    return Notification.objects.create(recipient=user, title=title, **kwargs)


class TestNotify:
    def test_creates_in_app_notification(self, user):
        notification = notify(
            user,
            "Hello",
            "body",
            category=NotificationCategory.GENERAL,
        )
        assert Notification.objects.filter(recipient=user).count() == 1
        assert notification.title == "Hello"

    def test_sends_email_when_allowed(self, user):
        mail.outbox.clear()
        notify(user, "Hello", "body", category=NotificationCategory.GENERAL)
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_respects_preference_opt_out(self, user):
        NotificationPreference.objects.update_or_create(
            user=user,
            defaults={"email_general": False},
        )
        mail.outbox.clear()
        notify(user, "Hello", "body", category=NotificationCategory.GENERAL)
        assert Notification.objects.filter(recipient=user).count() == 1
        assert len(mail.outbox) == 0

    def test_sends_without_an_on_commit_hook(self, user):
        """notify() still enqueues with delay(), not delay_on_commit().

        Only notify_many() moved to delay_on_commit(). Should notify() ever
        follow, this test starts failing (no on_commit callbacks run under
        django_db) and has to grow the django_capture_on_commit_callbacks
        fixture — it is not a licence to revert the change.
        """
        mail.outbox.clear()
        notify(user, "Hello", "body", category=NotificationCategory.LEAGUE)
        assert len(mail.outbox) == 1


class TestNotifyMany:
    def test_creates_a_notification_for_every_user(self):
        users = UserFactory.create_batch(3)

        notifications = notify_many(
            users,
            "League started",
            "Your first match is up.",
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.LEAGUE,
            url="https://example.com/leagues/spring/",
        )

        assert len(notifications) == 3
        for user in users:
            notification = Notification.objects.get(recipient=user)
            assert notification.title == "League started"
            assert notification.message == "Your first match is up."
            assert notification.level == NotificationLevel.SUCCESS
            assert notification.category == NotificationCategory.LEAGUE
            assert notification.url == "https://example.com/leagues/spring/"

    def test_query_count_does_not_grow_with_the_number_of_users(self):
        """The whole point of the function: a flat query count.

        notify() costs several queries per user, so a league start with a hundred
        players would be a few hundred queries. Notifying twenty users has to
        cost exactly as much as notifying one — if a stray per-user query ever
        creeps back in (a lazy recipient.email lookup, a get_or_create in a
        loop), it degrades silently and only shows up under load.

        Neither group has a NotificationPreference row here, which is the more
        expensive path; the count is the same either way, see
        test_users_with_an_existing_preference_cost_no_extra_query.
        """
        one_user = UserFactory.create_batch(1)
        twenty_users = UserFactory.create_batch(20)

        with CaptureQueriesContext(connection) as for_one:
            notify_many(one_user, "Hi", category=NotificationCategory.LEAGUE)
        with CaptureQueriesContext(connection) as for_twenty:
            notify_many(twenty_users, "Hi", category=NotificationCategory.LEAGUE)

        assert len(for_twenty) == len(for_one)
        assert len(for_twenty) <= MAX_NOTIFY_MANY_QUERIES

    def test_users_with_an_existing_preference_cost_no_extra_query(self):
        """The everyday path: users who have been through the settings page.

        Exercised separately because every other test hits the "no row yet" path,
        and a regression that only shows up once the row exists would go unseen.
        """
        users = UserFactory.create_batch(5)
        for user in users:
            NotificationPreference.objects.create(user=user)

        with CaptureQueriesContext(connection) as queries:
            notify_many(users, "Hi", category=NotificationCategory.LEAGUE)

        assert len(queries) <= MAX_NOTIFY_MANY_QUERIES
        assert Notification.objects.filter(recipient__in=users).count() == 5

    def test_does_not_write_preference_rows(
        self,
        django_capture_on_commit_callbacks,
    ):
        """notify_many() reads preferences, it never creates them.

        Creating the missing rows in a bulk_create used to race against
        NotificationPreferenceView.get_object()'s get_or_create() — user is a
        OneToOneField, so one player opening the settings page during a league
        start was enough for an IntegrityError. Since notify_many() runs from
        transaction.on_commit(), the league was already committed at that point:
        the exception killed the e-mail loop, nobody got a mail, and the retry
        found the league started and returned. Hence: read-only, defaults apply.
        """
        user = UserFactory()
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            notify_many([user], "League started", category=NotificationCategory.LEAGUE)

        assert not NotificationPreference.objects.filter(user=user).exists()
        # The default is opted in, so the mail goes out regardless.
        assert len(mail.outbox) == 1

    def test_survives_a_preference_created_while_it_runs(
        self,
        django_capture_on_commit_callbacks,
    ):
        """The race from above, deterministically: a row appears mid-call.

        The preferences page can insert the row at any moment. Nothing
        notify_many() does afterwards may collide with it.
        """
        users = UserFactory.create_batch(2)
        original_filter = NotificationPreference.objects.filter

        def filter_then_insert(*args, **kwargs):
            queryset = list(original_filter(*args, **kwargs))
            # Simulates the user hitting the settings page right now.
            NotificationPreference.objects.get_or_create(user=users[0])
            return queryset

        mail.outbox.clear()
        with (
            mock.patch.object(
                NotificationPreference.objects,
                "filter",
                side_effect=filter_then_insert,
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            notify_many(users, "League started", category=NotificationCategory.LEAGUE)

        # Proves the injected row really landed, i.e. the test is not vacuous.
        assert NotificationPreference.objects.filter(user=users[0]).exists()
        assert Notification.objects.filter(recipient__in=users).count() == 2
        assert len(mail.outbox) == 2

    def test_returns_notifications_with_primary_keys_and_created_at(self):
        """bulk_create() fills in the PKs and auto_now_add — verified, not assumed.

        Both are backend-dependent: they rely on the RETURNING clause, which
        PostgreSQL and SQLite support. notify_many() hands notification.id to the
        e-mail task, so if this ever stops holding, every bulk e-mail silently
        goes to the wrong row or none at all.
        """
        users = UserFactory.create_batch(2)

        notifications = notify_many(users, "Hi", category=NotificationCategory.LEAGUE)

        for notification in notifications:
            assert notification.pk is not None
            assert notification.created_at is not None
            # The PK really points at the stored row, it is not just a counter.
            assert Notification.objects.get(pk=notification.pk).title == "Hi"

    def test_sends_an_email_to_every_user(self, django_capture_on_commit_callbacks):
        """notify_many() enqueues with delay_on_commit(), so the fixture is required.

        delay_on_commit() registers the task via transaction.on_commit(), and
        under pytest.mark.django_db that callback never fires: the test runs in a
        transaction that gets rolled back, never committed. Without this fixture
        mail.outbox stays empty. Do not "fix" that by going back to delay() —
        delay_on_commit() is what keeps a Celery worker from picking the task up
        before the notification row is committed and losing the e-mail.
        """
        users = UserFactory.create_batch(2)
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            notify_many(users, "League started", category=NotificationCategory.LEAGUE)

        assert len(mail.outbox) == 2
        assert {message.to[0] for message in mail.outbox} == {u.email for u in users}

    def test_respects_the_league_opt_out(self, django_capture_on_commit_callbacks):
        """See test_sends_an_email_to_every_user for why the fixture is needed."""
        opted_out = UserFactory()
        NotificationPreference.objects.create(user=opted_out, email_league=False)
        subscribed = UserFactory()
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            notify_many(
                [opted_out, subscribed],
                "League started",
                category=NotificationCategory.LEAGUE,
            )

        # The opt-out only silences the e-mail, the in-app notification stays.
        assert Notification.objects.filter(recipient=opted_out).count() == 1
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [subscribed.email]

    def test_skips_users_without_an_email_address(
        self,
        django_capture_on_commit_callbacks,
    ):
        """See test_sends_an_email_to_every_user for why the fixture is needed."""
        without_email = UserFactory(email="")
        with_email = UserFactory()
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            notify_many(
                [without_email, with_email],
                "League started",
                category=NotificationCategory.LEAGUE,
            )

        assert Notification.objects.filter(recipient=without_email).count() == 1
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [with_email.email]

    def test_empty_user_list_is_a_no_op(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert notify_many([], "Nobody", category=NotificationCategory.LEAGUE) == []

    def test_send_email_false_creates_notifications_without_mail(
        self,
        django_capture_on_commit_callbacks,
    ):
        users = UserFactory.create_batch(2)
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            notify_many(
                users,
                "Quiet one",
                category=NotificationCategory.LEAGUE,
                send_email=False,
            )

        assert Notification.objects.filter(recipient__in=users).count() == 2
        assert len(mail.outbox) == 0


class TestSignals:
    def test_password_changed_creates_security_notification(self, user):
        password_changed.send(sender=user.__class__, request=None, user=user)
        assert Notification.objects.filter(
            recipient=user,
            category=NotificationCategory.SECURITY,
        ).exists()


class TestNotificationBell:
    def test_requires_login(self, client):
        response = client.get(reverse("notifications:bell"))
        assert response.status_code == 302

    def test_renders_unread_count(self, client, recipient):
        make_notification(recipient, "Unread one")
        make_notification(recipient, "Unread two")
        make_notification(recipient, "Read one", is_read=True)

        response = client.get(reverse("notifications:bell"))

        assert response.status_code == 200
        assert response.context["unread_notifications_count"] == 2
        assert "Unread one" in response.content.decode()

    def test_shows_at_most_five_notifications(self, client, recipient):
        for index in range(BELL_ITEM_LIMIT + 2):
            make_notification(recipient, f"Notification {index}")

        response = client.get(reverse("notifications:bell"))

        assert len(response.context["recent_notifications"]) == BELL_ITEM_LIMIT
        # Newest first — the two oldest ones fall off the end.
        assert "Notification 0" not in response.content.decode()

    def test_response_carries_the_polling_attributes(self, client, recipient):
        """Guards the polling chain, which fails silently when it breaks.

        The bell replaces itself (outerHTML), so every single response has to
        bring the htmx attributes along again. Render the trigger only on the
        first load — or wrap the <li> in an {% if %} — and polling stops dead
        after the first swap, with nothing in the console and every other test
        in this file still green.
        """
        response = client.get(reverse("notifications:bell"))

        content = response.content.decode()
        assert f'hx-get="{reverse("notifications:bell")}"' in content
        assert 'hx-swap="outerHTML"' in content
        assert "hx-trigger=" in content
        assert "every 30s" in content
        assert "visibilitychange" in content
        assert "notifications:refresh from:body" in content

    def test_response_has_no_surrounding_whitespace(self, client, recipient):
        """htmx inserts every node of the response, text nodes included.

        Leading or trailing whitespace in the markup is left behind in the
        navbar on every swap and piles up over a long session.
        """
        content = client.get(reverse("notifications:bell")).content.decode()

        assert content == content.strip()

    def test_hides_notifications_of_other_users(self, client, recipient):
        make_notification(UserFactory(), "Not yours")

        response = client.get(reverse("notifications:bell"))

        assert response.context["unread_notifications_count"] == 0
        assert "Not yours" not in response.content.decode()


class TestMarkRead:
    def test_htmx_request_returns_the_bell_partial(self, client, recipient):
        notification = make_notification(recipient, url="https://example.com/x/")

        response = client.post(
            reverse("notifications:mark_read", kwargs={"pk": notification.pk}),
            headers={"hx-request": "true"},
        )

        assert response.status_code == 200
        assert BELL_TEMPLATE in [template.name for template in response.templates]
        assert response.context["unread_notifications_count"] == 0
        notification.refresh_from_db()
        assert notification.is_read

    def test_plain_request_still_redirects(self, client, recipient):
        notification = make_notification(recipient, url="https://example.com/x/")

        response = client.post(
            reverse("notifications:mark_read", kwargs={"pk": notification.pk}),
        )

        assert response.status_code == 302
        assert response.url == notification.url
        notification.refresh_from_db()
        assert notification.is_read

    def test_cannot_mark_a_foreign_notification_read(self, client, recipient):
        notification = make_notification(UserFactory(), "Not yours")

        response = client.post(
            reverse("notifications:mark_read", kwargs={"pk": notification.pk}),
        )

        assert response.status_code == 404
        notification.refresh_from_db()
        assert not notification.is_read


class TestMarkAllRead:
    def test_htmx_request_returns_the_bell_partial(self, client, recipient):
        make_notification(recipient, "Unread one")
        make_notification(recipient, "Unread two")

        response = client.post(
            reverse("notifications:mark_all_read"),
            headers={"hx-request": "true"},
        )

        assert response.status_code == 200
        assert BELL_TEMPLATE in [template.name for template in response.templates]
        assert response.context["unread_notifications_count"] == 0
        assert not Notification.objects.for_user(recipient).unread().exists()

    def test_plain_request_still_redirects(self, client, recipient):
        make_notification(recipient, "Unread one")

        response = client.post(reverse("notifications:mark_all_read"))

        assert response.status_code == 302
        assert response.url == reverse("notifications:list")
        assert not Notification.objects.for_user(recipient).unread().exists()


class TestPreferences:
    def test_page_offers_the_league_opt_out(self, client, recipient):
        """The page renders the form with ``{{ form|crispy }}``.

        Listing a field on NotificationPreferenceForm is therefore all that puts
        it on the page — drop it from the form and the opt-out silently vanishes
        from the UI while the model field keeps defaulting to True.
        """
        response = client.get(reverse("notifications:preferences"))

        assert response.status_code == 200
        assert 'name="email_league"' in response.content.decode()

    def test_league_opt_out_can_be_switched_off(self, client, recipient):
        response = client.post(
            reverse("notifications:preferences"),
            data={"email_security": "on", "email_general": "on", "email_team": "on"},
        )

        assert response.status_code == 302
        preference = NotificationPreference.objects.get(user=recipient)
        assert preference.email_league is False
        assert preference.email_team is True
