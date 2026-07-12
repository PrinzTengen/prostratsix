from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LeaguesConfig(AppConfig):
    name = "prostratsix.leagues"
    verbose_name = _("Leagues")
