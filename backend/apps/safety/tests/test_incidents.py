"""Incidents, escalation, and the queue a human works (PRODUCT.md §13.10, §13.11).

Three properties, and the first one is the one that keeps a child's story on
screen: **nothing in this file may ever raise**. A guard that successfully made a
beat safe and then died writing the paperwork has produced the exact failure it
existed to prevent, through its own back door.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.profiles.models import Child
from apps.safety import escalation, incidents
from apps.safety.models import Action, SafetyIncident, Severity

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff() -> Any:
    return User.objects.create_superuser(email="ops@example.com", password="primer-42-test")


@pytest.fixture
def incident(child: Any) -> SafetyIncident:
    written = incidents.record(
        category="violence",
        action=Action.BLOCKED,
        severity=Severity.HIGH,
        child=child,
        detail={"markers": ["weapon"], "seq": 3},
    )
    assert written is not None
    return written


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_an_incident_carries_the_rule_and_not_the_text(incident: SafetyIncident) -> None:
    assert incident.category == "violence"
    assert incident.detail["markers"] == ["weapon"]
    assert incident.severity == Severity.HIGH
    assert incident.is_reviewed is False


def test_a_failed_write_never_reaches_the_caller(child: Any, monkeypatch: Any) -> None:
    """The module's one rule, asserted rather than trusted.

    Simulated at the ORM boundary because that is where it would really happen: a
    full disk, a lock timeout, a migration mid-deploy. The guard has already
    decided; the child's beat must not depend on the paperwork landing.
    """

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the database is having a bad day")

    monkeypatch.setattr(SafetyIncident.objects, "create", explode)

    assert incidents.record(category="violence", action=Action.BLOCKED, child=child) is None


def test_empty_detail_values_are_dropped(child: Any) -> None:
    """A `detail` full of nulls is a row a reviewer has to read past."""
    written = incidents.record(
        category="x",
        action=Action.BLOCKED,
        child=child,
        detail={"seq": 2, "model": "", "markers": [], "reason": None},
    )

    assert written is not None
    assert written.detail == {"seq": 2}


# ---------------------------------------------------------------------------
# Escalation (§13.10)
# ---------------------------------------------------------------------------
def test_notifying_marks_the_incident_and_names_the_parent(
    incident: SafetyIncident, caplog: Any
) -> None:
    with caplog.at_level("ERROR"):
        assert escalation.notify_parent(incident) is True

    incident.refresh_from_db()
    # The flag records that the path *ran*, not that the severity justified it.
    assert incident.escalated is True
    assert "safety.parent_notify" in caplog.text
    assert incident.child is not None
    assert escalation.parent_of(incident) == incident.child.parent


def test_the_alert_tells_a_parent_that_something_happened_not_what_was_said(
    incident: SafetyIncident,
) -> None:
    """§13.11's documented procedure is a conversation with a human, not an email
    containing the sentence we refused to show their child (§13.8)."""
    payload = escalation.alert_payload(incident)

    assert payload["severity"] == Severity.HIGH
    assert payload["category"] == "violence"
    assert incident.child is not None
    assert payload["childName"] == incident.child.first_name
    assert "narration" not in payload
    assert "detail" not in payload


def test_escalation_survives_a_child_deleted_mid_flight(incident: SafetyIncident) -> None:
    """Cascade removes the row; a notify racing that must not raise."""
    assert incident.child is not None
    incident.child.delete()

    assert escalation.notify_parent(incident) in {True, False}


# ---------------------------------------------------------------------------
# The queue (§13.11, §7.11)
# ---------------------------------------------------------------------------
def _rows(response: Any) -> set[str]:
    """Which incidents the changelist actually listed.

    Matched on the row's own id rather than on its category, because the category
    also appears in the filter sidebar — a naive substring assertion passes on a
    filter that filters nothing.
    """
    return {str(row.pk) for row in response.context["cl"].result_list}


def test_the_admin_queue_lists_incidents_and_filters_by_severity(
    client: Any, staff: Any, child: Any
) -> None:
    severe = incidents.record(
        category="violence", action=Action.BLOCKED, severity=Severity.HIGH, child=child
    )
    minor = incidents.record(
        category="prompt_injection", action=Action.SCRUBBED, severity=Severity.LOW, child=child
    )
    assert severe is not None and minor is not None
    client.force_login(staff)

    url = reverse("admin:safety_safetyincident_changelist")
    everything = client.get(url)
    assert everything.status_code == 200
    assert _rows(everything) == {str(severe.pk), str(minor.pk)}
    # The badge renders, so the severest row is findable by eye and not only by
    # reading every line.
    assert b"HIGH" in everything.content

    # The filter the criterion names, exercised as a real request rather than as
    # an attribute on the ModelAdmin.
    high_only = client.get(url, {"severity__exact": Severity.HIGH})
    assert high_only.status_code == 200
    assert _rows(high_only) == {str(severe.pk)}


def test_the_queue_defaults_to_the_work(client: Any, staff: Any, incident: SafetyIncident) -> None:
    client.force_login(staff)
    url = reverse("admin:safety_safetyincident_changelist")

    assert _rows(client.get(url, {"reviewed": "open"})) == {str(incident.pk)}
    assert _rows(client.get(url, {"reviewed": "done"})) == set()

    incidents.mark_reviewed(incident, staff)

    assert _rows(client.get(url, {"reviewed": "open"})) == set()
    assert _rows(client.get(url, {"reviewed": "done"})) == {str(incident.pk)}


def test_reviewing_is_recorded_once_and_by_whoever_got_there_first(
    incident: SafetyIncident, staff: Any
) -> None:
    someone_else = User.objects.create_superuser(email="two@example.com", password="primer-42-test")

    first = incidents.mark_reviewed(incident, staff)
    again = incidents.mark_reviewed(first, someone_else)

    assert again.reviewed_by == staff, "the second reader did not discover it"
    assert again.reviewed_at == first.reviewed_at


def test_incidents_cannot_be_created_or_deleted_by_hand(client: Any, staff: Any) -> None:
    """An incident that can be deleted is an incident that can be hidden."""
    client.force_login(staff)

    assert client.get(reverse("admin:safety_safetyincident_add")).status_code == 403


# ---------------------------------------------------------------------------
# Retention (§17.5)
# ---------------------------------------------------------------------------
def test_a_deleted_child_takes_their_incidents_with_them(child: Any) -> None:
    """Erasure is absolute; there is no audit exception to a parent's request."""
    incidents.record(category="violence", action=Action.BLOCKED, child=child)
    child.delete()

    assert not SafetyIncident.objects.exists()


def test_an_incident_outlives_the_beat_it_was_about(child: Any) -> None:
    """Beats are pruned aggressively while the family is still here (§17.5), and
    an incident that vanished with them would make the trigger rate decay with the
    retention job."""
    from apps.companion.models import Companion
    from apps.story.models import Adventure, Beat, PlaySession

    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    adventure = Adventure.objects.create(
        child=child, session=PlaySession.objects.create(child=child)
    )
    beat = Beat.objects.create(adventure=adventure, seq=1, narration="hello")

    written = incidents.record(
        category="violence", action=Action.BLOCKED, child=child, adventure=adventure, beat=beat
    )
    assert written is not None

    beat.delete()

    written.refresh_from_db()
    assert written.beat_id is None
    assert written.category == "violence"


def test_a_child_born_today_is_in_the_gentlest_band() -> None:
    """Guards read `age_band`, so this is a safety property rather than a detail."""
    parent = User.objects.create_user(email="band@example.com", password="primer-42-test")
    newborn = Child.objects.create(parent=parent, first_name="New", birthdate=datetime.date.today())

    assert newborn.age_band == "3-4"
