from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    name = "prostratsix.notifications"
    verbose_name = _("Notifications")

    def ready(self):
        from . import signals  # noqa: PLC0415

        # post_migrate is sent with sender=<this instance>, so the
        # connection has to happen here rather than via @receiver in
        # signals.py (which would only ever see the class).
        post_migrate.connect(signals.sync_site_domain, sender=self)
