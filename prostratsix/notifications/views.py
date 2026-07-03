from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView
from django.views.generic import UpdateView
from django.views.generic import View

from .forms import NotificationPreferenceForm
from .models import Notification
from .models import NotificationPreference


class NotificationListView(LoginRequiredMixin, ListView):
    template_name = "notifications/list.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        return Notification.objects.for_user(self.request.user)


class MarkReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(
            Notification,
            pk=pk,
            recipient=request.user,
        )
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        if notification.url:
            return redirect(notification.url)
        return redirect("notifications:list")


class MarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        Notification.objects.for_user(request.user).unread().update(is_read=True)
        return redirect("notifications:list")


class NotificationPreferenceView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    form_class = NotificationPreferenceForm
    template_name = "notifications/preferences.html"
    success_message = _("Notification preferences updated.")
    success_url = reverse_lazy("notifications:preferences")

    def get_object(self, queryset=None):
        preference, _created = NotificationPreference.objects.get_or_create(
            user=self.request.user,
        )
        return preference
