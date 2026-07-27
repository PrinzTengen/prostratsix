from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from . import services
from .models import SLOTS_TAKEN_ANNOTATION
from .models import League
from .models import LeagueRegistration
from .models import LeagueRosterEntry


class LeagueRegistrationInline(admin.TabularInline):
    """Shows the registrations of a league. Read-only, and that is the point.

    An editable inline is a way around the whole service layer: it can create a
    registration with no roster snapshot, no capacity check and no notification,
    or flip a status without telling anybody. Such a registration still counts
    towards ``slots_taken``, but ``start_league()`` walks the roster snapshots —
    so not one player of that team would ever hear that the league started.

    Registrations are made and withdrawn through the services (the frontend and
    the league actions), never by hand here.
    """

    model = LeagueRegistration
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["team", "status", "registered_by", "created_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj) -> bool:
        return False


class LeagueRosterEntryInline(admin.TabularInline):
    """The frozen roster of a registration — history, not an editing surface."""

    model = LeagueRosterEntry
    extra = 0
    can_delete = False
    fields = ["user", "role"]
    readonly_fields = fields

    def has_add_permission(self, request, obj) -> bool:
        return False


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
        # League.slots_taken picks the annotation up, so the changelist costs one
        # query instead of one COUNT per row.
        return super().get_queryset(request).with_slots_taken()

    @admin.display(description=_("Slots"), ordering=SLOTS_TAKEN_ANNOTATION)
    def slots_display(self, obj) -> str:
        return f"{obj.slots_taken} / {obj.max_teams}"

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
    """Browse registrations and their rosters — same read-only rule as the inline.

    Adding or editing one here would bypass the services just as the inline would,
    only from a different page. Deleting stays possible: that is a real cleanup
    operation and it takes the roster snapshot with it, rather than leaving a
    registration behind that nobody is notified about.
    """

    list_display = ["team", "league", "status", "registered_by", "created_at"]
    list_filter = ["status", "league"]
    search_fields = ["team__name", "team__tag", "league__name"]
    fields = ["league", "team", "status", "registered_by", "created_at"]
    readonly_fields = fields
    inlines = [LeagueRosterEntryInline]

    def has_add_permission(self, request) -> bool:
        return False
