from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class MatchResultForm(forms.Form):
    score_home = forms.IntegerField(label=_("Score home"), min_value=0)
    score_away = forms.IntegerField(label=_("Score away"), min_value=0)


class MatchDisputeForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class RegistrationRejectForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
