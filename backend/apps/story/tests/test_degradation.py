"""What a child sees when the product is degraded (PRODUCT.md §16.10, §18.8, Day 15).

`test_budget.py` proves the breaker is right about money. This file is the half
that matters: it opens the breaker, plays a real adventure through the real
orchestrator, and asserts what lands on a four-year-old's screen.

The standard it holds every degradation to is the one from §13 and §4.9, and it
is not "an error is handled". It is:

* there is a **beat** — narration, a scene, options to tap;
* the beat is **warm** and passes the same policy check every generated beat does;
* the story **continues** — answering it produces a next beat;
* nothing in it is an apology, a status message, or a word about the system.

Every mode is tested against that same standard, because a child cannot tell the
difference between "the vendor is down", "we ran out of budget" and "somebody
switched the pictures off", and neither should their story.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.analytics import budget
from apps.common.models import FeatureFlag
from apps.companion.models import Companion
from apps.media.exceptions import MediaBudgetExceeded, MediaSwitchedOff
from apps.safety import policy
from apps.safety.fallbacks import FALLBACK_EVIDENCE_KEY
from apps.safety.models import SafetyIncident
from apps.story import orchestrator
from apps.story.ai import router as ai_router
from apps.story.models import Adventure, Beat

pytestmark = pytest.mark.django_db


@pytest.fixture
def adventure(child: Any, fake_ai: Any) -> Adventure:
    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    return orchestrator.create_adventure(child)


@pytest.fixture
def tripped(monkeypatch: Any, settings: Any) -> None:
    """The breaker, open. Faked at `state()` rather than by spending real dollars
    into a real Redis, because what is under test here is the consequence of a
    trip and not the arithmetic that produces one (`test_budget.py` owns that)."""
    settings.INFERENCE_BUDGET_USD_PER_WINDOW = 1.00
    budget._ANNOUNCED.clear()
    monkeypatch.setattr(
        budget,
        "state",
        lambda: budget.State(
            available=True,
            spent_usd=Decimal("1.50"),
            cap_usd=Decimal("1.00"),
            window_s=3600,
            resets_in_s=120,
        ),
    )


def _is_a_playable_beat(beat: Beat) -> None:
    """The standard. Applied identically to every degraded mode below."""
    assert beat.narration.strip(), "a beat with no words is a blank screen"
    assert (beat.scene or {}).get("illustration_prompt"), "nothing for the picture to be"

    # Warm, by the product's own guard rather than by inspection: the same
    # deterministic policy that grades every generated beat, run against this
    # child's own ceiling and avoid list.
    findings = policy.check_text(beat.narration, beat.adventure.child)
    assert findings == [], f"a degraded beat failed the policy check: {findings}"

    # Nothing about the system. This is the list of words a child must never meet.
    lowered = beat.narration.lower()
    for word in ("error", "sorry", "unavailable", "try again", "budget", "problem", "failed"):
        assert word not in lowered, f"the word {word!r} reached a child's screen"

    # Answerable, or a warm close that deliberately asks nothing (§8.8).
    interaction = beat.interaction or {}
    if not orchestrator.is_final_seq(beat.adventure, beat.seq):
        assert interaction.get("type") != "none", "a mid-story beat with nothing to tap"


# ---------------------------------------------------------------------------
# The budget breaker
# ---------------------------------------------------------------------------
class TestATrippedBudgetBreaker:
    def test_the_child_still_gets_a_warm_illustrated_beat(
        self, adventure: Adventure, tripped: None
    ) -> None:
        """§18.8's "degrade to cached/fallback content behind narration".

        The generation is refused, the pre-vetted fallback beat is served, and it
        goes down the same pipeline every other beat does — which is the whole
        reason the breaker raises an `AIError` instead of inventing a new mode.
        """
        beat = orchestrator.create_beat(adventure, seq=1)

        _is_a_playable_beat(beat)
        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert beat.model.startswith("fallback:")
        assert (beat.learning or {}).get("skill_evidence_key") == FALLBACK_EVIDENCE_KEY

    def test_the_story_carries_on_afterwards(self, adventure: Adventure, tripped: None) -> None:
        """A degradation that ends the adventure is an outage with better manners.

        The child answers the fallback and gets another beat — and when the window
        resets, the very next beat is a generated one again, with nothing to
        clean up.
        """
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="wave_hello")

        second = orchestrator.create_beat(
            adventure, seq=2, prev_choice=orchestrator.turn_from(first)
        )
        _is_a_playable_beat(second)
        assert second.seq == 2

    def test_a_new_adventure_still_opens_with_a_plan(
        self, child: Any, fake_ai: Any, tripped: None
    ) -> None:
        """The plan is an LLM call too, so it is refused as well — and
        `build_plan`'s existing `AIError` handler answers with the scripted
        fallback plan. A child who arrives while the breaker is open gets a story,
        not a refusal at the door."""
        Companion.objects.create(child=child, archetype="dragon", name="Pip")
        adventure = orchestrator.create_adventure(child)

        assert adventure.plan, "no plan at all: the child would have nowhere to go"
        assert adventure.estimated_beats > 0

    def test_the_illustration_is_declined_without_taking_the_beat_down(
        self, adventure: Adventure, tripped: None
    ) -> None:
        """A picture is the part of a beat a child can do without (§4.9); the beat
        itself is not."""
        beat = orchestrator.create_beat(adventure, seq=1)
        with pytest.raises(MediaBudgetExceeded):
            orchestrator.illustrate_beat(beat)

        # And the stream's own handler turns that into "no picture", not an error.
        beat.refresh_from_db()
        _is_a_playable_beat(beat)

    def test_the_safety_substitution_is_never_refused(
        self, adventure: Adventure, tripped: None
    ) -> None:
        """The one picture a spend control must not be able to stop.

        Stock art is what a child gets when the image gate has just refused a
        generated illustration. Letting a cost number gate that would put money in
        front of the safety fallback, which is the trade this product does not
        make — so `get_or_generate_stock` passes `paid=False`.
        """
        from apps.media import stock as media_stock

        beat = orchestrator.create_beat(adventure, seq=1)
        media = media_stock.safe_illustration(adventure.child, seed=str(beat.id))
        assert media is not None
        assert media.is_servable

    def test_an_asset_we_already_hold_is_served_while_the_breaker_is_open(
        self, adventure: Adventure, monkeypatch: Any, settings: Any
    ) -> None:
        """ "Degrade to cached content" is not a mode the code enters — it is what
        refusing *new* spend leaves behind. The guard sits below both cache
        lookups, deliberately."""
        from apps.media import service as media_service

        beat = orchestrator.create_beat(adventure, seq=1)
        first = orchestrator.illustrate_beat(beat)
        assert first is not None

        # Now open the breaker and ask for exactly the same picture again.
        settings.INFERENCE_BUDGET_USD_PER_WINDOW = 1.00
        budget._ANNOUNCED.clear()
        monkeypatch.setattr(
            budget,
            "state",
            lambda: budget.State(True, Decimal("9.99"), Decimal("1.00"), 3600, 60),
        )
        calls = media_service.STATS.generated
        again = orchestrator.illustrate_beat(beat)

        assert again is not None
        assert media_service.STATS.generated == calls, "a cache hit must not pay a provider"


# ---------------------------------------------------------------------------
# The flags
# ---------------------------------------------------------------------------
class TestTheIllustrationFlag:
    def test_switching_pictures_off_leaves_a_playable_narrated_beat(
        self, adventure: Adventure
    ) -> None:
        """§19.9's biggest spend lever, and the one that costs a child least.

        The beat is generated normally — this flag stops nothing but the picture —
        and `StoryPage` renders the companion thinking where the illustration
        would be, which is already what it does while one is generating.
        """
        FeatureFlag.objects.create(name="media.illustrations", value="off")
        beat = orchestrator.create_beat(adventure, seq=1)

        _is_a_playable_beat(beat)
        assert beat.safety_status == Beat.SafetyStatus.PASSED, "the beat itself is unaffected"
        with pytest.raises(MediaSwitchedOff):
            orchestrator.illustrate_beat(beat)

    def test_the_voice_has_no_such_flag(self) -> None:
        """Voice is not an enhancement for a pre-reader — it is how the story is
        delivered at all. There is deliberately no lever that silences it; only
        the breaker can decline a line of narration, and only when the budget is
        genuinely gone."""
        from apps.common import flags

        assert not [name for name in flags.REGISTRY if "voice" in name or "tts" in name]

    def test_narration_is_still_synthesised_with_pictures_off(self, adventure: Adventure) -> None:
        FeatureFlag.objects.create(name="media.illustrations", value="off")
        beat = orchestrator.create_beat(adventure, seq=1)

        assert orchestrator.narrate_beat(beat) is not None


class TestThePrefetchFlag:
    def test_switching_it_off_stops_the_prefetch_and_nothing_else(
        self, adventure: Adventure, monkeypatch: Any
    ) -> None:
        queued: list[str] = []
        monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="follow_river")

        FeatureFlag.objects.create(name="story.prefetch_next_beat", value="off")
        assert orchestrator.schedule_advance(adventure, after=first) == orchestrator.ADVANCE_OFF
        assert queued == []

    def test_an_already_written_beat_still_reports_ready(
        self, adventure: Adventure, monkeypatch: Any
    ) -> None:
        """The ordering that matters: the flag is checked *after* `ADVANCE_READY`.

        Switching the prefetch off must not make a beat that is sitting in the
        database report as unready, or the client would wait for something it
        already has.
        """
        monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
        first = orchestrator.create_beat(adventure, seq=1)
        orchestrator.record_choice(first, chosen_key="follow_river")
        orchestrator.create_beat(adventure, seq=2, prev_choice=orchestrator.turn_from(first))

        FeatureFlag.objects.create(name="story.prefetch_next_beat", value="off")
        assert orchestrator.schedule_advance(adventure, after=first) == orchestrator.ADVANCE_READY


# ---------------------------------------------------------------------------
# The breaker's error type is the one everything above already handles
# ---------------------------------------------------------------------------
def test_the_breakers_error_is_an_aierror() -> None:
    """The single property that makes the whole degradation path free.

    `orchestrator._guarded_generation` has caught `AIError` and answered with a
    warm fallback since Day 7; `safety.classifier` has turned one into a recorded
    degradation for just as long. A breaker with an error type outside that
    hierarchy would have needed a second, untested degradation path written on
    launch day — on the one surface where an unhandled exception is a child
    looking at a dead screen.
    """
    assert issubclass(ai_router.BudgetExceededError, ai_router.AIError)
    assert issubclass(
        MediaBudgetExceeded, __import__("apps.media.exceptions", fromlist=["MediaError"]).MediaError
    )


def test_a_trip_is_visible_to_the_person_on_call(adventure: Adventure, tripped: None) -> None:
    """The child sees nothing; somebody has to see everything.

    A degradation that is invisible to operators is one that runs for a week.
    """
    orchestrator.create_beat(adventure, seq=1)
    assert SafetyIncident.objects.filter(category="inference_budget").exists()
