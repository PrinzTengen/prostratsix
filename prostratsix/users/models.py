from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CharField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .helpers.create_r6_link import create_tracker_link


class User(AbstractUser):
    """
    Default custom user model for ProStratSix.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = CharField(_("First Name"), blank=True, max_length=255)  # type: ignore[assignment]
    last_name = CharField(_("Last Name"), blank=True, max_length=255)  # type: ignore[assignment]
    date_of_birth = models.DateField(_("Date of Birth"), null=True, blank=True)
    privacy_policy_accepted = models.BooleanField(default=False)
    check_over_18 = models.BooleanField(default=False)
    profile_completed = models.BooleanField(default=False)

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"uuid": self.uuid})


def profile_picture_upload_path(instance, filename):
    return f"avatars/user_{instance.user.id}/{filename}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(
        upload_to=profile_picture_upload_path,
        blank=True,
        null=True,
    )
    thumbnail = models.ImageField(
        upload_to=profile_picture_upload_path,
        blank=True,
        null=True,
    )
    bio = models.TextField(blank=True)
    position = models.CharField(max_length=100, blank=True)
    is_lft = models.BooleanField(default=False)
    discord = models.CharField(max_length=100, blank=True)
    r6_gamertag = models.CharField(max_length=100, blank=True)
    tracker_link = models.URLField(blank=True)

    # add teams if teams are implemented
    # rounds_played if rounds are implemented
    # win_rate if rounds are implemented

    def __str__(self):
        return f"{self.user.username}'s Profile"

    def save(self, *args, **kwargs):
        """
        Override the save method to ensure that the clean method is called,
        which will validate the R6 gamertag and create the tracker link.
        """
        self.full_clean()  # calls clean() and thus create_tracker_link()
        super().save(*args, **kwargs)

    def clean(self):
        """
        Override the clean method to validate the R6 gamertag
        and create the tracker link.
        """
        super().clean()
        create_tracker_link(self)
