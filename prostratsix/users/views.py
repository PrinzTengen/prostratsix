from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import ListView
from django.views.generic import RedirectView
from django.views.generic import UpdateView

from prostratsix.users.forms import ProfileSetupForm
from prostratsix.users.models import User
from prostratsix.users.models import UserProfile

if TYPE_CHECKING:
    from django.db.models import QuerySet


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    slug_field = "uuid"
    slug_url_kwarg = "uuid"


user_detail_view = UserDetailView.as_view()


class UserListView(LoginRequiredMixin, ListView):
    """Roster / player directory with search and an LFT filter."""

    model = User
    template_name = "users/user_list.html"
    context_object_name = "users"
    paginate_by = 12

    def get_queryset(self) -> QuerySet[User]:
        qs = User.objects.select_related("profile").order_by("username")
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(username__icontains=query)
                | Q(name__icontains=query)
                | Q(profile__r6_gamertag__icontains=query),
            )
        if self.request.GET.get("lft") == "1":
            qs = qs.filter(profile__is_lft=True)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        context["lft"] = self.request.GET.get("lft") == "1"
        return context


user_list_view = UserListView.as_view()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    fields = ["name"]
    success_message = _("Information successfully updated")

    def get_success_url(self) -> str:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user.get_absolute_url()

    def get_object(self, queryset: QuerySet | None = None) -> User:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self) -> str:
        user = self.request.user
        if not user.profile_completed:
            return reverse("users:profile_setup")
        return reverse("users:detail", kwargs={"uuid": user.uuid})


user_redirect_view = UserRedirectView.as_view()


class ProfileSetupView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """First-login profile completion (username, gamertag, avatar, bio, LFT)."""

    model = UserProfile
    form_class = ProfileSetupForm
    template_name = "users/profile_setup.html"
    success_message = _("Your profile has been set up.")

    def get_object(self, queryset: QuerySet | None = None) -> UserProfile:
        assert self.request.user.is_authenticated  # type guard
        profile, _created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get_success_url(self) -> str:
        return reverse("users:detail", kwargs={"uuid": self.request.user.uuid})


profile_setup_view = ProfileSetupView.as_view()


class UserSettingsView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """Settings hub: edit profile, with links to account/security/notifications."""

    model = UserProfile
    form_class = ProfileSetupForm
    template_name = "users/user_settings.html"
    success_message = _("Your settings have been updated.")

    def get_object(self, queryset: QuerySet | None = None) -> UserProfile:
        assert self.request.user.is_authenticated  # type guard
        profile, _created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get_success_url(self) -> str:
        return reverse("users:settings")


user_settings_view = UserSettingsView.as_view()
