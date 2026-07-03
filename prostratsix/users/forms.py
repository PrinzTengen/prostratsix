from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.utils.translation import gettext_lazy as _

from .helpers.age import is_over_18
from .models import User
from .models import UserProfile


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):
        model = User


class UserAdminCreationForm(admin_forms.AdminUserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):
        model = User
        error_messages = {
            "username": {"unique": _("This username has already been taken.")},
        }


class RegistrationFieldsMixin(forms.Form):
    """
    Shared registration fields (name, birthday, over-18 and privacy consent)
    used by both the classic and the social signup forms.
    """

    first_name = forms.CharField(label=_("First Name"), max_length=255)
    last_name = forms.CharField(label=_("Last Name"), max_length=255)
    date_of_birth = forms.DateField(
        label=_("Date of Birth"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    check_over_18 = forms.BooleanField(
        label=_("I confirm that I am over 18 years old"),
    )
    privacy_policy_accepted = forms.BooleanField(
        label=_("I have read and accept the privacy policy"),
    )

    # Render fields in the order requested by the user flow. The social form
    # overrides this to drop the password fields it does not have.
    field_order = [
        "email",
        "first_name",
        "last_name",
        "password1",
        "password2",
        "date_of_birth",
        "check_over_18",
        "privacy_policy_accepted",
    ]

    def clean(self):
        cleaned_data = super().clean()
        date_of_birth = cleaned_data.get("date_of_birth")
        if date_of_birth and not is_over_18(date_of_birth):
            self.add_error(
                "check_over_18",
                _("You must be at least 18 years old to register."),
            )
        return cleaned_data

    def apply_registration_fields(self, user: User) -> None:
        """Persist the shared registration fields onto the user instance."""
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.name = f"{user.first_name} {user.last_name}".strip()
        user.date_of_birth = self.cleaned_data["date_of_birth"]
        user.check_over_18 = self.cleaned_data["check_over_18"]
        user.privacy_policy_accepted = self.cleaned_data["privacy_policy_accepted"]
        user.save()


class UserSignupForm(RegistrationFieldsMixin, SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields (email, password) are added automatically by allauth.
    Check UserSocialSignupForm for accounts created from social.
    """

    def save(self, request):
        user = super().save(request)
        self.apply_registration_fields(user)
        return user


class UserSocialSignupForm(RegistrationFieldsMixin, SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts (Discord).
    Email/name may be pre-filled from the provider; the user still confirms
    their birthday, age and the privacy policy. There are no password fields.
    """

    field_order = [
        "email",
        "first_name",
        "last_name",
        "date_of_birth",
        "check_over_18",
        "privacy_policy_accepted",
    ]

    def save(self, request):
        user = super().save(request)
        self.apply_registration_fields(user)
        return user


class ProfileSetupForm(forms.ModelForm):
    """
    Shown after the first login to complete the profile: site username,
    gamertag, avatar/thumbnail, biography and the LFT (looking for team) flag.
    """

    username = forms.CharField(
        label=_("Username"),
        max_length=150,
        help_text=_("Your public handle on ProStratSix."),
    )

    class Meta:
        model = UserProfile
        fields = ["avatar", "thumbnail", "bio", "r6_gamertag", "is_lft"]
        labels = {
            "r6_gamertag": _("Gamertag"),
            "bio": _("Biography"),
            "is_lft": _("Looking for a team (LFT)"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fill with the user's current (possibly auto-generated) username.
        if self.instance and self.instance.pk:
            self.fields["username"].initial = self.instance.user.username

    def clean_username(self):
        username = self.cleaned_data["username"]
        qs = User.objects.filter(username__iexact=username)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.user.pk)
        if qs.exists():
            raise forms.ValidationError(_("This username has already been taken."))
        return username

    def save(self, commit=True):  # noqa: FBT002
        profile = super().save(commit=False)
        user = profile.user
        user.username = self.cleaned_data["username"]
        user.profile_completed = True
        if commit:
            user.save()
            profile.save()
        return profile
