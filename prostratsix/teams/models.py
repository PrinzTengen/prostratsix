from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

INVITATION_VALIDITY_DAYS = 7


def team_image_upload_path(instance, filename):
    return f"teams/team_{instance.uuid}/{filename}"


def default_invitation_expiry():
    return timezone.now() + timedelta(days=INVITATION_VALIDITY_DAYS)


class TeamRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    MANAGER = "manager", _("Manager")
    PLAYER = "player", _("Player")
    SUBSTITUTE = "substitute", _("Substitute")


# Roles that are allowed to manage the team (invite, remove members, edit team).
MANAGEMENT_ROLES = (TeamRole.OWNER, TeamRole.MANAGER)


class InvitationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")
    REVOKED = "revoked", _("Revoked")
    EXPIRED = "expired", _("Expired")


class Team(models.Model):
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    name = models.CharField(_("Team name"), max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True, blank=True)
    tag = models.CharField(
        _("Team tag"),
        max_length=10,
        unique=True,
        null=True,
        blank=True,
        help_text=_("Short clan tag, e.g. PSX."),
    )
    bio = models.TextField(_("Bio"), blank=True)
    thumbnail = models.ImageField(
        upload_to=team_image_upload_path,
        blank=True,
        null=True,
    )
    banner = models.ImageField(
        upload_to=team_image_upload_path,
        blank=True,
        null=True,
    )
    region = models.CharField(_("Region"), max_length=100, blank=True)
    discord = models.CharField(max_length=100, blank=True)
    twitter = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("teams:detail", kwargs={"slug": self.slug})

    @property
    def owner(self):
        membership = self.memberships.filter(role=TeamRole.OWNER).first()
        return membership.user if membership else None

    @property
    def managers(self):
        return [m.user for m in self.memberships.filter(role__in=MANAGEMENT_ROLES)]

    def has_management_rights(self, user) -> bool:
        return self.memberships.filter(
            user=user,
            role__in=MANAGEMENT_ROLES,
        ).exists()


class TeamMembership(models.Model):
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    # OneToOne enforces the "one team per user" rule at the database level.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_membership",
    )
    role = models.CharField(
        max_length=20,
        choices=TeamRole,
        default=TeamRole.PLAYER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["role", "joined_at"]

    def __str__(self):
        return f"{self.user} — {self.team} ({self.get_role_display()})"

    @property
    def can_manage(self) -> bool:
        return self.role in MANAGEMENT_ROLES


class TeamInvitation(models.Model):
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_team_invitations",
    )
    status = models.CharField(
        max_length=20,
        choices=InvitationStatus,
        default=InvitationStatus.PENDING,
    )
    token = models.UUIDField(default=uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invitation_expiry)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one pending invitation per (team, user).
            models.UniqueConstraint(
                fields=["team", "invited_user"],
                condition=models.Q(status=InvitationStatus.PENDING),
                name="unique_pending_invitation",
            ),
        ]

    def __str__(self):
        return f"Invitation for {self.invited_user} to {self.team}"

    def get_absolute_url(self) -> str:
        return reverse("teams:invitation_detail", kwargs={"token": self.token})

    @property
    def is_expired(self) -> bool:
        return self.expires_at < timezone.now()
