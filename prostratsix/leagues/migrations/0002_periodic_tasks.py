from django.db import migrations

TASK_NAME = "leagues.sync_league_statuses"
TASK_PATH = "prostratsix.leagues.tasks.sync_league_statuses"
EVERY_MINUTES = 5


def create_periodic_tasks(apps, schema_editor):
    """Register the status sweep with django-celery-beat.

    A data migration and get_or_create(), deliberately not CELERY_BEAT_SCHEDULE:
    the DatabaseScheduler calls update_or_create() on every settings entry each
    time beat starts, so it would silently reset whatever an admin changed on the
    task (a pause among it) at every deploy. Created here, the row belongs to the
    admin from then on.
    """
    interval_schedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    periodic_task = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _created = interval_schedule.objects.get_or_create(
        every=EVERY_MINUTES,
        period="minutes",
    )
    periodic_task.objects.get_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "interval": schedule},
    )


def delete_periodic_tasks(apps, schema_editor):
    """Take the task row back out. The IntervalSchedule stays: it is shared."""
    periodic_task = apps.get_model("django_celery_beat", "PeriodicTask")
    periodic_task.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("leagues", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_periodic_tasks, delete_periodic_tasks),
    ]
