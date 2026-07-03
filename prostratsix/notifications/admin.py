from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Notification
from .models import NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "recipient", "category", "level", "is_read", "created_at"]
    list_filter = ["category", "level", "is_read"]
    search_fields = ["title", "message", "recipient__username", "recipient__email"]
    actions = ["mark_as_read"]

    @admin.action(description=_("Mark selected notifications as read"))
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "email_security", "email_general"]
    search_fields = ["user__username", "user__email"]
