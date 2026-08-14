from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class SeasonStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    REGISTRATION_OPEN = "registration_open", _("Registration open")
    ACTIVE = "active", _("Active")
    PLAYOFFS = "playoffs", _("Playoffs")
    COMPLETED = "completed", _("Completed")


class Season(models.Model):
    name = models.CharField(_("Name"), max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=SeasonStatus,
        default=SeasonStatus.DRAFT,
    )
    registration_opens_at = models.DateTimeField(_("Registration opens"))
    registration_closes_at = models.DateTimeField(_("Registration closes"))
    starts_at = models.DateTimeField(_("Season starts"))
    ends_at = models.DateTimeField(_("Season ends"), null=True, blank=True)
    max_teams = models.PositiveSmallIntegerField(default=16)
    # Configurable points system, applied by services.get_standings().
    points_win = models.PositiveSmallIntegerField(default=3)
    points_draw = models.PositiveSmallIntegerField(default=1)
    points_loss = models.PositiveSmallIntegerField(default=0)
    double_round_robin = models.BooleanField(
        _("Double round-robin"),
        default=True,
        help_text=_("Play a home and away leg against every opponent."),
    )
    playoff_size = models.PositiveSmallIntegerField(default=8)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("league:season_detail", kwargs={"slug": self.slug})

    @property
    def approved_teams(self):
        return [
            reg.team
            for reg in self.registrations.filter(
                status=RegistrationStatus.APPROVED,
            ).select_related("team")
        ]


class RegistrationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn")


class SeasonRegistration(models.Model):
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.CASCADE,
        related_name="season_registrations",
    )
    status = models.CharField(
        max_length=20,
        choices=RegistrationStatus,
        default=RegistrationStatus.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            # A team may only have one non-withdrawn registration per season.
            models.UniqueConstraint(
                fields=["season", "team"],
                condition=~models.Q(status=RegistrationStatus.WITHDRAWN),
                name="unique_active_season_registration",
            ),
        ]

    def __str__(self):
        return f"{self.team} — {self.season} ({self.get_status_display()})"


class MatchStage(models.TextChoices):
    REGULAR = "regular", _("Regular season")
    PLAYOFF = "playoff", _("Playoff")


class MatchStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    AWAITING_CONFIRMATION = "awaiting_confirmation", _("Awaiting confirmation")
    CONFIRMED = "confirmed", _("Confirmed")
    DISPUTED = "disputed", _("Disputed")
    FORFEITED = "forfeited", _("Forfeited")
    CANCELLED = "cancelled", _("Cancelled")


class Match(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="matches")
    stage = models.CharField(
        max_length=20,
        choices=MatchStage,
        default=MatchStage.REGULAR,
    )
    round_number = models.PositiveSmallIntegerField()
    home_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.CASCADE,
        related_name="home_matches",
    )
    away_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.CASCADE,
        related_name="away_matches",
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=25,
        choices=MatchStatus,
        default=MatchStatus.SCHEDULED,
    )
    score_home = models.PositiveSmallIntegerField(null=True, blank=True)
    score_away = models.PositiveSmallIntegerField(null=True, blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reported_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.TextField(blank=True)
    # Playoff bracket position — see league/services.py::generate_playoff_bracket
    # (planned separately, fields reserved here to avoid a later migration).
    bracket_round = models.CharField(max_length=30, blank=True)
    bracket_slot = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["round_number", "scheduled_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(home_team=models.F("away_team")),
                name="match_teams_differ",
            ),
        ]

    def __str__(self):
        home, away = self.home_team, self.away_team
        return f"{home} vs {away} — R{self.round_number} ({self.season})"

    def get_absolute_url(self) -> str:
        return reverse("league:match_detail", kwargs={"pk": self.pk})

    @property
    def winner(self):
        if (
            self.status != MatchStatus.CONFIRMED
            or self.score_home is None
            or self.score_away is None
        ):
            return None
        if self.score_home > self.score_away:
            return self.home_team
        if self.score_away > self.score_home:
            return self.away_team
        return None
