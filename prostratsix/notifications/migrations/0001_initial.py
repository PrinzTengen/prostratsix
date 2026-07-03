import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField(blank=True)),
                (
                    "level",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("success", "Success"),
                            ("warning", "Warning"),
                            ("error", "Error"),
                        ],
                        default="info",
                        max_length=20,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("security", "Security"),
                            ("general", "General"),
                        ],
                        default="general",
                        max_length=20,
                    ),
                ),
                ("url", models.URLField(blank=True)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "email_security",
                    models.BooleanField(
                        default=True,
                        verbose_name="E-mail me about security events",
                    ),
                ),
                (
                    "email_general",
                    models.BooleanField(
                        default=True,
                        verbose_name="E-mail me about general announcements",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_preference",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
