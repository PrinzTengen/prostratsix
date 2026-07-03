from __future__ import annotations

import pytest
from allauth.account.signals import password_changed
from django.core import mail

from prostratsix.notifications.models import Notification
from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationPreference
from prostratsix.notifications.services import notify

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


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
