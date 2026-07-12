from __future__ import annotations

import pytest
from allauth.account.signals import password_changed
from django.core import mail
from django.urls import reverse

from prostratsix.notifications.context_processors import BELL_ITEM_LIMIT
from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationPreference
from prostratsix.notifications.services import notify
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BELL_TEMPLATE = "notifications/partials/bell.html"


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
