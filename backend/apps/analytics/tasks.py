"""The session report, written after the child has gone (§12.1, §5.4, §16.3).

The fourth post-session sibling, alongside memory consolidation, the mastery
update and the interest update. Same posture as all three: enqueued on commit by
`story.orchestrator.end_adventure`, nobody waiting, its own `try` so a broker that
rejects one enqueue does not silently drop the rest.

**It is the one that has to go last, and it is the only one that has an opinion
about ordering.** The other three read a finished sitting and write to three
different places; this one reads *their output* — the report snapshots what the
skill model believes after this sitting's evidence has been folded in, and which
topics the interest graph ranks top after this sitting reinforced them. Generated
before those two land, it would record last week's numbers under today's date, and
the weekly report's "moved on this week" is measured from exactly those numbers.

Celery gives no ordering between siblings, so the ordering is a **gate rather than
a promise**: the task looks at `PlaySession.learning_updated_at` and
`interests_updated_at`, and retries if either is still null. Both are cheap,
provider-free jobs that finish in milliseconds, so in practice this waits once or
not at all. After `GATE_RETRIES` it gives up waiting and writes the report anyway
with a warning — a report whose snapshot is one sitting stale is worth having, and
a parent with no report at all because a worker died is not.

**A report is never regenerated.** `reports.generate_session_report` is idempotent
on `SessionReport.session_key`, and the read path generates one if this never ran
(`reports.report_for_session`) — so the two ways a report can come into existence
converge on one row rather than racing to overwrite each other.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.analytics import cost, reports
from apps.analytics.models import SessionReport
from apps.story.models import PlaySession

logger = logging.getLogger(__name__)

#: How long to wait for the mastery and interest updates before looking again.
#: Both are a handful of queries; this is sized for a worker that is busy, not for
#: one that is broken.
GATE_RETRY_DELAY_S = 15

#: How many times to wait. Three quarters of a minute in total, after which the
#: report is written with whatever the skill model currently says. Bounded because
#: an unbounded wait on a sibling task is a report that never appears.
GATE_RETRIES = 3


@shared_task(bind=True, max_retries=GATE_RETRIES)
def generate_report(self: Any, session_id: str) -> str:
    """Write the parent's report for one finished sitting.

    Returns a short outcome for the Celery result row: `generated`, `already` (a
    redelivery, or the read path got there first), `stale` (written without waiting
    any longer for the sibling updates) or `missing` (the sitting or the family was
    deleted while this was queued).
    """
    session = (
        PlaySession.objects.select_related("child")
        .prefetch_related("adventures")
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        # The same posture as every other post-session task: the deletion is the
        # newer instruction, and there is nothing here to report on (§13.8).
        logger.info("analytics.report_skipped session=%s reason=deleted", session_id)
        return "missing"

    if SessionReport.objects.filter(session_key=session.pk).exists():
        logger.info("analytics.report_skipped session=%s reason=already", session_id)
        return "already"

    waiting = session.learning_updated_at is None or session.interests_updated_at is None
    if waiting and self.request.retries < GATE_RETRIES:
        logger.info(
            "analytics.report_waiting session=%s attempt=%d", session_id, self.request.retries + 1
        )
        raise self.retry(countdown=GATE_RETRY_DELAY_S)

    # The highlight is a model call and it is spent on this sitting (§22.4). Same
    # re-entry as the other post-session tasks.
    with cost.trace_ids(child_id=session.child_id, session_id=session.pk):
        reports.generate_session_report(session)
    if waiting:
        # Worth a warning rather than an info: the snapshot in this report is one
        # sitting behind, so the *next* weekly report will measure movement from
        # the wrong baseline. Recoverable and quiet, which is why it is logged.
        logger.warning("analytics.report_stale session=%s reason=siblings_late", session_id)
        return "stale"
    return "generated"
