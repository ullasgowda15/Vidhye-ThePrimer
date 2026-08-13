"""Companion asset generation, off the request path (PRODUCT.md §5.8, §16.9).

Seven provider calls — a descriptor, six illustrations, one voice line — is
thirty seconds of work. Doing it inside the POST would hold an HTTP connection
open for half a minute and tie the whole companion to one fragile request; doing
it on a worker means the parent gets their companion immediately and the art
arrives while they hand the tablet over.

The task is idempotent by construction (every step in `service` checks first),
so retrying it is always safe and never double-charges: a re-run finds the
finished assets in the cache.
"""

import logging

from celery import shared_task

from apps.analytics import cost
from apps.companion.models import AssetStatus, Companion
from apps.companion.service import build_assets

logger = logging.getLogger(__name__)


@shared_task
def generate_companion_assets(companion_id: str) -> str:
    """Build the reference set and greeting for one companion.

    Deliberately no automatic retry: `build_assets` already swallows per-asset
    failures and records `partial`/`failed`, so a retry loop here would re-run a
    task that has already decided what it could do. Re-running is a deliberate
    act — the dev command, or a parent creating again.
    """
    companion = Companion.objects.filter(pk=companion_id).select_related("child").first()
    if companion is None:
        # The child was deleted while the task was queued. Not an error: the
        # deletion is the newer instruction, and it cascaded this row away.
        logger.info("companion.assets_skipped companion=%s reason=deleted", companion_id)
        return AssetStatus.FAILED

    # Seven provider calls — a descriptor, six illustrations, a voice line — and
    # the largest single generation event in a child's life with the product
    # (§19.9, §22.4). **Child, and deliberately no session**: this happens before
    # any sitting exists, so attributing it to one would be an invention, and
    # cost/session goes on answering "what does a story cost". What the child id
    # buys is the other two things — "what does bringing a child on board cost",
    # and the `GenerationCost.child` cascade, which is what makes §13.7's delete
    # reach these rows. Without it they are the only analytics rows in the product
    # that outlive the family (Day-14 review, P1-2).
    #
    # Re-entered here rather than inherited: a `ContextVar` does not cross a
    # Celery boundary, the same seam `story.tasks.generate_beat_media` re-enters.
    with cost.trace_ids(child_id=companion.child_id):
        build_assets(companion)
    return companion.assets_status
