from .models import Notification


def unread_notifications(request):
    """Expose the unread notification count and the latest items to templates."""
    if not request.user.is_authenticated:
        return {"unread_notifications_count": 0, "recent_notifications": []}

    qs = Notification.objects.for_user(request.user)
    return {
        "unread_notifications_count": qs.unread().count(),
        "recent_notifications": list(qs[:5]),
    }
