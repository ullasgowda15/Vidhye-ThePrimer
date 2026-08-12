"""The event pipeline's write end (PRODUCT.md §5.6, §17.4, §13.8).

Four properties, and each of them is a rule from `events.py`'s docstring that the
model alone cannot enforce:

1. telemetry never reaches the caller — a child's story does not stop because a
   counter could not be written;
2. the name comes from a closed set;
3. `props` carries shape and never content;
4. the child and parent keys cascade, so §13.7's delete is a delete here too —
   while the *session* key survives §17.5's pruning, which is a different thing.

Plus the emissions themselves: every service the brief names actually emits.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import events
from apps.analytics.models import Event
from apps.analytics.tests.conftest import finish, play
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db


class TestTelemetryNeverReachesTheCaller:
    """Rule 1. The one that a child is on the other end of."""

    def test_a_write_failure_is_swallowed(self, aarav: Child, monkeypatch: Any) -> None:
        def explode(**_: Any) -> None:
            raise RuntimeError("the analytics table is on fire")

        monkeypatch.setattr(events.Event.objects, "create", explode)

        # No raise, and a `None` the caller is documented never to branch on.
        assert events.track(Event.CHILD_CREATED, child=aarav) is None

    def test_a_failed_event_does_not_poison_the_caller_transaction(
        self, aarav: Child, monkeypatch: Any
    ) -> None:
        """The savepoint in `_write`, which is the subtle half of rule 1.

        Postgres marks a whole transaction failed after any error inside it. Without
        the inner `atomic`, an `IntegrityError` from the insert would take the
        caller's real work down with it — several queries later, for a reason
        nothing points at, and with `track`'s `except` having reported success.
        """
        from django.db import IntegrityError, transaction

        real_create = Event.objects.create
        calls = {"n": 0}

        def fail_once(**kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("simulated")
            return real_create(**kwargs)

        monkeypatch.setattr(events.Event.objects, "create", fail_once)

        with transaction.atomic():
            events.track(Event.CHILD_CREATED, child=aarav)
            # The caller's own work still lands. Without the savepoint this write
            # raises `TransactionManagementError` instead.
            aarav.first_name = "Aarav"
            aarav.save(update_fields=["first_name"])

        aarav.refresh_from_db()
        assert aarav.first_name == "Aarav"


class TestTheNameIsAClosedSet:
    """Rule 2. A typo is a metric that reads zero forever."""

    def test_an_unknown_name_is_dropped_in_production(self, aarav: Child, settings: Any) -> None:
        settings.DEBUG = False
        assert events.track("adventure.startd", child=aarav) is None
        assert Event.objects.count() == 0

    def test_an_unknown_name_raises_in_development(self, aarav: Child, settings: Any) -> None:
        settings.DEBUG = True
        with pytest.raises(ValueError, match="Unknown event name"):
            events.track("adventure.startd", child=aarav)

    def test_every_name_the_metrics_module_queries_is_in_the_set(self) -> None:
        """The two halves of the pipeline agree on the vocabulary.

        `metrics.py` resolves its queries through these constants rather than
        through string literals, which is what makes this assertion possible at
        all — and what makes a rename impossible to do halfway.
        """
        from apps.analytics import metrics

        queried = {
            metrics.Event.PARENT_SIGNED_UP,
            metrics.Event.ONBOARDING_COMPLETED,
            metrics.Event.ADVENTURE_STARTED,
            metrics.Event.ADVENTURE_COMPLETED,
            metrics.Event.SESSION_ENDED,
            metrics.Event.SAFETY_INCIDENT,
        }
        assert queried <= Event.NAMES


class TestPropsCarryShapeAndNeverContent:
    """Rule 3, which is §13.8 in one function."""

    def test_a_long_string_is_truncated(self, aarav: Child) -> None:
        narration = "Pip hopped along the riverbank. " * 40
        event = events.track(Event.BEAT_DELIVERED, child=aarav, oops=narration)

        assert event is not None
        assert len(event.props["oops"]) == events.MAX_STRING
        assert narration not in event.props["oops"]

    def test_containers_are_dropped_rather_than_serialised(self, aarav: Child) -> None:
        """A `Beat`, a list of the child's answers, a dict of a profile — none of
        them may become a JSON column nobody remembered to put a retention policy
        on. Passing `str(...)` is how a caller opts a value in deliberately."""
        event = events.track(
            Event.CHOICE_MADE,
            child=aarav,
            answers=["a frog", "three stones"],
            profile={"fears": ["the dark"]},
            seq=2,
        )

        assert event is not None
        assert event.props == {"seq": 2}

    def test_the_number_of_props_is_capped(self, aarav: Child) -> None:
        # `dict[str, Any]` so mypy reads the unpacking as props rather than as
        # attempts at `track`'s named keyword-only arguments.
        wide: dict[str, Any] = {f"k{index}": index for index in range(events.MAX_PROPS + 5)}
        event = events.track(Event.BEAT_DELIVERED, child=aarav, **wide)

        assert event is not None
        assert len(event.props) == events.MAX_PROPS

    def test_scalars_survive_unchanged(self, aarav: Child) -> None:
        event = events.track(
            Event.CHOICE_MADE,
            child=aarav,
            seq=3,
            latency_ms=2100,
            assisted=False,
            difficulty=0.42,
            chosen_key="b",
            nothing=None,
        )

        assert event is not None
        assert event.props == {
            "seq": 3,
            "latency_ms": 2100,
            "assisted": False,
            "difficulty": 0.42,
            "chosen_key": "b",
            "nothing": None,
        }


class TestIdentityAndDeletion:
    """Rule 4, and the invariant that decided how it is written.

    The first draft of `Event` kept a bare `child_key` UUID beside the FK, so that
    a September completion rate would not change because a family left in October.
    `test_children.py::test_every_fk_to_child_cascades_at_the_model_level` and its
    sibling in `test_privacy.py` both refused it, and they were right to: the
    privacy disclosure a parent reads says the data goes at once, and analytics is
    not allowed a footnote on that sentence. These two tests pin the outcome from
    this side, so nobody re-opens it by accident.
    """

    def test_the_parent_is_derived_from_the_child(self, aarav: Child) -> None:
        # Almost every child-scoped event is also a fact about an account, and no
        # caller should have to remember to say so twice.
        event = events.track(Event.CHILD_CREATED, child=aarav)

        assert event is not None
        assert event.parent_id == aarav.parent_id

    def test_deleting_a_child_takes_their_events_with_it(self, aarav: Child) -> None:
        events.track(Event.ADVENTURE_COMPLETED, child=aarav)
        events.track(Event.BEAT_DELIVERED, child=aarav, seq=1)

        aarav.delete()

        assert Event.objects.count() == 0

    def test_deleting_a_child_takes_their_cost_spans_too(self, aarav: Child) -> None:
        """The same rule one table over, and the one it costs us something.

        A family leaving rewrites the spend history a pricing decision was judged
        against. That is a real loss and it is still the right way round.
        """
        from apps.analytics import cost
        from apps.analytics.models import CostKind, GenerationCost

        with cost.trace(child=aarav):
            cost.record(kind=CostKind.LLM, operation="beat", provider="scripted")
        assert GenerationCost.objects.count() == 1

        aarav.delete()

        assert GenerationCost.objects.count() == 0

    def test_a_pruned_sitting_leaves_its_events_and_costs_countable(self, aarav: Child) -> None:
        """A session being pruned is **not** a delete (§17.5), and the FK says so.

        `session` is the one foreign key here that does not cascade, because raw
        beats are discarded long before the derived meaning stops mattering. Losing
        cost-per-session for last quarter because the sittings aged out would be a
        retention policy quietly eating a metric.
        """
        from apps.analytics import cost
        from apps.analytics.models import CostKind, GenerationCost
        from apps.story.models import PlaySession

        session = PlaySession.objects.create(child=aarav)
        events.track(Event.ADVENTURE_COMPLETED, child=aarav, session=session)
        with cost.trace(child=aarav, session=session):
            cost.record(kind=CostKind.LLM, operation="beat", provider="scripted")
        session_key = session.pk

        session.delete()

        event = Event.objects.get(name=Event.ADVENTURE_COMPLETED)
        assert event.session_id is None
        assert event.session_key == session_key

        span = GenerationCost.objects.get()
        assert span.session_id is None
        assert span.session_key == session_key


class TestEventsFlowFromEveryService:
    """Criterion 1's first half: the emissions exist, from the services the brief
    names. Each of these is a funnel step that reads as a perfect score if it is
    silently missing.

    The onboarding half — signup, consent, child, companion — is driven through
    the real endpoints in `tests/test_analytics_pipeline.py`, because those four
    are a *flow* and the thing worth asserting is that a parent walking it
    produces a funnel.
    """

    def test_a_played_adventure_emits_start_beats_choices_completion_and_session_end(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        adventure = play(aarav, correct=True)
        finish(adventure)

        counts = {
            name: Event.objects.filter(name=name).count()
            for name in (
                Event.ADVENTURE_STARTED,
                Event.BEAT_DELIVERED,
                Event.CHOICE_MADE,
                Event.ADVENTURE_COMPLETED,
                Event.SESSION_ENDED,
            )
        }
        assert counts[Event.ADVENTURE_STARTED] == 1
        assert counts[Event.ADVENTURE_COMPLETED] == 1
        assert counts[Event.SESSION_ENDED] == 1
        assert counts[Event.BEAT_DELIVERED] == adventure.estimated_beats
        assert counts[Event.CHOICE_MADE] == adventure.estimated_beats

        # Every one of them is tied to the sitting, which is what makes them
        # groupable at all.
        assert Event.objects.filter(session_key=adventure.session_id).count() == sum(
            counts.values()
        )

    def test_an_abandoned_adventure_emits_no_completion(self, aarav: Child, fake_ai: Any) -> None:
        """The numerator of `completion_rate`, and the reason it is a rate.

        A child who wandered off is recorded as an *absence* of a completion plus
        a `session.ended` that says why — not as a `completed: false` flag that a
        careless query would count anyway.
        """
        from apps.story.models import PlaySession

        adventure = play(aarav, correct=True)
        finish(adventure, reason=PlaySession.EndedReason.EXIT)

        assert Event.objects.filter(name=Event.ADVENTURE_COMPLETED).count() == 0
        ended = Event.objects.get(name=Event.SESSION_ENDED)
        assert ended.props["ended_reason"] == PlaySession.EndedReason.EXIT
        assert ended.props["completed"] is False

    def test_a_choice_event_carries_no_word_the_child_said(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        """§13.8, on the one event most tempting to widen.

        The label a child tapped and the answer they gave are the content of their
        afternoon. What analytics asks — did a tap happen, how long did it take,
        did the companion help — is answerable without either.
        """
        adventure = play(aarav, correct=True)
        labels = {
            str(option.get("label", "")).lower()
            for beat in adventure.beats.all()
            for option in (beat.options or [])
        }

        for event in Event.objects.filter(name=Event.CHOICE_MADE):
            blob = " ".join(str(value).lower() for value in event.props.values())
            assert not any(label and label in blob for label in labels)

    def test_a_safety_incident_emits_a_counter_and_no_detail(self, aarav: Child) -> None:
        from apps.safety import incidents
        from apps.safety.models import Action, Severity

        incidents.record(
            category="self_harm",
            action=Action.BLOCKED,
            severity=Severity.HIGH,
            child=aarav,
            detail={"matched_rule": "a phrase nobody should see twice"},
        )

        event = Event.objects.get(name=Event.SAFETY_INCIDENT)
        assert event.props["severity"] == Severity.HIGH
        assert event.props["category"] == "self_harm"
        assert event.props["escalated"] is False
        assert "matched_rule" not in event.props
        assert "phrase" not in " ".join(str(v) for v in event.props.values())


class TestTheDrain:
    """§5.6's "drained to the warehouse", and §13.8's retention on top of it."""

    def test_export_writes_ndjson_and_marks_the_rows(self, aarav: Child, tmp_path: Any) -> None:
        import json

        from django.core.management import call_command

        events.track(Event.ADVENTURE_STARTED, child=aarav, world="whispering_woods")
        events.track(Event.ADVENTURE_COMPLETED, child=aarav, beats=5)

        call_command("drain_events", out=str(tmp_path))

        files = list(tmp_path.glob("events-*.ndjson"))
        assert len(files) == 1
        rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
        assert {row["name"] for row in rows} == {
            Event.ADVENTURE_STARTED,
            Event.ADVENTURE_COMPLETED,
        }
        # The bare keys, not the FKs — a warehouse row must not need a join into a
        # database that will have deleted the child by the time anyone reads it.
        assert all(row["child_key"] == str(aarav.pk) for row in rows)
        assert Event.objects.filter(exported_at__isnull=True).count() == 0

    def test_a_second_drain_exports_nothing(self, aarav: Child, tmp_path: Any) -> None:
        from django.core.management import call_command

        events.track(Event.ADVENTURE_STARTED, child=aarav)
        call_command("drain_events", out=str(tmp_path))
        call_command("drain_events", out=str(tmp_path))

        assert len(list(tmp_path.glob("events-*.ndjson"))) == 1

    def test_prune_never_touches_an_unexported_row(
        self, aarav: Child, tmp_path: Any, settings: Any
    ) -> None:
        """The property that makes the retention job safe to run.

        A prune that can delete a row which never reached the export is a prune
        that can lose a week nobody knows is missing.
        """
        from django.core.management import call_command

        settings.EVENT_RETENTION_DAYS = 30
        old = timezone.now() - datetime.timedelta(days=90)
        events.track(Event.ADVENTURE_STARTED, child=aarav, ts=old)

        call_command("drain_events", out=str(tmp_path), prune=True)
        # Exported and pruned in one run — the export happens first, so the row is
        # archived before it is deleted.
        assert Event.objects.count() == 0

        events.track(Event.ADVENTURE_STARTED, child=aarav, ts=old)
        call_command("drain_events", out=str(tmp_path), prune=True, dry_run=True)
        assert Event.objects.count() == 1

    def test_prune_keeps_account_level_funnel_events(
        self, aarav: Child, tmp_path: Any, settings: Any
    ) -> None:
        """§13.8's retention is about *child behavioural* events.

        An account-level funnel event is not child data, and activation for a
        cohort has to stay computable after that cohort's raw play has aged out.
        """
        from django.core.management import call_command

        settings.EVENT_RETENTION_DAYS = 30
        old = timezone.now() - datetime.timedelta(days=90)
        events.track(Event.PARENT_SIGNED_UP, parent=aarav.parent, ts=old)
        events.track(Event.ADVENTURE_STARTED, child=aarav, ts=old)

        call_command("drain_events", out=str(tmp_path), prune=True)

        assert list(Event.objects.values_list("name", flat=True)) == [Event.PARENT_SIGNED_UP]
