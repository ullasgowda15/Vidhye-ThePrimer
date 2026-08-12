"""The post-session report task and its ordering gate (PRODUCT.md §12.1, §5.4, §16.3).

`generate_report` is the fourth post-session sibling and the only one with an
opinion about order: it *snapshots* what the mastery and interest updates wrote,
and Celery promises nothing about the order siblings run in. The gate is what
makes "moved on this week" measurable at all — a report generated before those two
land records last week's numbers under today's date, and every weekly movement
claim is then measured from the wrong baseline.

That is the whole reason this file exists. The gate is a correctness mechanism
rather than a tidiness one, and it had no test.
"""

from __future__ import annotations

from typing import Any

import pytest
from celery.exceptions import Retry

from apps.analytics import tasks
from apps.analytics.models import SessionReport
from apps.analytics.tests.conftest import finish, play
from apps.story import orchestrator
from apps.story.models import PlaySession

pytestmark = pytest.mark.django_db


def _closed(child: Any) -> PlaySession:
    """A finished sitting whose sibling updates have deliberately *not* run.

    `no_post_session_jobs` stubs every `.delay` in this suite, so ending an
    adventure here leaves both stamps null — which is exactly the state the gate
    exists for.
    """
    adventure = play(child, correct=True)
    orchestrator.end_adventure(adventure)
    return PlaySession.objects.get(pk=adventure.session_id)


def test_the_report_waits_for_the_two_updates_it_snapshots(aarav, fake_ai) -> None:
    session = _closed(aarav)
    assert session.learning_updated_at is None
    assert session.interests_updated_at is None

    with pytest.raises(Retry):
        tasks.generate_report(str(session.pk))

    # Nothing written: a report whose snapshot predates the evidence it is meant to
    # summarise is worse than a report that arrives fifteen seconds later.
    assert not SessionReport.objects.filter(session_key=session.pk).exists()


def test_the_report_is_written_once_the_siblings_have_landed(aarav, fake_ai) -> None:
    session = finish(play(aarav, correct=True))
    assert session.learning_updated_at is not None
    assert session.interests_updated_at is not None

    assert tasks.generate_report(str(session.pk)) == "generated"
    assert SessionReport.objects.filter(session_key=session.pk).count() == 1


def test_the_wait_is_bounded_and_the_parent_still_gets_a_report(
    aarav, fake_ai, monkeypatch: Any, caplog
) -> None:
    """A report one sitting stale beats no report at all, and it is logged as such."""
    session = _closed(aarav)
    monkeypatch.setattr(tasks, "GATE_RETRIES", 0)

    with caplog.at_level("WARNING", logger="apps.analytics.tasks"):
        assert tasks.generate_report(str(session.pk)) == "stale"

    assert "analytics.report_stale" in caplog.text
    assert SessionReport.objects.filter(session_key=session.pk).count() == 1


def test_a_redelivered_task_does_not_write_a_second_report(
    aarav, fake_ai, monkeypatch: Any
) -> None:
    """Celery is at-least-once (§16.3), and a report costs a model call."""
    session = _closed(aarav)
    monkeypatch.setattr(tasks, "GATE_RETRIES", 0)

    assert tasks.generate_report(str(session.pk)) == "stale"
    assert tasks.generate_report(str(session.pk)) == "already"

    assert SessionReport.objects.filter(session_key=session.pk).count() == 1


def test_a_deleted_family_is_not_an_error(aarav) -> None:
    """§13.8: the deletion is the newer instruction, and there is nothing to recap."""
    assert tasks.generate_report("00000000-0000-0000-0000-000000000000") == "missing"
