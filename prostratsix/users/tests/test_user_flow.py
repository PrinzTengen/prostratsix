from __future__ import annotations

from datetime import date
from datetime import timedelta

import pytest
from django.http import HttpResponse
from django.urls import reverse

from prostratsix.users.adapters import SocialAccountAdapter
from prostratsix.users.forms import ProfileSetupForm
from prostratsix.users.forms import UserSignupForm
from prostratsix.users.helpers.age import calculate_age
from prostratsix.users.helpers.age import is_over_18
from prostratsix.users.middleware import ProfileCompletionMiddleware
from prostratsix.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _signup_data(**overrides):
    data = {
        "email": "newcomer@example.com",
        "password1": "Str0ng-P4ssw0rd!xyz",
        "password2": "Str0ng-P4ssw0rd!xyz",
        "first_name": "Jane",
        "last_name": "Doe",
        "check_over_18": True,
        "privacy_policy_accepted": True,
    }
    data.update(overrides)
    return data


class TestAgeHelpers:
    def test_calculate_age(self):
        born = date.today() - timedelta(days=365 * 20 + 5)
        assert calculate_age(born) >= 19

    def test_is_over_18_true(self):
        assert is_over_18(date.today().replace(year=date.today().year - 25))

    def test_is_over_18_false(self):
        assert not is_over_18(date.today().replace(year=date.today().year - 10))


class TestUserSignupForm:
    def test_under_18_is_rejected(self):
        ten_years_ago = date.today().replace(year=date.today().year - 10)
        form = UserSignupForm(
            data=_signup_data(date_of_birth=ten_years_ago.isoformat()),
        )
        assert not form.is_valid()
        assert "check_over_18" in form.errors

    def test_missing_privacy_is_rejected(self):
        adult = date.today().replace(year=date.today().year - 25)
        form = UserSignupForm(
            data=_signup_data(
                date_of_birth=adult.isoformat(),
                privacy_policy_accepted=False,
            ),
        )
        assert not form.is_valid()
        assert "privacy_policy_accepted" in form.errors

    def test_valid_adult_signup(self):
        adult = date.today().replace(year=date.today().year - 25)
        form = UserSignupForm(data=_signup_data(date_of_birth=adult.isoformat()))
        assert form.is_valid(), form.errors


class TestProfileCompletionMiddleware:
    def _mw(self):
        return ProfileCompletionMiddleware(lambda request: HttpResponse("ok"))

    def test_incomplete_profile_is_redirected(self, user, rf):
        user.profile_completed = False
        user.save()
        request = rf.get("/some-protected-page/")
        request.user = user
        response = self._mw()(request)
        assert response.status_code == 302
        assert response.url == reverse("users:profile_setup")

    def test_setup_page_is_allowed(self, user, rf):
        user.profile_completed = False
        user.save()
        request = rf.get(reverse("users:profile_setup"))
        request.user = user
        response = self._mw()(request)
        assert response.status_code == 200

    def test_completed_profile_passes_through(self, user, rf):
        user.profile_completed = True
        user.save()
        request = rf.get("/some-protected-page/")
        request.user = user
        response = self._mw()(request)
        assert response.status_code == 200


class TestProfileSetupForm:
    def test_sets_username_and_completed(self, user):
        profile = user.profile
        form = ProfileSetupForm(
            data={
                "username": "fragqueen",
                "bio": "gg",
                "is_lft": True,
                "r6_gamertag": "",
            },
            instance=profile,
        )
        assert form.is_valid(), form.errors
        form.save()
        user.refresh_from_db()
        assert user.username == "fragqueen"
        assert user.profile_completed is True

    def test_username_uniqueness(self, user):
        UserFactory.create(username="taken")
        form = ProfileSetupForm(
            data={"username": "taken", "bio": "", "is_lft": False, "r6_gamertag": ""},
            instance=user.profile,
        )
        assert not form.is_valid()
        assert "username" in form.errors


class TestSocialAccountAdapter:
    def test_save_user_maps_discord_handle(self, user, monkeypatch):
        from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

        monkeypatch.setattr(
            DefaultSocialAccountAdapter,
            "save_user",
            lambda self, request, sociallogin, form=None: user,
        )

        class _Account:
            provider = "discord"
            extra_data = {"username": "cooldude"}

        class _SocialLogin:
            account = _Account()

        adapter = SocialAccountAdapter()
        adapter.save_user(None, _SocialLogin(), None)

        user.profile.refresh_from_db()
        assert user.profile.discord == "cooldude"
