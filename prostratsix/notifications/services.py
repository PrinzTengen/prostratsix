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
    actions: list[dict[str, str]] | None = None,
    send_email: bool = True,
) -> Notification:
    """
    Create an in-app notification for ``user`` and, depending on the user's
    preferences, enqueue an e-mail.

    ``actions`` are rendered as inline POST buttons on the notification (e.g.
    accept/decline a team invitation) without requiring the user to open
    ``url`` first: ``[{"label": "Accept", "url": "/teams/.../accept/"}]``.
    """
    notification = Notification.objects.create(
        recipient=user,
        title=title,
        message=message,
        level=level,
        category=category,
        url=url,
        actions=actions or [],
    )

    if send_email and user.email:
        preference, _created = NotificationPreference.objects.get_or_create(user=user)
        if preference.allows_email(category):
            # Imported here to avoid importing Celery at app-load time.
            from .tasks import send_notification_email  # noqa: PLC0415

            send_notification_email.delay(notification.id)

    return notification
