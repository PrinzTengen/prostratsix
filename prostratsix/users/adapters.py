from __future__ import annotations

import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

    from prostratsix.users.models import User


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def populate_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        data: dict[str, typing.Any],
    ) -> User:
        """
        Populates user information from social provider info.

        See: https://docs.allauth.org/en/latest/socialaccount/advanced.html#creating-and-populating-user-instances
        """
        user = super().populate_user(request, sociallogin, data)
        if not user.name:
            if name := data.get("name"):
                user.name = name
            elif first_name := data.get("first_name"):
                user.name = first_name
                if last_name := data.get("last_name"):
                    user.name += f" {last_name}"
        return user

    def save_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        form: typing.Any = None,
    ) -> User:
        """Persist the user and copy the Discord handle into the profile."""
        user = super().save_user(request, sociallogin, form)
        if sociallogin.account.provider == "discord":
            extra = sociallogin.account.extra_data or {}
            discord_name = extra.get("username") or extra.get("global_name")
            if discord_name:
                from prostratsix.users.models import UserProfile  # noqa: PLC0415

                profile, _created = UserProfile.objects.get_or_create(user=user)
                profile.discord = discord_name
                profile.save()
        return user
