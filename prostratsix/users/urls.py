from django.urls import path

from .views import profile_setup_view
from .views import user_detail_view
from .views import user_list_view
from .views import user_redirect_view
from .views import user_settings_view
from .views import user_update_view

app_name = "users"
urlpatterns = [
    path("", view=user_list_view, name="list"),
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("~update/", view=user_update_view, name="update"),
    path("~setup/", view=profile_setup_view, name="profile_setup"),
    path("~settings/", view=user_settings_view, name="settings"),
    path("<uuid:uuid>/", view=user_detail_view, name="detail"),
]
