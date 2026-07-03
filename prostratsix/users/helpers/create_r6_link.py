from http import HTTPStatus

import requests
from django.core.exceptions import ValidationError
from django.core.mail import mail_admins


def create_tracker_link(self):
    if not self.r6_gamertag:
        return

    url = f"https://r6.tracker.network/r6siege/profile/ubi/{self.r6_gamertag}/overview"

    try:
        response = requests.head(
            url,
            timeout=8,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )

        # Manche Seiten blocken HEAD, dann GET testen
        if response.status_code in [403, 405]:
            response = requests.get(
                url,
                timeout=8,
                allow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0",
                },
            )

    except requests.RequestException as error:
        mail_admins(
            subject="Tracker-Link konnte nicht geprüft werden",
            message=f"Gamertag: {self.r6_gamertag}\nURL: {url}\nFehler: {error}",
        )

        raise ValidationError(
            {
                "r6_gamertag": "Der Tracker-Link konnte gerade nicht geprüft werden.",
            },
        ) from error

    if response.status_code == HTTPStatus.NOT_FOUND:
        mail_admins(
            subject="Ungültiger R6 Tracker-Link",
            message=f"Für diesen Gamertag wurde keine Tracker-Seite gefunden:\n\n{url}",
        )

        raise ValidationError(
            {
                "r6_gamertag": (
                    "Für diesen Gamertag wurde keine Tracker-Seite gefunden."
                ),
            },
        )

    self.tracker_link = url
