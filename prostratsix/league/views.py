from __future__ import annotations

from itertools import groupby

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic import View

from prostratsix.teams.models import Team
from prostratsix.teams.services import get_user_team

from . import services
from .forms import MatchDisputeForm
from .forms import MatchResultForm
from .forms import RegistrationRejectForm
from .models import Match
from .models import MatchStatus
from .models import RegistrationStatus
from .models import Season
from .models import SeasonRegistration
from .models import SeasonStatus


class StaffRequiredMixin(LoginRequiredMixin):
    """Require ``request.user.is_staff`` (league organizer action)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, _("You do not have permission to do this."))
            return redirect("league:season_list")
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------


class SeasonListView(ListView):
    model = Season
    template_name = "league/season_list.html"
    context_object_name = "seasons"


class SeasonDetailView(DetailView):
    model = Season
    template_name = "league/season_detail.html"
    context_object_name = "season"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        season = self.object
        user = self.request.user
        user_team = get_user_team(user) if user.is_authenticated else None
        user_registration = None
        if user_team is not None:
            user_registration = (
                season.registrations.filter(team=user_team)
                .exclude(status=RegistrationStatus.WITHDRAWN)
                .first()
            )
        context["user_team"] = user_team
        context["user_registration"] = user_registration
        context["can_register"] = (
            user.is_authenticated
            and user_team is not None
            and user_registration is None
            and season.status == SeasonStatus.REGISTRATION_OPEN
        )
        context["approved_teams"] = season.approved_teams
        context["is_staff"] = user.is_authenticated and user.is_staff
        if context["is_staff"]:
            context["pending_registrations"] = season.registrations.filter(
                status=RegistrationStatus.PENDING,
            ).select_related("team", "requested_by")
        return context


class SeasonRegisterView(LoginRequiredMixin, View):
    def post(self, request, slug):
        season = get_object_or_404(Season, slug=slug)
        team = get_user_team(request.user)
        if team is None:
            messages.error(
                request,
                _("You need a team before you can register for a season."),
            )
            return redirect(season.get_absolute_url())
        try:
            services.register_team_for_season(season, team, request.user)
        except services.LeagueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Registration submitted."))
        return redirect(season.get_absolute_url())


class SeasonScheduleView(DetailView):
    model = Season
    template_name = "league/season_schedule.html"
    context_object_name = "season"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        matches = list(
            self.object.matches.select_related("home_team", "away_team").order_by(
                "round_number",
                "id",
            ),
        )
        context["rounds"] = [
            {"round_number": round_number, "matches": list(round_matches)}
            for round_number, round_matches in groupby(
                matches,
                key=lambda m: m.round_number,
            )
        ]
        context["is_staff"] = (
            self.request.user.is_authenticated and self.request.user.is_staff
        )
        return context


class SeasonGenerateScheduleView(StaffRequiredMixin, View):
    def post(self, request, slug):
        season = get_object_or_404(Season, slug=slug)
        try:
            services.generate_schedule(season)
        except services.LeagueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Schedule generated."))
        return redirect("league:season_schedule", slug=season.slug)


class SeasonStandingsView(DetailView):
    model = Season
    template_name = "league/season_standings.html"
    context_object_name = "season"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["standings"] = services.get_standings(self.object)
        return context


# ---------------------------------------------------------------------------
# Registrations (organizer queue)
# ---------------------------------------------------------------------------


class RegistrationQueueView(StaffRequiredMixin, ListView):
    model = SeasonRegistration
    template_name = "league/registration_queue.html"
    context_object_name = "registrations"

    def get_queryset(self):
        return SeasonRegistration.objects.filter(
            status=RegistrationStatus.PENDING,
        ).select_related("team", "season", "requested_by")


class RegistrationApproveView(StaffRequiredMixin, View):
    def post(self, request, pk):
        registration = get_object_or_404(SeasonRegistration, pk=pk)
        try:
            services.approve_registration(registration, request.user)
        except services.LeagueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Registration approved."))
        return redirect("league:registration_queue")


class RegistrationRejectView(StaffRequiredMixin, View):
    def post(self, request, pk):
        registration = get_object_or_404(SeasonRegistration, pk=pk)
        form = RegistrationRejectForm(request.POST)
        reason = form.cleaned_data["reason"] if form.is_valid() else ""
        try:
            services.reject_registration(registration, request.user, reason=reason)
        except services.LeagueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Registration rejected."))
        return redirect("league:registration_queue")


class RegistrationWithdrawView(LoginRequiredMixin, View):
    def post(self, request, pk):
        registration = get_object_or_404(SeasonRegistration, pk=pk)
        try:
            services.withdraw_registration(registration, request.user)
        except services.LeagueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Registration withdrawn."))
        return redirect(registration.season.get_absolute_url())


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------


class MatchDetailView(DetailView):
    model = Match
    template_name = "league/match_detail.html"
    context_object_name = "match"

    def get_queryset(self):
        return Match.objects.select_related(
            "home_team",
            "away_team",
            "season",
            "reported_by",
            "confirmed_by",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        match = self.object
        user = self.request.user
        can_report = False
        can_respond = False
        if user.is_authenticated:
            if match.status == MatchStatus.SCHEDULED:
                can_report = match.home_team.has_management_rights(
                    user,
                ) or match.away_team.has_management_rights(user)
            elif match.status == MatchStatus.AWAITING_CONFIRMATION:
                try:
                    confirming_team = services.get_confirming_team(match)
                except services.MatchError:
                    confirming_team = None
                can_respond = (
                    confirming_team is not None
                    and confirming_team.has_management_rights(
                        user,
                    )
                )
        context["can_report"] = can_report
        context["can_respond"] = can_respond
        context["is_staff"] = user.is_authenticated and user.is_staff
        context["result_form"] = MatchResultForm()
        context["dispute_form"] = MatchDisputeForm()
        return context


class MatchReportView(LoginRequiredMixin, View):
    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        team = get_user_team(request.user)
        form = MatchResultForm(request.POST)
        if team is None or not form.is_valid():
            messages.error(request, _("Please provide a valid score."))
            return redirect(match.get_absolute_url())
        try:
            services.report_result(
                match,
                team,
                form.cleaned_data["score_home"],
                form.cleaned_data["score_away"],
                request.user,
            )
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Result reported. Waiting for the opponent to confirm."),
            )
        return redirect(match.get_absolute_url())


class MatchConfirmView(LoginRequiredMixin, View):
    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            services.confirm_result(match, request.user)
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Result confirmed."))
        return redirect(match.get_absolute_url())


class MatchDisputeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        form = MatchDisputeForm(request.POST)
        reason = form.cleaned_data["reason"] if form.is_valid() else ""
        try:
            services.dispute_result(match, request.user, reason=reason)
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Result disputed. League staff will review it."),
            )
        return redirect(match.get_absolute_url())


class MatchResolveDisputeView(StaffRequiredMixin, View):
    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        form = MatchResultForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Please provide a valid score."))
            return redirect(match.get_absolute_url())
        try:
            services.resolve_dispute(
                match,
                request.user,
                form.cleaned_data["score_home"],
                form.cleaned_data["score_away"],
            )
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Dispute resolved."))
        return redirect(match.get_absolute_url())


class MatchForfeitView(StaffRequiredMixin, View):
    def post(self, request, pk, team_pk):
        match = get_object_or_404(Match, pk=pk)
        team = get_object_or_404(Team, pk=team_pk)
        try:
            services.forfeit_match(match, request.user, team)
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Match forfeited."))
        return redirect(match.get_absolute_url())


class MatchCancelView(StaffRequiredMixin, View):
    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            services.cancel_match(match, request.user)
        except services.MatchError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Match cancelled."))
        return redirect(match.get_absolute_url())


season_list_view = SeasonListView.as_view()
season_detail_view = SeasonDetailView.as_view()
season_register_view = SeasonRegisterView.as_view()
season_schedule_view = SeasonScheduleView.as_view()
season_generate_schedule_view = SeasonGenerateScheduleView.as_view()
season_standings_view = SeasonStandingsView.as_view()
registration_queue_view = RegistrationQueueView.as_view()
registration_approve_view = RegistrationApproveView.as_view()
registration_reject_view = RegistrationRejectView.as_view()
registration_withdraw_view = RegistrationWithdrawView.as_view()
match_detail_view = MatchDetailView.as_view()
match_report_view = MatchReportView.as_view()
match_confirm_view = MatchConfirmView.as_view()
match_dispute_view = MatchDisputeView.as_view()
match_resolve_dispute_view = MatchResolveDisputeView.as_view()
match_forfeit_view = MatchForfeitView.as_view()
match_cancel_view = MatchCancelView.as_view()
