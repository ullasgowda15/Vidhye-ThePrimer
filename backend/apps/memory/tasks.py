"""Consolidation, off the child's lifecycle (PRODUCT.md §5.4, §16.3).

One task, enqueued once per sitting by `orchestrator.end_adventure`.

**It must never delay the child, and "never" is architectural rather than
aspirational.** `POST /adventures/{id}/end` is the last thing a four-year-old's
device does before the screen says goodnight, and consolidation is a model call
plus a handful of embedding calls — seconds, sometimes many. Doing it inline would
put that wait between "all done" and the story closing, which is the one place in
the product where the child has already stopped playing and would be watching a
spinner for nothing. So the endpoint marks the adventure finished, answers, and
this runs afterwards on a worker.

**No automatic retry, for the same reason the media task has none.**
`consolidation.consolidate_session` degrades internally: an unreachable model
costs the subtle memories, an unreachable embedding provider costs the vectors,
and both still write rows. A retry would therefore re-run something that has
already decided what it could do — and `consolidated_at` would make the second
attempt a no-op anyway. The re-run that matters is the deliberate one
(`manage.py consolidate_session`), for a sitting that failed in a way worth
looking at.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.memory import consolidation
from apps.story.models import PlaySession

logger = logging.getLogger(__name__)


@shared_task
def consolidate(session_id: str) -> str:
    """Consolidate one sitting into long-term memory.

    Returns a short outcome string for the Celery result row: `consolidated`,
    `already` (a redelivery), `empty` (nothing scored above the floor) or
    `missing` (the session, child or family was deleted while this was queued).
    """
    session = (
        PlaySession.objects.select_related("child")
        .prefetch_related("adventures")
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        # A family deleted their account between the sitting ending and this task
        # being picked up. The deletion is the newer instruction; there is nothing
        # here to remember and nothing to repair (§13.8).
        logger.info("memory.consolidate_skipped session=%s reason=deleted", session_id)
        return "missing"

    outcome = consolidation.consolidate_session(session)
    return outcome.status
