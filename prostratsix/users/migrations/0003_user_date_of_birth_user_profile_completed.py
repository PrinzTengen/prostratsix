from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_user_check_over_18_user_first_name_user_last_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True, verbose_name="Date of Birth"),
        ),
        migrations.AddField(
            model_name="user",
            name="profile_completed",
            field=models.BooleanField(default=False),
        ),
    ]
