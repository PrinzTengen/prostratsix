from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import FormView
from django.views.generic import ListView
from django.views.generic import UpdateView
from django.views.generic import View

from . import services
from .forms import TeamForm
from .forms import TeamInviteForm
from .models import InvitationStatus
from .models import Team
from .models import TeamInvitation

User = get_user_model()


class TeamManagementRequiredMixin(LoginRequiredMixin):
    """Load the team by slug and require the user to have management rights."""

    def dispatch(self, request, *args, **kwargs):
        self.team = get_object_or_404(Team, slug=kwargs["slug"])
        if not request.user.is_authenticated or not self.team.has_management_rights(
            request.user,
        ):
            messages.error(request, _("You cannot manage this team."))
            return redirect(self.team.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)


class TeamListView(ListView):
    model = Team
    template_name = "teams/team_list.html"
    context_object_name = "teams"
    paginate_by = 12

    def get_queryset(self):
        qs = Team.objects.all()
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(tag__icontains=query))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        return context


class TeamDetailView(DetailView):
    model = Team
    template_name = "teams/team_detail.html"
    context_object_name = "team"

    def get_queryset(self):
        return Team.objects.prefetch_related("memberships__user")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        team = self.object
        user = self.request.user
        can_manage = user.is_authenticated and team.has_management_rights(user)
        context["can_manage"] = can_manage
        context["memberships"] = team.memberships.select_related("user")
        context["is_member"] = (
            user.is_authenticated
            and team.memberships.filter(
                user=user,
            ).exists()
        )
        context["is_owner"] = user.is_authenticated and team.owner == user
        if can_manage:
            context["pending_invitations"] = team.invitations.filter(
                status=InvitationStatus.PENDING,
            ).select_related("invited_user")
            context["invite_form"] = TeamInviteForm()
        return context


class TeamCreateView(LoginRequiredMixin, FormView):
    form_class = TeamForm
    template_name = "teams/team_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and (
            services.get_user_membership(request.user) is not None
        ):
            messages.info(request, _("You are already a member of a team."))
            team = services.get_user_team(request.user)
            return redirect(team.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        fields = {
            key: value
            for key, value in form.cleaned_data.items()
            if value not in (None, "", False)
        }
        try:
            team = services.create_team(self.request.user, **fields)
        except services.TeamError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Team created."))
        return redirect(team.get_absolute_url())


class TeamUpdateView(TeamManagementRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "teams/team_form.html"
    context_object_name = "team"

    def get_object(self, queryset=None):
        return self.team

    def get_success_url(self):
        messages.success(self.request, _("Team updated."))
        return self.team.get_absolute_url()


class TeamInviteView(TeamManagementRequiredMixin, View):
    def post(self, request, slug):
        form = TeamInviteForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Please provide a username or e-mail."))
            return redirect(self.team.get_absolute_url())

        identifier = form.cleaned_data["username_or_email"].strip()
        target = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier),
        ).first()
        if target is None:
            messages.error(request, _("No user found with that username or e-mail."))
            return redirect(self.team.get_absolute_url())

        try:
            services.invite_member(self.team, request.user, target)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Invitation sent to %(user)s.") % {"user": target},
            )
        return redirect(self.team.get_absolute_url())


class RevokeInvitationView(TeamManagementRequiredMixin, View):
    def post(self, request, slug, pk):
        invitation = get_object_or_404(TeamInvitation, pk=pk, team=self.team)
        try:
            services.revoke_invitation(invitation, request.user)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Invitation revoked."))
        return redirect(self.team.get_absolute_url())


class RemoveMemberView(TeamManagementRequiredMixin, View):
    def post(self, request, slug, pk):
        target = get_object_or_404(User, pk=pk)
        try:
            services.remove_member(self.team, request.user, target)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Member removed."))
        return redirect(self.team.get_absolute_url())


class TransferOwnershipView(TeamManagementRequiredMixin, View):
    def post(self, request, slug, pk):
        new_owner = get_object_or_404(User, pk=pk)
        try:
            services.transfer_ownership(self.team, request.user, new_owner)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Ownership transferred."))
        return redirect(self.team.get_absolute_url())


class DeleteTeamView(TeamManagementRequiredMixin, View):
    def post(self, request, slug):
        try:
            services.delete_team(self.team, request.user)
        except services.TeamError as exc:
            messages.error(request, str(exc))
            return redirect(self.team.get_absolute_url())
        messages.success(request, _("Team deleted."))
        return redirect("teams:list")


class LeaveTeamView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            services.leave_team(request.user)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("You left the team."))
        return redirect("teams:list")


class InvitationDetailView(LoginRequiredMixin, DetailView):
    model = TeamInvitation
    template_name = "teams/invitation_detail.html"
    context_object_name = "invitation"
    slug_field = "token"
    slug_url_kwarg = "token"

    def get_object(self, queryset=None):
        invitation = get_object_or_404(TeamInvitation, token=self.kwargs["token"])
        if invitation.invited_user_id != self.request.user.id:
            raise Http404
        return invitation


class InvitationAcceptView(LoginRequiredMixin, View):
    def post(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)
        try:
            services.accept_invitation(invitation, request.user)
        except services.TeamError as exc:
            messages.error(request, str(exc))
            return redirect("notifications:list")
        messages.success(request, _("You joined the team."))
        return redirect(invitation.team.get_absolute_url())


class InvitationDeclineView(LoginRequiredMixin, View):
    def post(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)
        try:
            services.decline_invitation(invitation, request.user)
        except services.TeamError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Invitation declined."))
        return redirect("notifications:list")


team_list_view = TeamListView.as_view()
team_detail_view = TeamDetailView.as_view()
team_create_view = TeamCreateView.as_view()
team_update_view = TeamUpdateView.as_view()
team_invite_view = TeamInviteView.as_view()
revoke_invitation_view = RevokeInvitationView.as_view()
remove_member_view = RemoveMemberView.as_view()
transfer_ownership_view = TransferOwnershipView.as_view()
delete_team_view = DeleteTeamView.as_view()
leave_team_view = LeaveTeamView.as_view()
invitation_detail_view = InvitationDetailView.as_view()
invitation_accept_view = InvitationAcceptView.as_view()
invitation_decline_view = InvitationDeclineView.as_view()
