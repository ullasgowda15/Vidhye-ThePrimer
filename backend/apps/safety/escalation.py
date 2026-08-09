"""The escalation path (PRODUCT.md §13.10).

> *"If in-session signals suggest a child in real distress or a disclosure of
> harm/abuse: the beat de-escalates to comfort and safety immediately; the event
> is logged with high severity; and a **parent-escalation path** is triggered
> (parent-facing alert), with documented internal procedures and, where legally
> applicable, appropriate external guidance."*

Three of those four things are real today and one is a seam, and it is worth
being exact about which is which.

**Real:** the beat de-escalates (the pipeline serves a warm fallback and never
the flagged text); the event is logged at high severity; a `SafetyIncident` row
is written with `escalated=True` and is visible in the admin queue within
seconds.

**A seam:** the *delivery* of the parent-facing alert. `apps.notifications` is an
empty shell until its own build day, and there is no email provider wired, no
push registration and no in-app alert surface. So `notify_parent` records the
alert as an outbound intent — who, about which child, why, and at what severity —
and logs it at `error` where the founder's own log will show it today.

**Why build the seam now rather than the alert.** Because the branch that *calls*
it is what needs to exist before Week 2 opens a free-text surface: an escalation
path that is added after the surface that produces escalations is a path that has
never run. Wiring a channel into this function later is one function body; adding
the decision to escalate afterwards is threading a new concept through the
generation pipeline.

**Why the child sees nothing.** A four-year-old who has said something worrying
must not be told that they have triggered a system. The story continues, warmly,
somewhere gentle (§13.10's "de-escalates to comfort"). The adults are the ones who
are told.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.safety.models import SafetyIncident

logger = logging.getLogger(__name__)

#: What a delivered alert would need to say. Assembled here rather than at the
#: channel so that when a channel lands it renders one payload rather than
#: inventing a second vocabulary for the same event.
ALERT_SUBJECT = "Something in today's story needs your attention"


def parent_of(incident: SafetyIncident) -> Any:
    """The account to alert, or `None` when the child row is already gone."""
    child = incident.child
    return child.parent if child is not None else None


def alert_payload(incident: SafetyIncident) -> dict[str, Any]:
    """The alert, as a channel would need it. Carries no story text.

    A parent is told *that* something happened, its category and when — not the
    sentence. The sentence is in neither the incident nor the alert by design
    (`incidents` module docstring); a parent who wants the detail asks a human,
    and that conversation is §13.11's documented procedure rather than an email.
    """
    child = incident.child
    return {
        "subject": ALERT_SUBJECT,
        "childId": str(child.id) if child is not None else None,
        "childName": child.first_name if child is not None else None,
        "incidentId": str(incident.id),
        "severity": incident.severity,
        "category": incident.category,
        "occurredAt": incident.created_at.isoformat(),
    }


def notify_parent(incident: SafetyIncident) -> bool:
    """Trigger the parent-escalation path for one incident. Returns whether it fired.

    Never raises. An escalation that threw would take down the story path it was
    called from, which would turn "a child said something worrying" into "the app
    stopped working" — the worst possible pairing.

    Marks the incident `escalated` so the flag records that the path *ran*, not
    merely that the severity would have justified it. Those are different facts and
    the admin queue filters on the first one.
    """
    try:
        parent = parent_of(incident)
        payload = alert_payload(incident)

        # The seam. When `apps.notifications` lands, this is where its outbound
        # task is enqueued — with `payload` unchanged, which is why it is built
        # above rather than formatted into the log line.
        logger.error(
            "safety.parent_notify incident=%s severity=%s category=%s parent=%s payload=%s "
            "channel=pending",
            incident.id,
            incident.severity,
            incident.category,
            getattr(parent, "id", None),
            payload,
        )

        if not incident.escalated:
            incident.escalated = True
            incident.save(update_fields=["escalated", "updated_at"])
        return True
    except Exception:
        logger.exception("safety.parent_notify_failed incident=%s", getattr(incident, "id", None))
        return False
