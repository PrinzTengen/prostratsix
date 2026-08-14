from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationCategory(models.TextChoices):
    SECURITY = "security", _("Security")
    GENERAL = "general", _("General")
    TEAM = "team", _("Team")
    LEAGUE = "league", _("League")


class NotificationLevel(models.TextChoices):
    INFO = "info", _("Info")
    SUCCESS = "success", _("Success")
    WARNING = "warning", _("Warning")
    ERROR = "error", _("Error")


class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(recipient=user)

    def unread(self):
        return self.filter(is_read=False)


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    level = models.CharField(
        max_length=20,
        choices=NotificationLevel,
        default=NotificationLevel.INFO,
    )
    category = models.CharField(
        max_length=20,
        choices=NotificationCategory,
        default=NotificationCategory.GENERAL,
    )
    url = models.URLField(blank=True)
    actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Quick actions rendered as POST buttons, e.g. "
        '[{"label": "Accept", "url": "/teams/invitations/<token>/accept/"}].',
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.recipient} — {self.title}"


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    email_security = models.BooleanField(
        default=True,
        verbose_name=_("E-mail me about security events"),
    )
    email_general = models.BooleanField(
        default=True,
        verbose_name=_("E-mail me about general announcements"),
    )
    email_team = models.BooleanField(
        default=True,
        verbose_name=_("E-mail me about team events"),
    )
    email_league = models.BooleanField(
        default=True,
        verbose_name=_("E-mail me about league events"),
    )

    def __str__(self):
        return f"{self.user}'s notification preferences"

    def allows_email(self, category: str) -> bool:
        if category == NotificationCategory.SECURITY:
            return self.email_security
        if category == NotificationCategory.TEAM:
            return self.email_team
        if category == NotificationCategory.LEAGUE:
            return self.email_league
        return self.email_general
