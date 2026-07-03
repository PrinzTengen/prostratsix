from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from prostratsix.notifications.models import NotificationCategory
from prostratsix.notifications.models import NotificationLevel
from prostratsix.notifications.services import notify

User = get_user_model()


class Command(BaseCommand):
    help = "Send a general notification to all active users."

    def add_arguments(self, parser):
        parser.add_argument("title", type=str, help="Notification title")
        parser.add_argument(
            "--message",
            type=str,
            default="",
            help="Optional notification body",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Create in-app notifications only, do not send e-mails",
        )

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True)
        count = 0
        for user in users.iterator():
            notify(
                user,
                title=options["title"],
                message=options["message"],
                level=NotificationLevel.INFO,
                category=NotificationCategory.GENERAL,
                send_email=not options["no_email"],
            )
            count += 1
        self.stdout.write(
            self.style.SUCCESS(f"Sent notification to {count} user(s)."),
        )
