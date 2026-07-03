from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from datetime import date

ADULT_AGE = 18


def calculate_age(born: date, today: date | None = None) -> int:
    """Return the age in completed years for the given birth date."""
    today = today or timezone.localdate()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def is_over_18(born: date, today: date | None = None) -> bool:
    """Return True if the given birth date corresponds to an age of 18 or more."""
    return calculate_age(born, today) >= ADULT_AGE
