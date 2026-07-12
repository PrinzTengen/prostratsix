from django.urls import path

from .views import MarkAllReadView
from .views import MarkReadView
from .views import NotificationBellView
from .views import NotificationListView
from .views import NotificationPreferenceView

app_name = "notifications"
urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path("bell/", NotificationBellView.as_view(), name="bell"),
    path("<int:pk>/read/", MarkReadView.as_view(), name="mark_read"),
    path("read-all/", MarkAllReadView.as_view(), name="mark_all_read"),
    path("preferences/", NotificationPreferenceView.as_view(), name="preferences"),
]
