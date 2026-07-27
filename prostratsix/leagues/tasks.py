from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from . import services
from .models import League
from .models import LeagueStatus

logger = logging.getLogger(__name__)


@shared_task()
def sync_league_statuses() -> dict[str, int]:
    """Walk every league that is due through its next scheduled status change.

    Runs every five minutes (the ``PeriodicTask`` row is created by
    ``0002_periodic_tasks``, not by CELERY_BEAT_SCHEDULE — the DatabaseScheduler
    would reset that one on every beat restart).

    The filters here only pre-select roughly; the guarantees live in the services.
    ``start_league()`` takes the row lock and re-checks the status *after* it, so
    two overlapping runs of this task cannot start the same league twice and send
    every player a second round of notifications. Re-implementing that check here
    would be duplicating the wrong half of it: a check outside the lock is exactly
    the race it exists to prevent.

    The three steps run in order and each queryset is evaluated when its step
    starts, so a league that was left behind (say the worker was down for a day)
    catches up in one run: draft -> registration open -> registration closed ->
    running.

    Returns a count per transition, which is what ends up in the Celery result and
    is the only thing to look at when asking "did the sweep do anything at all?".
    """
    now = timezone.now()

    return {
        "opened": _apply(
            services.open_registration,
            League.objects.filter(
                status=LeagueStatus.DRAFT,
                registration_opens_at__lte=now,
            ),
        ),
        "closed": _apply(
            services.close_registration,
            League.objects.filter(
                status=LeagueStatus.REGISTRATION_OPEN,
                registration_closes_at__lte=now,
            ),
        ),
        # starts_at is nullable; __lte drops the NULL rows on its own — a league
        # without a start date is one nobody has scheduled yet.
        "started": _apply(
            services.start_league,
            League.objects.filter(
                status=LeagueStatus.REGISTRATION_CLOSED,
                starts_at__lte=now,
            ),
        ),
    }


def _apply(service, leagues) -> int:
    """Run ``service`` on every league, counting the ones it changed.

    One league that cannot make the transition — because an admin cancelled it a
    second ago, or because its row is wedged — must not take the rest of the sweep
    down with it. Each service call is its own transaction (this runs in a worker,
    not in a request, so ATOMIC_REQUESTS does not apply), so the loop can carry on
    after a failure with a clean connection.
    """
    changed = 0
    for league in leagues:
        try:
            service(league)
        except services.LeagueError:
            # Expected: the league is in a status this transition does not allow.
            logger.info(
                "Skipped %s in status %s",
                league,
                league.status,
                exc_info=True,
            )
        except Exception:
            # Blind on purpose: whatever one row throws, the sweep has to reach the
            # leagues behind it. It is logged with its traceback, never swallowed.
            logger.exception("Failed to update the status of %s", league)
        else:
            changed += 1
    return changed
