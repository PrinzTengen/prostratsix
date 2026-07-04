from django.contrib import admin

from .models import Team
from .models import TeamInvitation
from .models import TeamMembership


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "tag", "region", "created_at"]
    search_fields = ["name", "tag", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TeamMembershipInline]


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "team", "role", "joined_at"]
    list_filter = ["role"]
    search_fields = ["user__username", "user__email", "team__name"]
    autocomplete_fields = ["user", "team"]


@admin.register(TeamInvitation)
class TeamInvitationAdmin(admin.ModelAdmin):
    list_display = [
        "invited_user",
        "team",
        "status",
        "invited_by",
        "created_at",
        "expires_at",
    ]
    list_filter = ["status"]
    search_fields = ["invited_user__username", "invited_user__email", "team__name"]
    autocomplete_fields = ["invited_user", "invited_by", "team"]
