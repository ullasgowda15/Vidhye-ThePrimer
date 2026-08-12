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

from apps.analytics import cost
from apps.story import orchestrator
from apps.story.ai.router import AIError
from apps.story.models import Adventure, Beat

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
    # **This is where the cost context has to be re-entered** (§19.9). A
    # `ContextVar` is per-task, so the trace `create_beat` opened does not follow a
    # picture into the worker that renders it — and illustration plus voice is most
    # of what a beat costs. Without this line every media span lands unattributed
    # and `cost_per_session` reports roughly the LLM half of the true number.
    # `trace_ids` rather than `trace` because the ids are already on the row.
    with cost.trace_ids(child_id=beat.adventure.child_id, session_id=beat.adventure.session_id):
        async_to_sync(orchestrator.attach_media)(beat, stages=pending)

    beat.refresh_from_db()
    remaining = orchestrator.pending_media_stages(beat)
    if remaining:
        logger.warning("story.media_task_partial beat=%s missing=%s", beat_id, ",".join(remaining))
        return "partial"
    return "complete"


@shared_task
def advance_adventure(adventure_id: str, after_beat_id: str) -> str:
    """Generate the beat that follows `after_beat_id`, ahead of the client (§4.9).

    Returns a one-word outcome for the Celery result row: `generated`, `ready` (the
    beat already existed), `skipped` (the position is not ours to fill) or `missing`.

    This is the prefetch, and it is the reason a child who taps a choice card is
    told what happened instead of watching their friend think about it. The stream
    that arrives a moment later finds the beat already written and replays it — and
    if it arrives *first*, the lock in `_beat_or_generate` means one of the two
    waits for the other rather than both paying a provider.

    `after_beat_id` rather than a bare sequence number, because "the beat after the
    one the child just finished" is a fact about the story and "beat four" is a
    guess about a race. Re-checked here rather than trusted from the caller: the
    child may have raced ahead, or the adventure may have ended, between the enqueue
    and the worker picking it up.

    No retry, deliberately. A failed prefetch is not a failed beat — the stream
    generates it on demand and the child never learns the difference. Retrying would
    risk paying twice for a beat nobody is waiting for.

    **And no safe fallback, for the same reason** (`allow_fallback=False`). A beat
    the guard could not obtain is a *persisted* fallback if we write one here, and
    this task runs with nobody waiting: the stream that arrives a second later
    would have generated the real beat. `create_beat`'s default is the opposite
    because on that path a child is looking at the screen.
    """
    after = (
        Beat.objects.select_related("adventure__child__companion")
        .filter(pk=after_beat_id, adventure_id=adventure_id)
        .first()
    )
    if after is None:
        logger.info("story.advance_skipped beat=%s reason=deleted", after_beat_id)
        return "missing"

    adventure = after.adventure
    if adventure.status != Adventure.Status.IN_PROGRESS:
        logger.info("story.advance_skipped adventure=%s reason=%s", adventure_id, adventure.status)
        return "skipped"

    seq, prev = orchestrator.next_position(adventure)
    if seq != after.seq + 1 or (prev is not None and prev.pk != after.pk):
        # The child is not where this task assumed: they were still on an earlier
        # beat, or a stream got here first. Generating anyway would write a beat
        # into a position that is not next, which `next_position` would then have to
        # skip past — a beat paid for and never told.
        logger.info(
            "story.advance_skipped adventure=%s reason=moved want=%d found=%d",
            adventure_id,
            after.seq + 1,
            seq,
        )
        return "skipped"

    if adventure.beats.filter(seq=seq).exists():
        logger.info("story.advance_skipped adventure=%s reason=exists seq=%d", adventure_id, seq)
        return "ready"

    try:
        beat, _ = orchestrator.resolve_beat(adventure, seq=seq, prev=prev, allow_fallback=False)
    except AIError:
        # The stream will try again when the child asks. Logged, never raised: a
        # failed prefetch must not become a retry storm against a provider that is
        # already having a bad time.
        logger.exception("story.advance_failed adventure=%s seq=%d", adventure_id, seq)
        return "skipped"

    # The picture, the voice and the icons are not this task's to run: `create_beat`
    # handed them to `generate_beat_media` on commit, and doing them here as well
    # would be two workers missing the same cache and paying twice for one beat.
    logger.info("story.advance adventure=%s beat=%s seq=%d", adventure_id, beat.id, beat.seq)
    return "generated"
