from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Case
from django.db.models import IntegerField
from django.db.models import Value
from django.db.models import When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import formats
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic import View

from prostratsix.teams.services import get_user_team

from . import services
from .models import League
from .models import LeagueStatus

PANEL_TEMPLATE = "leagues/partials/registration_panel.html"

# The order the directory shows leagues in: the ones a team can still join first,
# the ones that are over last. Drafts are not in it at all (see LeagueListView).
STATUS_RANK = Case(
    When(status=LeagueStatus.REGISTRATION_OPEN, then=Value(0)),
    When(status=LeagueStatus.REGISTRATION_CLOSED, then=Value(1)),
    When(status=LeagueStatus.RUNNING, then=Value(2)),
    When(status=LeagueStatus.FINISHED, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


def is_htmx(request) -> bool:
    """Tell an htmx request apart from a plain one.

    Same check as ``notifications.views.BellResponseMixin`` — one header, no
    extra dependency for it.
    """
    return request.headers.get("HX-Request") == "true"


def render_panel(request, league, feedback=None) -> HttpResponse:
    """Render the registration panel on its own, for an htmx swap.

    Stripped of surrounding whitespace for the same reason as the notification
    bell (see ``notifications.views.render_bell``): htmx swaps this with
    ``outerHTML`` and inserts every node of the response, text nodes included,
    so a leading and a trailing newline would pile up in the card on every swap.
    """
    context = {"league": league, "feedback": feedback}
    context.update(panel_state(league, request.user))
    markup = render_to_string(PANEL_TEMPLATE, context, request=request).strip()
    return HttpResponse(markup)


def _withdrawal_is_possible(league: League) -> bool:
    """Same rule as ``withdraw_team()``: nobody walks out of a running season."""
    return not (
        league.status == LeagueStatus.RUNNING
        or league.status in services.TERMINAL_STATUSES
    )


def _closed_window_reason(league: League) -> str:
    if timezone.now() < league.registration_opens_at:
        opens_at = formats.date_format(
            timezone.localtime(league.registration_opens_at),
            "SHORT_DATETIME_FORMAT",
        )
        return _("Die Anmeldung öffnet erst am %(date)s.") % {"date": opens_at}
    return _("Die Anmeldung ist geschlossen.")


def _registration_blocker(league: League, team) -> str:
    """The first guard of :func:`services.register_team` this team trips over.

    The permission guard is not in here — it runs a step earlier, in
    :func:`panel_state`, for the same reason the service checks it first. Same
    guards, same order otherwise, so the button is never offered for something
    the service would refuse; the empty string means "nothing is in the way".
    """
    if not league.is_registration_window_open:
        return _closed_window_reason(league)

    roster_size = team.memberships.count()
    if roster_size < league.min_roster_size:
        return _(
            "Dein Team braucht mindestens %(required)d Spieler (aktuell: %(actual)d).",
        ) % {"required": league.min_roster_size, "actual": roster_size}

    if league.is_full:
        return _("Die Liga ist voll.")

    return ""


def panel_state(league: League, user) -> dict:
    """What the registration panel may offer this user — and why it may not.

    ``blocking_reason`` is the point of the whole function: the user reads what
    actually stands in his way ("your team needs at least 5 players (currently:
    4)") instead of looking at a greyed-out button nobody explains. The reasons
    mirror the exceptions of the service layer, which stays the authority — a
    stale panel can at worst offer a button whose POST then comes back with the
    service's own error.

    The permission guard runs before the registration is even looked up, exactly
    as in the services: a plain player of a registered team gets the reason, not
    a withdraw button he is not allowed to press.

    Costs one query for the team, one for the registration and one for the roster
    size. ``league.is_full`` is free when the league came off an annotated
    queryset (``LeagueQuerySet.with_slots_taken()``) — which is what the views
    hand in.
    """
    state = {
        "user_team": None,
        "registration": None,
        "can_register": False,
        "can_withdraw": False,
        "blocking_reason": "",
    }

    if not user.is_authenticated:
        state["blocking_reason"] = _("Melde dich an, um dein Team zu registrieren.")
        return state

    team = get_user_team(user)
    state["user_team"] = team
    if team is None:
        state["blocking_reason"] = _("Du gehörst noch keinem Team an.")
        return state
    if not team.has_management_rights(user):
        state["blocking_reason"] = _(
            "Nur Owner und Manager können ein Team für eine Liga registrieren.",
        )
        return state

    registration = services.get_team_registration(league, team)
    state["registration"] = registration
    if registration is not None:
        state["can_withdraw"] = _withdrawal_is_possible(league)
        if not state["can_withdraw"]:
            state["blocking_reason"] = _(
                "Die Liga hat bereits begonnen — ein Rückzug ist nicht mehr möglich.",
            )
        return state

    blocker = _registration_blocker(league, team)
    state["blocking_reason"] = blocker
    state["can_register"] = not blocker
    return state


class LeagueListView(ListView):
    """The public league directory — no login needed to browse it."""

    model = League
    template_name = "leagues/league_list.html"
    context_object_name = "leagues"
    paginate_by = 12

    def get_queryset(self):
        # with_slots_taken() is not optional: every card shows "3 / 8" and its
        # status line goes through is_full, and both read League.slots_taken.
        # Without the annotation the page pays a COUNT per league and the query
        # count grows with the number of leagues on it.
        #
        # Drafts are left out: a league in draft is not announced yet, it is a
        # league the admin is still writing.
        return (
            League.objects.exclude(status=LeagueStatus.DRAFT)
            .with_slots_taken()
            .order_by(STATUS_RANK, "-registration_opens_at", "name")
        )


class LeagueDetailView(DetailView):
    """The public league page: participants, slots, and the registration panel."""

    model = League
    template_name = "leagues/league_detail.html"
    context_object_name = "league"

    def get_queryset(self):
        return League.objects.with_slots_taken()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        league = self.object
        context["registrations"] = league.confirmed_registrations.select_related(
            "team",
        ).order_by("created_at")
        context.update(panel_state(league, self.request.user))
        return context


class LeagueActionView(LoginRequiredMixin, View):
    """POST-only base for the register and the withdraw button.

    Progressive enhancement in both directions:

    * without JavaScript the browser posts the plain ``<form>`` and is redirected
      back to the league page, carrying a django message;
    * with htmx the same POST comes back as the re-rendered registration panel
      plus the ``HX-Trigger: notifications:refresh`` header, which makes the
      navbar bell refetch itself — the notification the service just wrote shows
      up at once instead of at the next 30-second poll.

    The htmx branch deliberately adds **no** django message: nothing would consume
    it (the panel is not a page and does not render the message block), so it
    would sit in the session and surface on whatever unrelated page the user opens
    next. The feedback goes into the panel instead.
    """

    success_message: str = ""

    def act(self, league: League, actor, team) -> None:
        raise NotImplementedError

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        team = get_user_team(request.user)
        if team is None:
            return self.respond(request, league, error=_("Du gehörst keinem Team an."))

        try:
            self.act(league, request.user, team)
        except services.LeagueError as error:
            return self.respond(request, league, error=str(error))
        return self.respond(request, league, success=self.success_message)

    def respond(self, request, league: League, *, error="", success=""):
        if is_htmx(request):
            # Read the league back annotated: its slot count moved a moment ago and
            # the panel has to show the number after this action, not before it.
            league = get_object_or_404(League.objects.with_slots_taken(), pk=league.pk)
            level = "danger" if error else "success"
            response = render_panel(
                request,
                league,
                feedback={"level": level, "text": error or success},
            )
            if success:
                # The bell partial listens for this on <body> (hx-trigger
                # "notifications:refresh from:body") and refetches itself.
                response["HX-Trigger"] = "notifications:refresh"
            return response

        if error:
            messages.error(request, error)
        else:
            messages.success(request, success)
        return redirect(league.get_absolute_url())


class LeagueRegisterView(LeagueActionView):
    success_message = _("Team für die Liga registriert.")

    def act(self, league: League, actor, team) -> None:
        services.register_team(league, actor, team)


class LeagueWithdrawView(LeagueActionView):
    success_message = _("Team aus der Liga zurückgezogen.")

    def act(self, league: League, actor, team) -> None:
        services.withdraw_team(league, actor, team)


league_list_view = LeagueListView.as_view()
league_detail_view = LeagueDetailView.as_view()

# transaction.non_atomic_requests, and it has to sit here, on the view callable:
# Django's handler reads _non_atomic_requests off the function it is about to call.
#
# WHY (do not "clean this up"): the project sets ATOMIC_REQUESTS = True
# (config/settings/base.py), so every request runs inside one transaction. The
# atomic() block in register_team()/withdraw_team() would then be a mere
# savepoint — and PostgreSQL releases the row lock that select_for_update() takes
# only when the OUTERMOST transaction commits, i.e. at the end of the request,
# template rendering included. The League row would stay locked for the whole
# request instead of the ~1 ms of the critical section, and every other
# registration for that league would queue behind it. That hurts precisely when
# the lock earns its keep: a registration deadline, everybody clicking at 20:00.
# transaction.on_commit() would slide to the end of the request as well, so the
# notification inserts and the Celery enqueues would ride inside it too.
#
# Without ATOMIC_REQUESTS the service's own atomic() is the real transaction: the
# lock is released when register_team() returns and on_commit fires there. The
# services manage their atomicity themselves — the request-wide transaction buys
# them nothing.
#
# The race test in test_services.py calls the service directly and would never
# notice this decorator going missing. test_views.py::TestAtomicRequests is what
# guards it.
league_register_view = transaction.non_atomic_requests(LeagueRegisterView.as_view())
league_withdraw_view = transaction.non_atomic_requests(LeagueWithdrawView.as_view())
