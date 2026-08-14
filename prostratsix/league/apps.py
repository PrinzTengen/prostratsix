from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LeagueConfig(AppConfig):
    name = "prostratsix.league"
    verbose_name = _("League")
