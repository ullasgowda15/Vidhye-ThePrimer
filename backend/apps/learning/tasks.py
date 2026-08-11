"""The mastery update, off the child's lifecycle (PRODUCT.md §10.5, §16.3).

One task, enqueued once per sitting by `story.orchestrator.end_adventure`,
alongside memory consolidation. The two are siblings by design: both read a
finished sitting, both write something the companion will use next time, and
neither may happen while a four-year-old is looking at the screen. `POST
/adventures/{id}/end` is the last request the tablet makes before the story
closes, and §10.5's update is a handful of queries plus a lock — small, but not
zero, and not on that request.

**Post-session, not per-beat**, which is §10.5 read literally and is also the only
honest place for it. A running estimate updated mid-adventure would change the
difficulty of a beat from underneath the beat that was generated against it, and
would give the last answer of a sitting a different weight from the first purely
because of when the transaction landed.

No automatic retry, for the reason every other task in this repo has none: the
work is idempotent by gate (`learning_updated_at`), so a retry either does nothing
or re-does something that already succeeded. What is *not* safe is a redelivery
that slips past the gate, which is why the stamp is written in the same
transaction as the update.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.learning import engine
from apps.story.models import PlaySession

logger = logging.getLogger(__name__)


@shared_task
def update_from_session(session_id: str) -> str:
    """Fold one sitting's in-story evidence into the child's skill model.

    Returns a short outcome for the Celery result row: `updated`, `empty` (the
    child played, but nothing they did was evidence about an objective), `already`
    (a redelivery) or `missing` (the sitting or the family was deleted while this
    was queued).
    """
    session = (
        PlaySession.objects.select_related("child")
        .prefetch_related("adventures")
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        # Same posture as `memory.tasks.consolidate`: a family deleted their
        # account between the sitting ending and this task being picked up. The
        # deletion is the newer instruction (§13.8).
        logger.info("learning.update_skipped session=%s reason=deleted", session_id)
        return "missing"

    if session.learning_updated_at is not None:
        logger.info("learning.update_skipped session=%s reason=already", session_id)
        return "already"

    with transaction.atomic():
        # Locked and re-checked inside the transaction: two workers handed the same
        # redelivered task would otherwise both pass the check above and both fold
        # the same evidence in. The stamp is written in here with the update, so
        # there is no window in which the estimate has moved and the gate says it
        # has not.
        locked = PlaySession.objects.select_for_update().get(pk=session.pk)
        if locked.learning_updated_at is not None:
            logger.info("learning.update_skipped session=%s reason=raced", session_id)
            return "already"

        moved = engine.update_from_adventures(session.child, list(session.adventures.all()))

        locked.learning_updated_at = timezone.now()
        locked.save(update_fields=["learning_updated_at", "updated_at"])

    if not moved:
        logger.info("learning.update_empty session=%s child=%s", session_id, session.child_id)
        return "empty"

    logger.info(
        "learning.update session=%s child=%s objectives=%s",
        session_id,
        session.child_id,
        ",".join(sorted(moved)),
    )
    return "updated"
