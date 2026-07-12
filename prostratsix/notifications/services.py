from __future__ import annotations

from .models import Notification
from .models import NotificationCategory
from .models import NotificationLevel
from .models import NotificationPreference


def notify(  # noqa: PLR0913
    user,
    title: str,
    message: str = "",
    *,
    level: str = NotificationLevel.INFO,
    category: str = NotificationCategory.GENERAL,
    url: str = "",
    send_email: bool = True,
) -> Notification:
    """
    Create an in-app notification for ``user`` and, depending on the user's
    preferences, enqueue an e-mail.
    """
    notification = Notification.objects.create(
        recipient=user,
        title=title,
        message=message,
        level=level,
        category=category,
        url=url,
    )

    if send_email and user.email:
        preference, _created = NotificationPreference.objects.get_or_create(user=user)
        if preference.allows_email(category):
            # Imported here to avoid importing Celery at app-load time.
            from .tasks import send_notification_email  # noqa: PLC0415

            send_notification_email.delay(notification.id)

    return notification


def notify_many(  # noqa: PLR0913
    users,
    title: str,
    message: str = "",
    *,
    level: str = NotificationLevel.INFO,
    category: str = NotificationCategory.GENERAL,
    url: str = "",
    send_email: bool = True,
) -> list[Notification]:
    """
    Create the same notification for many users in a bounded number of queries.

    Calling :func:`notify` in a loop costs two queries per user (the insert plus
    a ``get_or_create`` on the preferences); notifying every player of every team
    in a league is a three-digit number of users. This function stays at a
    handful of queries no matter how many users it is given.

    The price is ``bulk_create``: it bypasses ``Model.save()`` and any
    ``post_save`` signal on :class:`~prostratsix.notifications.models.Notification`.
    There is no such signal today (``notifications/signals.py`` only listens to
    allauth signals), so nothing is skipped — but whoever adds one, e.g. for a
    websocket push, has to hook it up here as well or it will silently not fire
    for bulk notifications.

    ``auto_now_add`` and the returned primary keys do work with ``bulk_create``
    on PostgreSQL and SQLite, both of which return the inserted rows (``RETURNING``
    clause). A backend without that support would hand back unsaved primary keys
    and break the e-mail dispatch below.

    E-mails are enqueued with ``delay_on_commit()`` rather than ``delay()``:
    callers run inside ``transaction.atomic()``, and a Celery worker picking the
    task up before that transaction commits would not see the notification row
    yet. ``send_notification_email`` swallows the resulting ``DoesNotExist``, so
    the e-mail would go missing without a trace.
    """
    users = list(users)
    if not users:
        return []

    notifications = Notification.objects.bulk_create(
        [
            Notification(
                recipient=user,
                title=title,
                message=message,
                level=level,
                category=category,
                url=url,
            )
            for user in users
        ],
    )

    if not send_email:
        return notifications

    # One query for the existing preferences, one to create the missing ones.
    preferences = {
        preference.user_id: preference
        for preference in NotificationPreference.objects.filter(user__in=users)
    }
    missing = [
        NotificationPreference(user=user)
        for user in users
        if user.pk not in preferences
    ]
    for preference in NotificationPreference.objects.bulk_create(missing):
        preferences[preference.user_id] = preference

    # Imported here to avoid importing Celery at app-load time.
    from .tasks import send_notification_email  # noqa: PLC0415

    for notification in notifications:
        preference = preferences[notification.recipient_id]
        if notification.recipient.email and preference.allows_email(category):
            send_notification_email.delay_on_commit(notification.id)

    return notifications
