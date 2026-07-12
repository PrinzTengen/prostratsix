from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView
from django.views.generic import UpdateView
from django.views.generic import View

from .forms import NotificationPreferenceForm
from .models import Notification
from .models import NotificationPreference

BELL_TEMPLATE = "notifications/partials/bell.html"


def render_bell(request) -> HttpResponse:
    """Render the navbar bell partial.

    The markup is stripped of surrounding whitespace on purpose: htmx swaps
    this response with ``outerHTML`` and inserts every node of it, text nodes
    included, while removing only the old element. A leading and a trailing
    newline would therefore be left behind in the navbar on every swap — about
    a thousand stray whitespace nodes over an eight-hour session.
    """
    return HttpResponse(render_to_string(BELL_TEMPLATE, request=request).strip())


class NotificationListView(LoginRequiredMixin, ListView):
    template_name = "notifications/list.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        return Notification.objects.for_user(self.request.user)


class NotificationBellView(LoginRequiredMixin, View):
    """Render just the navbar bell (badge + latest notifications).

    htmx polls this every 30 seconds and swaps the response over the current
    bell, so the badge stays current without a page reload. The badge count and
    the items come from the ``unread_notifications`` context processor, which
    runs on this render like on any other.
    """

    def get(self, request):
        return render_bell(request)


class BellResponseMixin:
    """Tell an htmx request apart from a plain one.

    The mark-read views answer htmx with the refreshed bell instead of a
    redirect — htmx would follow the redirect and swap a whole page into the
    navbar, while the bell partial is what the caller asked to update.
    """

    def is_htmx(self, request) -> bool:
        return request.headers.get("HX-Request") == "true"


class MarkReadView(BellResponseMixin, LoginRequiredMixin, View):
    """Mark a single notification of the current user as read.

    Answers an htmx request with the refreshed bell partial, so the badge drops
    without a page load. A plain request keeps redirecting to the notification's
    target, or to the list if it has none — that is the path the buttons on the
    notification list page take.
    """

    def post(self, request, pk):
        notification = get_object_or_404(
            Notification,
            pk=pk,
            recipient=request.user,
        )
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        if self.is_htmx(request):
            return render_bell(request)
        if notification.url:
            return redirect(notification.url)
        return redirect("notifications:list")


class MarkAllReadView(BellResponseMixin, LoginRequiredMixin, View):
    """Mark every unread notification of the current user as read.

    Answers an htmx request with the refreshed bell partial, a plain request
    with a redirect to the notification list.
    """

    def post(self, request):
        Notification.objects.for_user(request.user).unread().update(is_read=True)
        if self.is_htmx(request):
            return render_bell(request)
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
