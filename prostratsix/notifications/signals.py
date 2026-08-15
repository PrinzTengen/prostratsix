from allauth.account.signals import email_added
from allauth.account.signals import email_changed
from allauth.account.signals import password_changed
from allauth.account.signals import password_reset
from django.conf import settings
from django.contrib.sites.models import Site
from django.dispatch import receiver

from .models import NotificationCategory
from .models import NotificationLevel
from .services import notify


def sync_site_domain(sender, **kwargs):
    """Keep django.contrib.sites in sync with DJANGO_SITE_DOMAIN/-NAME.

    Connected in NotificationsConfig.ready() with sender=<app config
    instance> (post_migrate is sent with the instance, not the class —
    a plain ``@receiver(post_migrate, sender=NotificationsConfig)`` here
    would silently never match). Runs once per `migrate` invocation so
    absolute URLs built via Site.objects.get_current() (notification
    e-mails, invite links) point at the right host per environment
    instead of the "example.com" placeholder the sites migration ships
    with.
    """
    Site.objects.update_or_create(
        id=settings.SITE_ID,
        defaults={"domain": settings.DJANGO_SITE_DOMAIN, "name": settings.DJANGO_SITE_NAME},
    )


try:  # allauth.mfa ships its own signals
    from allauth.mfa.signals import authenticator_added
except ImportError:  # pragma: no cover - depends on allauth version
    authenticator_added = None


@receiver(password_changed)
def on_password_changed(sender, request, user, **kwargs):
    notify(
        user,
        title="Your password was changed",
        message="If you did not make this change, please reset your password "
        "immediately and contact support.",
        level=NotificationLevel.WARNING,
        category=NotificationCategory.SECURITY,
    )


@receiver(password_reset)
def on_password_reset(sender, request, user, **kwargs):
    notify(
        user,
        title="Your password was reset",
        message="Your account password was reset successfully.",
        level=NotificationLevel.WARNING,
        category=NotificationCategory.SECURITY,
    )


@receiver(email_changed)
def on_email_changed(
    sender,
    request,
    user,
    from_email_address,
    to_email_address,
    **kwargs,
):
    notify(
        user,
        title="Your e-mail address was changed",
        message=f"Your primary e-mail address is now {to_email_address.email}.",
        level=NotificationLevel.WARNING,
        category=NotificationCategory.SECURITY,
    )


@receiver(email_added)
def on_email_added(sender, request, user, email_address, **kwargs):
    notify(
        user,
        title="A new e-mail address was added",
        message=f"{email_address.email} was added to your account.",
        level=NotificationLevel.INFO,
        category=NotificationCategory.SECURITY,
    )


if authenticator_added is not None:

    @receiver(authenticator_added)
    def on_authenticator_added(sender, request, user, authenticator, **kwargs):
        notify(
            user,
            title="Two-factor authentication enabled",
            message="A new authenticator was added to your account.",
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.SECURITY,
        )
