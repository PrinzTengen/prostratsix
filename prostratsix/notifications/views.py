from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django.views.generic import View

from .forms import NotificationPreferenceForm
from .models import Notification
from .models import NotificationPreference

BELL_TEMPLATE = "notifications/partials/bell.html"
BELL_ITEM_LIMIT = 5


def bell_context(user) -> dict:
    """Badge count and the newest notifications of ``user`` for the navbar bell.

    Deliberately computed here instead of read from the
    ``unread_notifications`` context processor, so the partial renders the same
    way whether it is polled on its own or embedded in a full page.
    """
    qs = Notification.objects.for_user(user)
    return {
        "unread_notifications_count": qs.unread().count(),
        "recent_notifications": list(qs[:BELL_ITEM_LIMIT]),
    }


class NotificationListView(LoginRequiredMixin, ListView):
    template_name = "notifications/list.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        return Notification.objects.for_user(self.request.user)


class NotificationBellView(LoginRequiredMixin, TemplateView):
    """Render just the navbar bell (badge + latest notifications).

    htmx polls this every 30 seconds and swaps the response over the current
    bell, so the badge stays current without a page reload.
    """

    template_name = BELL_TEMPLATE

    def get_context_data(self, **kwargs):
        return super().get_context_data(**bell_context(self.request.user), **kwargs)


class BellResponseMixin:
    """Answer htmx requests with the refreshed bell instead of a redirect.

    A redirect would make htmx follow it and swap a whole page into the navbar;
    the bell partial is what the caller actually asked to update.
    """

    def render_bell(self, request):
        return render(request, BELL_TEMPLATE, bell_context(request.user))

    def is_htmx(self, request) -> bool:
        return request.headers.get("HX-Request") == "true"


class MarkReadView(BellResponseMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(
            Notification,
            pk=pk,
            recipient=request.user,
        )
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        if self.is_htmx(request):
            return self.render_bell(request)
        if notification.url:
            return redirect(notification.url)
        return redirect("notifications:list")


class MarkAllReadView(BellResponseMixin, LoginRequiredMixin, View):
    def post(self, request):
        Notification.objects.for_user(request.user).unread().update(is_read=True)
        if self.is_htmx(request):
            return self.render_bell(request)
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
