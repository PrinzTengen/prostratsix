from django.contrib import admin

from . import services
from .models import Match
from .models import Season
from .models import SeasonRegistration


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "starts_at", "max_teams", "playoff_size"]
    list_filter = ["status"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(SeasonRegistration)
class SeasonRegistrationAdmin(admin.ModelAdmin):
    list_display = ["team", "season", "status", "requested_by", "requested_at"]
    list_filter = ["status", "season"]
    search_fields = ["team__name", "season__name"]
    autocomplete_fields = ["team", "season", "requested_by", "decided_by"]
    actions = ["approve", "reject"]

    @admin.action(description="Approve selected registrations")
    def approve(self, request, queryset):
        approved = 0
        for registration in queryset:
            try:
                services.approve_registration(registration, decided_by=request.user)
            except services.LeagueError as exc:
                self.message_user(request, f"{registration}: {exc}", level="error")
            else:
                approved += 1
        if approved:
            self.message_user(request, f"Approved {approved} registration(s).")

    @admin.action(description="Reject selected registrations")
    def reject(self, request, queryset):
        rejected = 0
        for registration in queryset:
            try:
                services.reject_registration(registration, decided_by=request.user)
            except services.LeagueError as exc:
                self.message_user(request, f"{registration}: {exc}", level="error")
            else:
                rejected += 1
        if rejected:
            self.message_user(request, f"Rejected {rejected} registration(s).")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = [
        "home_team",
        "away_team",
        "season",
        "round_number",
        "stage",
        "status",
        "score_home",
        "score_away",
    ]
    list_filter = ["status", "stage", "season"]
    search_fields = ["home_team__name", "away_team__name"]
    autocomplete_fields = [
        "home_team",
        "away_team",
        "season",
        "reported_by",
        "confirmed_by",
    ]
