from django.contrib import admin
from django.contrib import messages
from django.db.models import Count
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from . import services
from .models import League
from .models import LeagueRegistration
from .models import LeagueRosterEntry
from .models import RegistrationStatus


class LeagueRegistrationInline(admin.TabularInline):
    model = LeagueRegistration
    extra = 0
    autocomplete_fields = ["team", "registered_by"]
    readonly_fields = ["created_at"]


class LeagueRosterEntryInline(admin.TabularInline):
    model = LeagueRosterEntry
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    """The primary place a league is created — there is no frontend for it."""

    list_display = [
        "name",
        "status",
        "slots_display",
        "registration_closes_at",
        "starts_at",
    ]
    list_filter = ["status", "region"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [LeagueRegistrationInline]
    actions = ["open_registration", "close_registration", "start_league"]

    def get_queryset(self, request):
        # slots_taken is a property and would run one COUNT per row otherwise.
        return (
            super()
            .get_queryset(request)
            .annotate(
                confirmed_count=Count(
                    "registrations",
                    filter=Q(registrations__status=RegistrationStatus.CONFIRMED),
                ),
            )
        )

    @admin.display(description=_("Slots"), ordering="confirmed_count")
    def slots_display(self, obj) -> str:
        return f"{obj.confirmed_count} / {obj.max_teams}"

    @admin.action(description=_("Open registration for the selected leagues"))
    def open_registration(self, request, queryset):
        self._run(
            request,
            queryset,
            services.open_registration,
            _("Registration opened for %(count)d league(s)."),
        )

    @admin.action(description=_("Close registration for the selected leagues"))
    def close_registration(self, request, queryset):
        self._run(
            request,
            queryset,
            services.close_registration,
            _("Registration closed for %(count)d league(s)."),
        )

    @admin.action(description=_("Start the selected leagues"))
    def start_league(self, request, queryset):
        self._run(
            request,
            queryset,
            services.start_league,
            _("%(count)d league(s) started."),
        )

    def _run(self, request, queryset, service, success_message) -> None:
        """Apply a service to every selected league, one league at a time.

        Deliberately not a ``queryset.update()``: the status transitions carry
        the row lock, the idempotency check and the notifications to the players
        — an update() would flip the column and tell nobody.

        Note that ATOMIC_REQUESTS = True wraps the whole admin request in one
        transaction, so the row locks the services take pile up: an action on
        twenty leagues holds twenty league rows until the response is rendered.
        Fine for an admin action on a handful of rows, worth knowing before this
        is pointed at a few hundred.
        """
        changed = 0
        for league in queryset:
            try:
                service(league)
            except services.LeagueError as error:
                self.message_user(
                    request,
                    f"{league}: {error}",
                    level=messages.ERROR,
                )
            else:
                changed += 1

        if changed:
            self.message_user(
                request,
                success_message % {"count": changed},
                level=messages.SUCCESS,
            )


@admin.register(LeagueRegistration)
class LeagueRegistrationAdmin(admin.ModelAdmin):
    list_display = ["team", "league", "status", "registered_by", "created_at"]
    list_filter = ["status", "league"]
    search_fields = ["team__name", "team__tag", "league__name"]
    autocomplete_fields = ["team", "league", "registered_by"]
    inlines = [LeagueRosterEntryInline]
