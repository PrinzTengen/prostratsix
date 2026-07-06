from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Team


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = [
            "name",
            "tag",
            "bio",
            "region",
            "discord",
            "twitter",
            "thumbnail",
            "banner",
        ]


class TeamInviteForm(forms.Form):
    username_or_email = forms.CharField(
        label=_("Username or e-mail"),
        max_length=255,
    )
