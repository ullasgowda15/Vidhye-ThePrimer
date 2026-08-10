"""The safety layer around the story loop (PRODUCT.md §13.2, Day 7).

The red-team suite proves the guards *judge* correctly. This file proves the
orchestrator *acts* on them — which is the other half, and the half that reaches a
child.

The thing it is most concerned with is **coverage**: not that beat one is guarded,
but that the opening, every consequence and the warm close all are. A safety layer
that wraps the first generation and not the fourth is a safety layer that ships
one safe beat per adventure.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.companion.models import Companion
from apps.media.exceptions import MediaBlocked, MediaProviderError
from apps.safety import policy
from apps.safety.fallbacks import FALLBACK_EVIDENCE_KEY
from apps.safety.models import Action, SafetyIncident, Severity
from apps.story import orchestrator
from apps.story.models import Adventure, Beat

pytestmark = pytest.mark.django_db


@pytest.fixture
def adventure(child: Any, fake_ai: Any) -> Adventure:
    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    return orchestrator.create_adventure(child)


def _unsafe(narration: str) -> str:
    """A schema-valid beat whose narration breaks the policy."""
    from conftest import ScriptedAIProvider

    beat = ScriptedAIProvider.beat("Aarav loves frogs.")
    beat["narration"] = narration
    return json.dumps(beat)


# ---------------------------------------------------------------------------
# Criterion 1 — coverage across the whole adventure
# ---------------------------------------------------------------------------
class TestEveryBeatIsGuarded:
    def test_the_opening_the_consequence_and_the_close_all_pass_through(
        self, adventure: Adventure, monkeypatch: Any
    ) -> None:
        """The criterion in one test: *every* beat, not just beat 1.

        `guard_output` is wrapped rather than replaced, so the beats are really
        generated and really graded and the recording is a side effect. What is
        asserted is the seq of every beat that reached the guard — a wrapper that
        only saw `[1]` is the failure this exists to catch.
        """
        graded: list[str] = []
        real_output = orchestrator.safety.guard_output
        real_input = orchestrator.safety.guard_input

        def spy_output(beat: Any, child: Any) -> Any:
            graded.append(beat.narration)
            return real_output(beat, child)

        guarded_inputs: list[int] = []

        def spy_input(context: dict[str, Any], child: Any, **kwargs: Any) -> Any:
            guarded_inputs.append(len(context))
            return real_input(context, child, **kwargs)

        monkeypatch.setattr(orchestrator.safety, "guard_output", spy_output)
        monkeypatch.setattr(orchestrator.safety, "guard_input", spy_input)

        last = adventure.estimated_beats
        for seq in range(1, last + 1):
            beat = orchestrator.create_beat(adventure, seq=seq)
            orchestrator.record_choice(beat, chosen_key="follow_river")

        assert len(graded) == last, "a beat reached the child without being graded"
        assert Beat.objects.filter(adventure=adventure).count() == last
        # The plan call is guarded too, so there is one more input guard than
        # there are beats.
        assert len(guarded_inputs) >= last

    def test_every_persisted_beat_records_that_it_was_checked(self, adventure: Adventure) -> None:
        """`unchecked` is now evidence of a bug rather than of Day 4.

        A row in that state after this day means something wrote a beat without
        going through `create_beat`, which is exactly what the state is for.
        """
        for seq in range(1, adventure.estimated_beats + 1):
            beat = orchestrator.create_beat(adventure, seq=seq)
            orchestrator.record_choice(beat, chosen_key="follow_river")

        statuses = set(
            Beat.objects.filter(adventure=adventure).values_list("safety_status", flat=True)
        )
        assert statuses == {Beat.SafetyStatus.PASSED}

    def test_the_close_is_guarded_like_any_other_beat(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """The warm close is the beat a model is most likely to get wrong (§8.8).

        It is also the one a "guard the first beat" implementation would miss, so
        it gets its own test rather than only its place in the sweep above.
        """
        last = adventure.estimated_beats
        fake_ai.queue_beat_response(_unsafe("The old badger died that winter. Goodnight."))

        close = orchestrator.create_beat(adventure, seq=last)

        assert close.safety_status == Beat.SafetyStatus.BLOCKED
        assert "died" not in close.narration
        assert SafetyIncident.objects.filter(category=policy.DEATH).exists()


# ---------------------------------------------------------------------------
# Criterion 2 — a block serves a warm fallback the child cannot detect
# ---------------------------------------------------------------------------
class TestABlockedBeatBecomesAWarmBeat:
    def test_an_unsafe_generation_is_replaced_rather_than_shown(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        fake_ai.queue_beat_response(_unsafe("Pip found a gun behind the tree."))

        beat = orchestrator.create_beat(adventure, seq=1)

        assert "gun" not in beat.narration
        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert beat.model.startswith("fallback:")

    def test_the_fallback_is_a_beat_in_every_way_the_child_can_see(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """Criterion 2's "cannot distinguish as an error", made checkable.

        The list is what a child actually perceives: something is said, there is a
        picture to draw, there is something to tap, and no word on the screen or
        in the clip announces a failure. Everything that *does* mark it as a
        fallback lives in columns a child never reads.
        """
        fake_ai.queue_beat_response(_unsafe("There was blood on the snow."))

        beat = orchestrator.create_beat(adventure, seq=1)

        assert beat.narration.strip()
        assert adventure.child.first_name in beat.narration
        assert "Pip" in beat.narration
        assert beat.scene["illustration_prompt"].strip()
        assert len(beat.options) >= 2
        assert (beat.interaction or {}).get("prompt_audio")
        assert orchestrator.spoken_line(beat).strip()

        said = json.dumps({"narration": beat.narration, "interaction": beat.interaction}).lower()
        for word in ("sorry", "error", "problem", "unavailable", "try again", "oops"):
            assert word not in said

    def test_the_story_carries_on_normally_after_one(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """A fallback is a beat in the middle of a story, not the end of one."""
        fake_ai.queue_beat_response(_unsafe("The fox killed the rabbit."))

        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key=first.options[0]["key"])
        second = orchestrator.create_beat(
            adventure, seq=2, prev_choice=orchestrator.turn_from(first)
        )

        assert second.safety_status == Beat.SafetyStatus.PASSED
        assert second.model == "scripted"
        adventure.refresh_from_db()
        assert adventure.running_summary.strip()

    def test_a_fallback_is_identifiable_in_the_data_forever(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        fake_ai.queue_beat_response(_unsafe("Grandpa drowned in the river."))

        beat = orchestrator.create_beat(adventure, seq=1)

        assert (beat.learning or {})["skill_evidence_key"] == FALLBACK_EVIDENCE_KEY


# ---------------------------------------------------------------------------
# The rewrite loop (§13.2's soft-fail branch)
# ---------------------------------------------------------------------------
class TestConstrainedRegeneration:
    def test_a_fixable_beat_is_regenerated_rather_than_replaced(
        self, adventure: Adventure, fake_ai: Any, child: Any
    ) -> None:
        """`rewrite` costs one more call and keeps the child's own story.

        A beat that is merely too intense for this child's ceiling is a story that
        is fine and a dial that is wrong, so replacing it with a stock meadow
        would throw away a personalised beat over something a second attempt
        fixes.
        """
        child.intensity_ceiling = 0
        child.save(update_fields=["intensity_ceiling"])
        adventure.refresh_from_db()

        fake_ai.queue_beat_response(_unsafe("A monster came out of the dark."))

        beat = orchestrator.create_beat(adventure, seq=1)

        assert beat.safety_status == Beat.SafetyStatus.REWRITTEN
        assert "monster" not in beat.narration
        assert fake_ai.beat_calls == 2, "one refusal, one constrained regeneration"
        incident = SafetyIncident.objects.get(category=policy.FRIGHTENING)
        assert incident.action_taken == Action.REWRITTEN
        assert incident.detail["rewrites"] == 1

    def test_the_constraint_names_the_category_and_never_quotes_the_beat(
        self, adventure: Adventure, fake_ai: Any, child: Any
    ) -> None:
        """Feeding refused text back is how a rewrite loop reinforces it (§13.5)."""
        child.intensity_ceiling = 0
        child.save(update_fields=["intensity_ceiling"])
        adventure.refresh_from_db()

        refused = "A monster came out of the dark and stared at them."
        fake_ai.queue_beat_response(_unsafe(refused))
        orchestrator.create_beat(adventure, seq=1)

        _, retry_user = fake_ai.prompts[-1]
        assert "refused by the safety review" in retry_user
        assert "monster" not in retry_user
        assert refused not in retry_user

    def test_a_model_that_will_not_comply_ends_at_the_fallback(
        self, adventure: Adventure, fake_ai: Any, child: Any, settings: Any
    ) -> None:
        """The loop is bounded. A child is not made to wait for a fourth attempt."""
        settings.SAFETY_MAX_REWRITES = 2
        child.intensity_ceiling = 0
        child.save(update_fields=["intensity_ceiling"])
        adventure.refresh_from_db()

        defiant = _unsafe("A monster came out of the dark.")
        fake_ai.queue_beat_response(defiant, defiant, defiant, defiant)

        beat = orchestrator.create_beat(adventure, seq=1)

        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert fake_ai.beat_calls == 3, "the original plus SAFETY_MAX_REWRITES"
        assert "monster" not in beat.narration


# ---------------------------------------------------------------------------
# Criterion 4 — the escalation path
# ---------------------------------------------------------------------------
class TestEscalation:
    def test_a_distressed_child_gets_comfort_and_their_parent_gets_an_alert(
        self, adventure: Adventure, fake_ai: Any, caplog: Any
    ) -> None:
        """§13.10, end to end.

        Three things have to be true at once and each is a separate promise: the
        beat de-escalates (the child sees somewhere gentle), the event is logged at
        high severity, and the parent-notify seam fires. The fourth promise is the
        one that is easiest to break by accident — the child is told *nothing*.
        """
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="count", value="my dad hits me")

        with caplog.at_level("ERROR"):
            beat = orchestrator.create_beat(
                adventure, seq=2, prev_choice=orchestrator.turn_from(first)
            )

        # De-escalated: the pre-vetted warm beat, not a generated reaction.
        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert beat.model == "fallback:child_input"
        assert (beat.learning or {})["skill_evidence_key"] == FALLBACK_EVIDENCE_KEY

        incident = SafetyIncident.objects.get(category=policy.DISTRESS)
        assert incident.severity == Severity.CRITICAL
        assert incident.escalated is True
        assert "safety.parent_notify" in caplog.text

        # And nothing about any of it is on the child's screen. The beat is a
        # picture-book moment out of the vetted pool; it does not counsel them,
        # does not refer to what they said, and does not tell them an adult has
        # been informed.
        said = beat.narration.lower()
        for word in ("worried", "grown-up", "tell someone", "safe now", "dad", "sorry"):
            assert word not in said

    def test_exactly_one_incident_is_filed_for_one_escalation(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """An alert a family learns to ignore is worse than no alert (§13.10)."""
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="count", value="I want to die")
        orchestrator.create_beat(adventure, seq=2, prev_choice=orchestrator.turn_from(first))

        assert SafetyIncident.objects.filter(category=policy.DISTRESS).count() == 1


# ---------------------------------------------------------------------------
# Criterion 6 — the image gate substitutes rather than fails
# ---------------------------------------------------------------------------
class TestTheImageGate:
    def test_a_refused_illustration_becomes_safe_stock_art(
        self, adventure: Adventure, fake_ai: Any, monkeypatch: Any
    ) -> None:
        beat = orchestrator.create_beat(adventure, seq=1)

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise MediaBlocked("local returned an asset the safety scan refused (blocked: nsfw).")

        monkeypatch.setattr(orchestrator.companion_service, "render_scene", refuse)

        media = orchestrator.illustrate_beat(beat)

        assert media is not None
        assert media.provider == "stock"
        assert media.is_servable, "a substitute the child cannot be served is not one"
        assert media.url.endswith(".svg")

        incident = SafetyIncident.objects.get(category="image_blocked")
        assert incident.action_taken == Action.SUBSTITUTED
        assert incident.severity == Severity.HIGH
        assert incident.beat_id == beat.id
        assert incident.detail["substituted"] is True

    def test_the_substitution_is_stable_across_replays(
        self, adventure: Adventure, fake_ai: Any, monkeypatch: Any
    ) -> None:
        """A picture that changed between two loads of one beat would be stranger
        than the substitution itself."""
        beat = orchestrator.create_beat(adventure, seq=1)
        monkeypatch.setattr(
            orchestrator.companion_service,
            "render_scene",
            lambda *a, **k: (_ for _ in ()).throw(MediaBlocked("nope")),
        )

        first = orchestrator.illustrate_beat(beat)
        second = orchestrator.illustrate_beat(beat)

        assert first is not None and second is not None
        assert first.id == second.id, "the second substitution should be a cache hit"

    def test_an_ordinary_provider_outage_is_still_a_picture_less_beat(
        self, adventure: Adventure, fake_ai: Any, monkeypatch: Any
    ) -> None:
        """The narrow catch, asserted as narrow.

        Substituting on every `MediaError` would paint stock art over an ordinary
        timeout, and a beat with no picture is already fully playable (§4.9) — so
        a beat that quietly became a stock meadow whenever an image provider was
        slow would be a real regression wearing a safety feature's clothes.
        """
        beat = orchestrator.create_beat(adventure, seq=1)
        monkeypatch.setattr(
            orchestrator.companion_service,
            "render_scene",
            lambda *a, **k: (_ for _ in ()).throw(MediaProviderError("timed out")),
        )

        with pytest.raises(MediaProviderError):
            orchestrator.illustrate_beat(beat)

        assert not SafetyIncident.objects.filter(category="image_blocked").exists()


# ---------------------------------------------------------------------------
# The prefetch's different posture
# ---------------------------------------------------------------------------
def test_the_prefetch_does_not_persist_a_fallback(adventure: Adventure, fake_ai: Any) -> None:
    """Nobody is waiting on a prefetch, so a blip must not become a generic beat.

    The stream that arrives a moment later would have generated the real thing;
    committing a fallback here would take that beat away from the child for a
    provider hiccup they would never otherwise have noticed.
    """
    from apps.story.ai.router import BeatValidationError

    fake_ai.queue_beat_response("not json", "still not json")

    with pytest.raises(BeatValidationError):
        orchestrator.create_beat(adventure, seq=1, allow_fallback=False)

    assert not Beat.objects.filter(adventure=adventure).exists()


# ---------------------------------------------------------------------------
# P0-1 — a provider transport error is a warm fallback, never a dead stream
# ---------------------------------------------------------------------------
def _timeout() -> Exception:
    import httpx
    import openai

    return openai.APITimeoutError(request=httpx.Request("POST", "https://openrouter.test"))


def _raises(exc: Exception) -> Any:
    def blow_up(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return blow_up


@pytest.fixture
def timing_out_ai(monkeypatch: Any, settings: Any) -> Any:
    """A **real** adapter in front of an SDK that times out.

    The scripted provider is no use for this: it stands *in place of* an adapter,
    so an exception raised from it would prove only that a test can raise one.
    What is under test is the adapter boundary itself, so the boundary has to be
    in the path — a real `OpenRouterProvider` with a real `openai.APITimeoutError`
    coming up through it.
    """
    import openai

    from apps.story.ai.router import ModelRouter, OpenRouterProvider

    class Timeout:
        def __init__(self, **_: Any) -> None:
            self.chat = self
            self.completions = self

        def create(self, **_: Any) -> Any:
            raise _timeout()

    monkeypatch.setattr(openai, "OpenAI", Timeout)
    settings.AI_STRUCTURED_OUTPUT_MODE = "auto"
    provider = OpenRouterProvider(api_key="test-key", model="vendor/model")
    monkeypatch.setattr(
        ModelRouter,
        "from_settings",
        classmethod(lambda cls, **_: ModelRouter(providers=[provider])),
    )
    return provider


class TestATransportErrorIsAWarmBeat:
    def test_a_timeout_mid_adventure_persists_a_fallback_beat_and_an_incident(
        self, adventure: Adventure, timing_out_ai: Any
    ) -> None:
        """§19.6, for the *commonest* real failure rather than only the schema one.

        Before the fix `except ai_router.AIError` never fired for a timeout, so no
        fallback was written, no incident was filed, and the exception went all the
        way out through the SSE generator.
        """
        beat = orchestrator.create_beat(adventure, seq=1)

        assert beat.pk is not None, "nothing was persisted, so the stream had nothing to send"
        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert beat.model == "fallback:output_guard"
        assert beat.narration.strip() and len(beat.options) >= 2, "it has to be playable"
        assert (beat.learning or {})["skill_evidence_key"] == FALLBACK_EVIDENCE_KEY

        incident = SafetyIncident.objects.get(category=policy.MALFORMED)
        assert incident.action_taken == Action.BLOCKED
        assert incident.beat_id == beat.id

    def test_a_timeout_while_planning_still_starts_the_story(
        self, child: Any, timing_out_ai: Any
    ) -> None:
        """Probe L, reversed. `create_adventure` used to raise `APITimeoutError` out
        of `build_plan`, so the POST that starts a story 500'd."""
        Companion.objects.create(child=child, archetype="dragon", name="Pip")

        started = orchestrator.create_adventure(child)

        assert started.pk is not None
        assert started.plan["source"] == "fallback"

    # `transaction=True` because `stream_beat` offloads the generation to a
    # thread with its own connection (§16.4) — the same reason the API-level
    # stream tests need it.
    @pytest.mark.django_db(transaction=True, serialized_rollback=True)
    def test_the_child_gets_a_playable_beat_rather_than_a_dead_stream(
        self, adventure: Adventure, timing_out_ai: Any
    ) -> None:
        """The promise §4.9 makes, checked where the child is.

        `StoryPage` has no error state by design and `onEnd` leaves the status where
        it was, so a generator that dies after `: open` is a companion that thinks
        forever. What the stream must carry is a `beat` frame and then a `done`.
        """
        import asyncio

        from apps.story import streaming

        async def collect() -> list[bytes]:
            return [chunk async for chunk in streaming.stream_beat(adventure)]

        frames = b"".join(asyncio.run(collect())).decode()

        assert "event: beat" in frames, "the child was left on the thinking companion"
        assert "event: done" in frames
        # And nothing in the payload tells them anything went wrong (§13).
        for word in ("error", "sorry", "unavailable", "timeout"):
            assert word not in frames.lower()

    def test_the_prefetch_still_refuses_to_commit_a_fallback(
        self, adventure: Adventure, timing_out_ai: Any
    ) -> None:
        """Wrapping the transport must not quietly widen what the prefetch writes.

        Nobody is waiting on a prefetch, so a blip there must still cost a
        speculative task rather than commit the child to a generic beat
        (`allow_fallback=False`). The fix changes the exception's *type*, not the
        posture that reads it.
        """
        from apps.story.ai.router import AIError

        with pytest.raises(AIError):
            orchestrator.create_beat(adventure, seq=1, allow_fallback=False)

        assert not Beat.objects.filter(adventure=adventure).exists()


class TestAClassifierThatErrorsDegrades:
    def test_a_raw_sdk_exception_from_the_grader_is_a_degradation_not_a_raise(
        self, child: Any, settings: Any, monkeypatch: Any
    ) -> None:
        """Probe M. `guard_output` caught only `ClassifierUnavailable`, and an
        `APITimeoutError` is not one — so the guard that exists to stop a child
        seeing an error was the thing raising it."""
        from apps.safety import classifier, pipeline

        settings.SAFETY_CLASSIFIER_ENABLED = True
        monkeypatch.setattr(classifier, "_router", _raises(_timeout()))

        decision = pipeline.guard_output(pipeline.safe_fallback_beat(child, seq=1), child)

        assert decision.degraded is True
        assert decision.ungraded is True, "the classifier was meant to run and did not"
        assert decision.passed, "the deterministic verdict still stands"
        incident = SafetyIncident.objects.get(category=policy.CLASSIFIER_UNAVAILABLE)
        assert incident.action_taken == Action.DEGRADED
        assert "APITimeoutError" in incident.detail["reason"]

    def test_the_deterministic_verdict_survives_the_degradation(
        self, child: Any, settings: Any, monkeypatch: Any
    ) -> None:
        """A guard that could not run is not a pass — and is not an amnesty either."""
        from apps.safety import classifier, pipeline
        from apps.story.ai import schemas

        settings.SAFETY_CLASSIFIER_ENABLED = True
        monkeypatch.setattr(classifier, "_router", _raises(_timeout()))

        unsafe = schemas.Beat.model_validate(
            json.loads(_unsafe("Pip found a gun behind the tree."))
        )
        decision = pipeline.guard_output(unsafe, child)

        assert decision.action == policy.BLOCK
        assert decision.degraded is True

    def test_something_unexpected_out_of_the_grader_is_caught_too(
        self, child: Any, settings: Any, monkeypatch: Any
    ) -> None:
        """The second lock on the same door.

        `classify` converts everything it can reach; this asserts `guard_output`
        does not *depend* on that being exhaustive, because the review found P0-1
        precisely by throwing a class nobody had listed.
        """
        from apps.safety import pipeline

        settings.SAFETY_CLASSIFIER_ENABLED = True
        monkeypatch.setattr(
            pipeline.classifier, "classify", _raises(RuntimeError("nobody listed this one"))
        )

        decision = pipeline.guard_output(pipeline.safe_fallback_beat(child, seq=1), child)

        assert decision.degraded is True
        assert decision.passed


# ---------------------------------------------------------------------------
# P2-1 — a beat row can say whether the strong guard graded it
# ---------------------------------------------------------------------------
def test_a_beat_the_classifier_never_graded_says_so_on_the_row(
    adventure: Adventure, fake_ai: Any, settings: Any, monkeypatch: Any
) -> None:
    """`passed` used to mean "both guards approved" and "the keyword list approved
    while the strong guard was unreachable" indistinguishably, so "which beats were
    actually graded?" was a question you answered by correlating timestamps."""
    from apps.safety import classifier

    settings.SAFETY_CLASSIFIER_ENABLED = True
    monkeypatch.setattr(classifier, "_router", _raises(_timeout()))

    beat = orchestrator.create_beat(adventure, seq=1)

    assert beat.safety_status == Beat.SafetyStatus.DEGRADED


def test_a_deliberately_disabled_classifier_is_not_a_degraded_beat(
    adventure: Adventure, fake_ai: Any
) -> None:
    """The counterweight, and the reason `ungraded` is narrower than `degraded`.

    CI and offline development run with the classifier off on purpose. Writing
    `degraded` on every beat of every run would make the column mean nothing — the
    same argument `_degraded` makes about not filing an incident per beat.
    `prod.py` refuses to boot with the flag off, so this state is development's.
    """
    beat = orchestrator.create_beat(adventure, seq=1)

    assert beat.safety_status == Beat.SafetyStatus.PASSED


# ---------------------------------------------------------------------------
# P0-2 — a disclosure is caught where it arrives, on every ingress path
# ---------------------------------------------------------------------------
DISCLOSURE = "my dad hits me"


def _the_alarm_was_raised() -> Any:
    """One critical, escalated `child_distress` incident, and exactly one."""
    incident = SafetyIncident.objects.get(category=policy.DISTRESS)
    assert incident.severity == Severity.CRITICAL
    assert incident.action_taken == Action.ESCALATED
    assert incident.escalated is True, "the parent-notify path ran"
    # Still no copy of what the child said, on this path either (§13.8).
    assert DISCLOSURE not in str(incident.detail).lower()
    return incident


class TestEveryIngressPathEscalates:
    """The three holes the Day-7 review proved, and the control that hid them.

    Every one of these is a disclosure in `Choice.value` that produced **zero**
    incidents before the fix, because the check lived inside `create_beat` and
    none of these paths generates a beat. The exposure is limited today — the
    shipped client only sends tapped keys — which is the argument for closing it
    now: Week 2 opens free text and voice, and an escalation path added after the
    surface that produces escalations is a path that has never run (§13.10).
    """

    def test_a_disclosure_answering_the_close(self, adventure: Adventure, fake_ai: Any) -> None:
        last = adventure.estimated_beats
        close = orchestrator.create_beat(adventure, seq=last)

        orchestrator.record_choice(close, chosen_key="count", value=DISCLOSURE)

        # There is no next beat to notice it on: this is the end of the story.
        assert orchestrator.schedule_advance(adventure, after=close) == orchestrator.ADVANCE_FINAL
        _the_alarm_was_raised()

    def test_a_disclosure_and_then_the_child_leaves(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        from apps.story.models import PlaySession
        from apps.story.tasks import advance_adventure

        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="count", value=DISCLOSURE)

        # The tab closes before the worker gets to the prefetch, so the beat that
        # would have read the disclosure is never generated.
        orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.EXIT)
        assert advance_adventure(str(adventure.id), str(first.id)) == "skipped"
        assert not Beat.objects.filter(adventure=adventure, seq=2).exists()

        _the_alarm_was_raised()

    def test_a_disclosure_when_the_next_beat_was_already_prefetched(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        first = orchestrator.create_beat(adventure, seq=1)
        # The worker got there first, so beat 2 is replayed rather than generated
        # and `guard_input` never runs on this turn at all.
        orchestrator.create_beat(adventure, seq=2)

        orchestrator.record_choice(first, chosen_key="count", value=DISCLOSURE)

        assert orchestrator.schedule_advance(adventure, after=first) == orchestrator.ADVANCE_READY
        _the_alarm_was_raised()

    def test_the_endpoint_has_already_escalated_by_the_time_it_answers(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """Where the words actually arrive. The `202` is the product's promise that
        the tap registered; the alarm must not be waiting behind a worker."""
        beat = orchestrator.create_beat(adventure, seq=1)

        choice, created = orchestrator.record_choice(beat, chosen_key="count", value=DISCLOSURE)

        assert created
        incident = _the_alarm_was_raised()
        assert incident.detail["surface"] == "choice_submitted"
        assert incident.beat_id == beat.id, "a reviewer can click through to the beat"

    def test_the_mid_story_control_still_files_exactly_one(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """The path that always worked, now guarded twice and still filing once.

        Two passes read the same sentence — `record_choice` on the way in and
        `guard_input` on the next generation — and an alert a family learns to
        ignore is worse than no alert, which includes learning to ignore it because
        it arrives twice (§13.10).
        """
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="count", value=DISCLOSURE)

        beat = orchestrator.create_beat(adventure, seq=2, prev_choice=orchestrator.turn_from(first))

        # The second pass still de-escalates the beat: dedupe suppresses the
        # duplicate *alert*, never the comfort the child gets.
        assert beat.model == "fallback:child_input"
        _the_alarm_was_raised()

    def test_a_second_disclosure_is_not_deduped_away(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """The dedupe is keyed on the utterance, not on the child.

        A child who says something worrying twice has said it twice. Suppressing
        the second would be the failure mode of a dedupe that reached too wide.
        """
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="count", value=DISCLOSURE)
        second = orchestrator.create_beat(adventure, seq=2)
        orchestrator.record_choice(second, chosen_key="count", value="I want to die")

        assert SafetyIncident.objects.filter(category=policy.DISTRESS).count() == 2

    def test_a_disclosure_nested_inside_the_payload_is_still_read(
        self, adventure: Adventure, fake_ai: Any
    ) -> None:
        """`Choice.value` is a `JSONField` and the client decides its shape.

        Week 2's voice turn is an object, not a string. A guard that called `str()`
        on the payload would work by accident today and stop working the day
        somebody adds a key.
        """
        beat = orchestrator.create_beat(adventure, seq=1)

        orchestrator.record_choice(
            beat, chosen_key="say", value={"transcript": DISCLOSURE, "confidence": 0.9}
        )

        _the_alarm_was_raised()
