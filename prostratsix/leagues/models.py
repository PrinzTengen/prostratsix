from uuid import uuid4

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from prostratsix.teams.models import Team
from prostratsix.teams.models import TeamRole

DEFAULT_MIN_ROSTER_SIZE = 5


def league_image_upload_path(instance, filename):
    return f"leagues/league_{instance.uuid}/{filename}"


class LeagueStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    REGISTRATION_OPEN = "registration_open", _("Registration open")
    REGISTRATION_CLOSED = "registration_closed", _("Registration closed")
    RUNNING = "running", _("Running")
    FINISHED = "finished", _("Finished")
    CANCELLED = "cancelled", _("Cancelled")


class RegistrationStatus(models.TextChoices):
    CONFIRMED = "confirmed", _("Confirmed")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    DISQUALIFIED = "disqualified", _("Disqualified")


class League(models.Model):
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    name = models.CharField(_("League name"), max_length=150, unique=True)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    description = models.TextField(_("Description"), blank=True)
    banner = models.ImageField(
        upload_to=league_image_upload_path,
        blank=True,
        null=True,
    )
    region = models.CharField(_("Region"), max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=LeagueStatus,
        default=LeagueStatus.DRAFT,
    )
    max_teams = models.PositiveIntegerField(_("Maximum number of teams"))
    min_roster_size = models.PositiveIntegerField(
        _("Minimum roster size"),
        default=DEFAULT_MIN_ROSTER_SIZE,
        help_text=_("A team needs at least this many members to register."),
    )
    # Not evaluated anywhere yet: registrations are confirmed straight away, there
    # is no approval workflow. The field exists so that adding one later does not
    # need a schema migration on a running league.
    requires_approval = models.BooleanField(
        _("Registrations require approval"),
        default=False,
    )
    # All datetimes are stored in UTC and rendered in settings.TIME_ZONE
    # (Europe/Berlin) — server-wide, not per user. An admin in another timezone
    # entering "20:00" is entering 20:00 Berlin time, not their local time.
    registration_opens_at = models.DateTimeField(_("Registration opens at"))
    registration_closes_at = models.DateTimeField(_("Registration closes at"))
    starts_at = models.DateTimeField(_("Starts at"), null=True, blank=True)
    ends_at = models.DateTimeField(_("Ends at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-registration_opens_at", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    registration_closes_at__gt=models.F("registration_opens_at"),
                ),
                name="registration_closes_after_it_opens",
            ),
            models.CheckConstraint(
                condition=models.Q(max_teams__gt=0),
                name="league_has_at_least_one_slot",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("leagues:detail", kwargs={"slug": self.slug})

    @property
    def confirmed_registrations(self):
        return self.registrations.filter(status=RegistrationStatus.CONFIRMED)

    @property
    def slots_taken(self) -> int:
        return self.confirmed_registrations.count()

    @property
    def slots_free(self) -> int:
        return max(self.max_teams - self.slots_taken, 0)

    @property
    def is_full(self) -> bool:
        return self.slots_free == 0

    @property
    def is_registration_window_open(self) -> bool:
        """Status and time window allow registrations — capacity aside.

        Kept apart from :attr:`is_registration_open` so ``register_team()`` can
        tell "the league is closed" from "the league is full" and raise the
        error that actually says what happened.
        """
        now = timezone.now()
        return (
            self.status == LeagueStatus.REGISTRATION_OPEN
            and self.registration_opens_at <= now < self.registration_closes_at
        )

    @property
    def is_registration_open(self) -> bool:
        return self.is_registration_window_open and not self.is_full


class LeagueRegistration(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="league_registrations",
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="league_registrations",
    )
    status = models.CharField(
        max_length=20,
        choices=RegistrationStatus,
        default=RegistrationStatus.CONFIRMED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one confirmed registration per (league, team). A team that
            # withdrew drops out of the partial index and may register again.
            models.UniqueConstraint(
                fields=["league", "team"],
                condition=models.Q(status=RegistrationStatus.CONFIRMED),
                name="unique_confirmed_registration",
            ),
        ]

    def __str__(self):
        return f"{self.team} — {self.league} ({self.get_status_display()})"


class LeagueRosterEntry(models.Model):
    """A team member as of the moment the team registered.

    A snapshot, not a live view of the team: members who leave afterwards stay
    on it, and a withdrawal keeps it around as history. This is what later ties
    a player's match statistics to a specific league.
    """

    registration = models.ForeignKey(
        LeagueRegistration,
        on_delete=models.CASCADE,
        related_name="roster",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="league_roster_entries",
    )
    # Copied from TeamMembership.role at registration time, deliberately not a
    # reference to it — a role change afterwards must not rewrite the snapshot.
    role = models.CharField(
        max_length=20,
        choices=TeamRole,
        default=TeamRole.PLAYER,
    )

    class Meta:
        ordering = ["role", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["registration", "user"],
                name="unique_roster_entry_per_registration",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.registration.league} ({self.get_role_display()})"
