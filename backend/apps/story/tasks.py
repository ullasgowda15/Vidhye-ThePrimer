"""Beat media, produced off the response lifecycle (PRODUCT.md §16.3, §18.5).

The picture and the voice used to be generated *inside* the streaming response.
That made them hostage to the client: a tablet that slept, a tab that closed, a
`curl` killed after two seconds — the beat itself still finished and was persisted
by its own offloaded thread, but the coroutine that would have started the media
never got there, and what was left was a paid-for beat with no picture and no
voice. The founder's dev database held three of them.

So the media belongs to a task keyed on the beat. It is enqueued the moment the
beat is committed (`orchestrator.schedule_beat_media`) and it finishes whether or
not anyone is still listening; the stream, meanwhile, only *watches* for the
result (`orchestrator.follow_media`). The child's experience is unchanged — the
narration still arrives first and the picture still lands separately — but a
disconnect now costs the connection rather than the beat.

Idempotent by construction, like the companion task: it does only the stages that
are still missing, and every step underneath is cache-first (§16.6), so a re-run
finds the finished asset rather than paying for it again.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from celery import shared_task

from apps.story import orchestrator
from apps.story.models import Beat

logger = logging.getLogger(__name__)


@shared_task
def generate_beat_media(beat_id: str) -> str:
    """Finish this beat's illustration and voice line.

    Returns a one-word outcome for the Celery result row: `complete`, `partial`
    (a provider failed — the beat is still tellable), `skipped`, or `missing`.

    Deliberately no automatic retry, for the same reason as the companion task:
    `attach_media` already swallows per-asset failures, so a retry loop here would
    re-run a task that has already decided what it could do. The re-run that
    matters is the deliberate one — the next stream open, via
    `orchestrator.ensure_media_scheduled`.
    """
    beat = (
        Beat.objects.select_related("adventure__child__companion", "media", "audio")
        .filter(pk=beat_id)
        .first()
    )
    if beat is None:
        # The child, the adventure or the beat was deleted while this was queued.
        # The deletion is the newer instruction; nothing to repair.
        logger.info("story.media_task_skipped beat=%s reason=deleted", beat_id)
        return "missing"

    pending = orchestrator.pending_media_stages(beat)
    if not pending:
        # A second open queued a repair for a beat another worker had just
        # finished. Doing nothing here is the whole reason the check exists.
        logger.info("story.media_task_skipped beat=%s reason=complete", beat_id)
        return "skipped"

    logger.info("story.media_task beat=%s stages=%s", beat_id, ",".join(pending))
    async_to_sync(orchestrator.attach_media)(beat, stages=pending)

    beat.refresh_from_db()
    remaining = orchestrator.pending_media_stages(beat)
    if remaining:
        logger.warning("story.media_task_partial beat=%s missing=%s", beat_id, ",".join(remaining))
        return "partial"
    return "complete"
