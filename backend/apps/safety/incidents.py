"""Writing the incident record (PRODUCT.md §13.11, §17.4).

One rule governs this whole module: **recording an incident must never be able to
break a child's story.** Every write is wrapped, every failure is logged and
swallowed, and the caller carries on with the decision it had already made. The
alternative — a guard that raises because the incident table is full — would
convert a beat we successfully made safe into a dead screen, which is the failure
the guard exists to prevent, arriving through the guard itself.

The second rule is about what goes in: **no child-facing text**. `detail` carries
the *names* of the rules that fired, the verdict, the seq, the model that wrote
the beat — never the narration, never the profile field, never the injected
string. An incident table full of the content we refused to show anyone would be
a new place for that content to live, subject to no retention policy and read by
more people than the beat ever was (§13.8).
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from apps.analytics import events
from apps.analytics.models import Event
from apps.safety.models import Action, SafetyIncident, Severity

logger = logging.getLogger(__name__)


def record(
    *,
    category: str,
    action: str,
    severity: str = Severity.LOW,
    child: Any = None,
    adventure: Any = None,
    beat: Any = None,
    detail: dict[str, Any] | None = None,
    escalated: bool = False,
) -> SafetyIncident | None:
    """Write one incident. Returns the row, or `None` if it could not be written.

    `beat` is often `None` even when a beat is involved: a blocked generation is
    blocked *before* it is persisted, so there is no row to point at. That is the
    common case and not a gap — `detail` carries the seq, and the adventure FK
    places it.
    """
    payload = {key: value for key, value in (detail or {}).items() if value not in (None, "", [])}

    try:
        incident = SafetyIncident.objects.create(
            child=child,
            adventure=adventure,
            beat=beat,
            severity=severity,
            category=category[:64],
            detail=payload,
            action_taken=action,
            escalated=escalated,
        )
    except Exception:
        # Total, deliberately. See the module docstring: the story continues.
        logger.exception(
            "safety.incident_write_failed category=%s action=%s severity=%s",
            category,
            action,
            severity,
        )
        return None

    logger.warning(
        "safety.incident id=%s severity=%s category=%s action=%s escalated=%d child=%s "
        "adventure=%s detail=%s",
        incident.id,
        severity,
        category,
        action,
        int(escalated),
        getattr(child, "id", None),
        getattr(adventure, "id", None),
        payload,
    )
    # §5.6 and §22.5. The counter, not a second copy of the record: `SafetyIncident`
    # is the row a human reviews (§13.11), this is the row a rate is computed from,
    # and the rule about content applies to it twice over — nothing from `detail`
    # crosses, only the four labels the guardrail metric groups by.
    #
    # `track` swallows its own failures, which is the same posture the write above
    # takes and for the same reason: a child's story does not stop because a
    # counter could not be incremented.
    events.track(
        Event.SAFETY_INCIDENT,
        child=child,
        severity=severity,
        category=category[:64],
        action=action,
        escalated=escalated,
    )
    return incident


def mark_reviewed(incident: SafetyIncident, reviewer: Any) -> SafetyIncident:
    """Close one incident in the queue (§13.11).

    Idempotent: re-reviewing keeps the *first* reviewer and timestamp, because
    "who looked at this and when" is an audit fact and the second person to open
    the row did not discover it.
    """
    if incident.reviewed_at is not None:
        return incident
    incident.reviewed_by = reviewer
    incident.reviewed_at = timezone.now()
    incident.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
    return incident


def action_for(decision_action: str) -> str:
    """Map a pipeline verdict onto the `action_taken` column.

    Kept here so the two vocabularies stay separate on purpose: `policy.BLOCK` is
    what the guard *decided*, and `Action.BLOCKED` is what the product *did* about
    it. They agree today and they will not always — a future flag that logs
    without enforcing would change one and not the other.
    """
    from apps.safety import policy

    return {
        policy.REWRITE: Action.REWRITTEN,
        policy.BLOCK: Action.BLOCKED,
        policy.ESCALATE: Action.ESCALATED,
    }.get(decision_action, Action.BLOCKED)
