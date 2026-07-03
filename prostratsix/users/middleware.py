from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ProfileCompletionMiddleware:
    """
    Force authenticated users whose profile is not yet completed into the
    profile setup page until they fill in the required fields.

    The allauth account/MFA/social pages, the admin, static and media files
    stay reachable so users can still verify email, log out or set up 2FA.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not user.profile_completed
            and not user.is_staff
        ):
            setup_url = reverse("users:profile_setup")
            allowed_prefixes = (
                setup_url,
                "/accounts/",
                "/api/",
                settings.STATIC_URL,
                settings.MEDIA_URL,
                f"/{settings.ADMIN_URL}",
            )
            if not request.path.startswith(allowed_prefixes):
                return redirect(setup_url)
        return self.get_response(request)
