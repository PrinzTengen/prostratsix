from __future__ import annotations

import itertools
from datetime import timedelta

from django.utils import timezone

from prostratsix.league.models import Season
from prostratsix.league.models import SeasonStatus

_season_counter = itertools.count(1)


def create_season(**overrides) -> Season:
    now = timezone.now()
    defaults = {
        "name": f"Season {next(_season_counter)}",
        "registration_opens_at": now - timedelta(days=1),
        "registration_closes_at": now + timedelta(days=7),
        "starts_at": now + timedelta(days=14),
        "status": SeasonStatus.REGISTRATION_OPEN,
    }
    defaults.update(overrides)
    return Season.objects.create(**defaults)
