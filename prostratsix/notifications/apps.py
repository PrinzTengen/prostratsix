from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    name = "prostratsix.notifications"
    verbose_name = _("Notifications")

    def ready(self):
        import prostratsix.notifications.signals  # noqa: F401, PLC0415
