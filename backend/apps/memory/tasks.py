"""Consolidation and the interest graph, off the child's lifecycle (§5.4, §16.3).

Two tasks, each enqueued once per sitting by `orchestrator.end_adventure`.

**It must never delay the child, and "never" is architectural rather than
aspirational.** `POST /adventures/{id}/end` is the last thing a four-year-old's
device does before the screen says goodnight, and consolidation is a model call
plus a handful of embedding calls — seconds, sometimes many. Doing it inline would
put that wait between "all done" and the story closing, which is the one place in
the product where the child has already stopped playing and would be watching a
spinner for nothing. So the endpoint marks the adventure finished, answers, and
this runs afterwards on a worker. `update_interests` is far cheaper — it calls no
provider at all — and it runs here for a different reason: it reads a *finished*
sitting, and a graph updated mid-story would change the world the child is standing
in while they are standing in it.

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
from django.db import transaction

from apps.analytics import cost
from apps.memory import consolidation, interest
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

    # Consolidation is a model call plus a handful of embedding calls, and it is
    # spent *on this sitting* — so it belongs in this sitting's cost/session
    # (§22.4), not in the unattributed pile. Re-entered here for the same reason
    # `story.tasks.generate_beat_media` does: a `ContextVar` does not cross a
    # Celery boundary.
    with cost.trace_ids(child_id=session.child_id, session_id=session.pk):
        outcome = consolidation.consolidate_session(session)
    return outcome.status


@shared_task
def update_interests(session_id: str) -> str:
    """Fold one sitting into the child's interest graph (§11.7).

    The third of the post-session jobs, alongside `consolidate` above and
    `learning.tasks.update_from_session`. Sibling rather than a step inside
    consolidation, and the reason is failure isolation: consolidation calls a model
    and an embedding provider, this calls neither, and a graph that stopped updating
    because a vendor was down would take a fortnight to notice and could not be
    rebuilt afterwards — the raw beats it reads are the ones §17.5 prunes.

    It is also the cheapest of the three, which is why it is last: a broker that
    rejects the enqueue costs the least here.

    Returns a short outcome for the Celery result row: `updated`, `empty` (the child
    played, but nothing in it said what they love), `already` (a redelivery) or
    `missing` (the sitting or the family was deleted while this was queued).
    """
    session = (
        PlaySession.objects.select_related("child")
        .prefetch_related("adventures")
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        # The deletion is the newer instruction, exactly as in `consolidate` (§13.8).
        logger.info("memory.interests_skipped session=%s reason=deleted", session_id)
        return "missing"

    with transaction.atomic():
        # Locked and re-checked inside the transaction, like the mastery update:
        # two workers handed the same redelivered task would otherwise both pass
        # the gate and both raise the same weights, and a topic reinforced twice
        # for one afternoon is a favourite the graph invented.
        locked = PlaySession.objects.select_for_update().select_related("child").get(pk=session.pk)
        if locked.interests_updated_at is not None:
            logger.info("memory.interests_skipped session=%s reason=raced", session_id)
            return "already"
        outcome = interest.update_from_session(locked)

    return outcome.status
