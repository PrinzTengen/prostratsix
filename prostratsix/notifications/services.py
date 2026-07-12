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

            # Known debt, not an oversight: this should be delay_on_commit() too,
            # exactly as in notify_many() below, which spells out the argument.
            # notify() is called from teams' @transaction.atomic services, so the
            # same lost-e-mail risk exists here today. Switching it means moving
            # every existing caller's tests onto django_capture_on_commit_callbacks
            # — own ticket, deliberately deferred. What this is NOT is a licence to
            # "unify" notify_many() back onto delay().
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

    Calling :func:`notify` in a loop costs several queries per user (the insert
    plus a ``get_or_create`` on the preferences); notifying every player of every
    team in a league is a three-digit number of users. This function stays at two
    queries no matter how many users it is given.

    Unlike :func:`notify` it reads the preferences without ever writing them: a
    user who has no ``NotificationPreference`` row yet is treated with the model
    defaults. Creating the missing rows here would race against the preferences
    page — see the comment below.

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
    the e-mail would go missing without a trace. :func:`notify` above still uses
    ``delay()`` and carries the same latent risk — that is known debt on its own
    ticket, not a sign that this one here is wrong.
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

    # One query, read-only. Users without a row fall back to an unsaved instance
    # carrying the very same defaults — writing the missing rows here would race
    # against NotificationPreferenceView.get_object()'s get_or_create(): user is a
    # OneToOneField, so a single player opening the settings page mid-league-start
    # would break the bulk_create with an IntegrityError, and because we run from
    # transaction.on_commit() the league is already committed by then — nobody
    # would get an e-mail and the retry would find the league started and bail.
    # The row gets created on its own the first time that page is opened.
    preferences = {
        preference.user_id: preference
        for preference in NotificationPreference.objects.filter(user__in=users)
    }
    defaults = NotificationPreference()

    # Imported here to avoid importing Celery at app-load time.
    from .tasks import send_notification_email  # noqa: PLC0415

    # Walk both lists instead of following notification.recipient: it makes it
    # obvious that no foreign key is resolved here. strict=True holds bulk_create
    # to returning the objects in the order it was given them.
    for user, notification in zip(users, notifications, strict=True):
        preference = preferences.get(user.pk, defaults)
        if user.email and preference.allows_email(category):
            send_notification_email.delay_on_commit(notification.id)

    return notifications
