from .models import Notification

BELL_ITEM_LIMIT = 5


def unread_notifications(request):
    """Expose the unread notification count and the latest items to templates.

    This is the single source of the navbar bell's data. It runs on every
    render — including the renders of the bell partial itself — so neither the
    bell view nor the mark-read views build that context a second time.
    """
    if not request.user.is_authenticated:
        return {"unread_notifications_count": 0, "recent_notifications": []}

    qs = Notification.objects.for_user(request.user)
    return {
        "unread_notifications_count": qs.unread().count(),
        "recent_notifications": list(qs[:BELL_ITEM_LIMIT]),
    }
