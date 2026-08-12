"""Per-generation cost tracing and cost/session (PRODUCT.md §19.9, §22.4).

Criterion 2. Three things have to be true and each has a class here:

1. **Every generation is traced** — LLM, image and TTS — because §19.9's claim is
   "every", and instrumentation you have to remember to add is instrumentation the
   next call site will not have.
2. **The arithmetic is right, and a route we do not know the price of is reported
   as unknown rather than as free.** This is the property that decides whether the
   dashboard is worth reading the day someone swaps in a paid model.
3. **cost/session attributes to the right sitting**, including across the Celery
   boundary a `ContextVar` does not cross — which is where most of a beat's money
   is spent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import cost, pricing
from apps.analytics.models import CostKind, GenerationCost
from apps.analytics.tests.conftest import play
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db


class TestThePriceTable:
    """Pure arithmetic — no database, no providers."""

    def test_llm_input_and_output_are_priced_separately(self) -> None:
        """Output is typically 3-5x input, and a blended rate would make prompt
        minimisation (§19.9's second lever) look like it did nothing."""
        amount = pricing.llm_cost(
            "anthropic:claude-sonnet-5", input_tokens=1_000_000, output_tokens=0
        )
        assert amount == Decimal("3.00")

        amount = pricing.llm_cost(
            "anthropic:claude-sonnet-5", input_tokens=0, output_tokens=1_000_000
        )
        assert amount == Decimal("15.00")

    def test_a_free_route_is_priced_at_zero_not_left_unknown(self) -> None:
        """ "This route is free" and "we have no idea what this route costs" are
        different claims and are stored as different claims."""
        assert pricing.llm_cost(
            "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
            input_tokens=50_000,
            output_tokens=5_000,
        ) == Decimal(0)

    def test_an_unknown_model_is_unpriced(self) -> None:
        assert (
            pricing.llm_cost(
                "anthropic:some-model-we-have-not-listed", input_tokens=1, output_tokens=1
            )
            is None
        )

    def test_a_bare_provider_entry_prices_a_whole_vendor(self) -> None:
        assert pricing.tts_cost("google:en-US-Neural2-F", characters=1000) == Decimal("0.016")

    def test_an_integer_zero_override_is_a_price_and_not_an_absence(self, settings: Any) -> None:
        """The `is None` checks in `_rate`, and the reason they are not stylistic.

        A rate of `0` arriving as an integer from `AI_PRICE_TABLE` is falsy. An
        `or` chain would read it as "unknown" and mark every free generation
        unpriced — which is the exact confusion the module exists to prevent,
        arriving through the module.
        """
        settings.AI_PRICE_TABLE = {"image": {"someprovider": 0}}
        assert pricing.image_cost("someprovider") == Decimal(0)

    def test_the_environment_overrides_the_built_in_table(self, settings: Any) -> None:
        settings.AI_PRICE_TABLE = {"llm": {"anthropic:claude-sonnet-5": ["6.00", "30.00"]}}
        assert pricing.llm_cost(
            "anthropic:claude-sonnet-5", input_tokens=1_000_000, output_tokens=0
        ) == Decimal("6.00")

    def test_a_malformed_price_table_does_not_raise(self, settings: Any) -> None:
        """A mistyped environment variable costs an unpriced span, which the
        dashboard says out loud. It must never cost a beat."""
        settings.AI_PRICE_TABLE = "not a dict at all"
        assert pricing.llm_cost("anthropic:claude-opus-5", input_tokens=10, output_tokens=10)


class TestTheSpan:
    """`cost.record` and the context it reads."""

    def test_a_span_is_attributed_to_the_sitting_in_context(self, aarav: Child) -> None:
        from apps.story.models import PlaySession

        session = PlaySession.objects.create(child=aarav)
        with cost.trace(child=aarav, session=session):
            cost.record(
                kind=CostKind.LLM,
                operation="beat",
                provider="anthropic",
                model="claude-sonnet-5",
                units={"input_tokens": 1000, "output_tokens": 500},
            )

        span = GenerationCost.objects.get()
        assert span.session_key == session.pk
        assert span.child_id == aarav.pk
        assert span.priced is True
        assert span.cost_usd == Decimal("0.010500")

    def test_a_span_with_no_context_is_written_anyway(self) -> None:
        """It is real money. Hiding it would understate cost/session, and guessing
        a sitting for it would be worse than either."""
        cost.record(kind=CostKind.IMAGE, operation="illustration", provider="cloudflare")

        span = GenerationCost.objects.get()
        assert span.session_key is None
        assert span.priced is True

    def test_an_unknown_route_is_stored_as_unpriced_and_not_as_zero(self) -> None:
        cost.record(
            kind=CostKind.LLM,
            operation="beat",
            provider="somevendor",
            model="somemodel",
            units={"input_tokens": 100_000, "output_tokens": 20_000},
        )

        span = GenerationCost.objects.get()
        assert span.priced is False
        assert span.cost_usd == Decimal(0)

    def test_the_context_nests_and_restores(self, aarav: Child) -> None:
        from apps.story.models import PlaySession

        outer = PlaySession.objects.create(child=aarav)
        inner = PlaySession.objects.create(child=aarav)

        with cost.trace(child=aarav, session=outer):
            with cost.trace(child=aarav, session=inner):
                assert cost.current().session_id == inner.pk
            assert cost.current().session_id == outer.pk
        assert cost.current().session_id is None

    def test_recording_never_raises_into_the_caller(self, monkeypatch: Any) -> None:
        """Same rule as `events.track`, and for the same reason: a child is on the
        other end of most of these."""

        def explode(**_: Any) -> None:
            raise RuntimeError("the cost table is on fire")

        monkeypatch.setattr(GenerationCost.objects, "create", explode)
        assert cost.record(kind=CostKind.LLM, operation="beat", provider="anthropic") is None


class TestEveryGenerationIsTraced:
    """§19.9's "every", asserted against a real played adventure."""

    def test_a_played_adventure_traces_its_plan_and_every_beat(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        adventure = play(aarav)

        operations = list(
            GenerationCost.objects.filter(session_key=adventure.session_id).values_list(
                "operation", flat=True
            )
        )
        assert operations.count("plan") == 1
        # One per beat at minimum. More would mean a repair was billed, which is
        # also correct and is why this is `>=`.
        assert operations.count("beat") >= adventure.estimated_beats

    def test_media_is_traced_with_the_units_its_vendor_bills_for(self, aarav: Child) -> None:
        """A picture is per image; a spoken line is per character. Getting these
        two units the same way round is the whole of §22.4's media arithmetic."""
        from apps.media import service as media_service

        media_service.get_or_generate_audio(child=aarav, text="Hello there", voice_id="warm_low")
        media_service.get_or_generate_image(
            child=aarav,
            style_hash="s",
            character_hash="c",
            scene_hash="sc",
            prompt="a small round friendly dragon by a river",
        )

        spans = {span.kind: span for span in GenerationCost.objects.all()}
        assert spans[CostKind.TTS].units == {"characters": len("Hello there")}
        assert spans[CostKind.TTS].operation == "narration"
        assert spans[CostKind.IMAGE].units == {"images": 1}
        assert spans[CostKind.IMAGE].operation == "illustration"

    def test_a_cache_hit_is_not_a_span(self, aarav: Child) -> None:
        """The second identical picture costs nothing, so there is nothing to
        record. A zero-cost span for it would make cost/session fall for a reason
        that is not a saving."""
        from apps.media import service as media_service

        for _ in range(3):
            media_service.get_or_generate_image(
                child=aarav,
                style_hash="s",
                character_hash="c",
                scene_hash="sc",
                prompt="a small round friendly dragon by a river",
            )

        assert GenerationCost.objects.filter(kind=CostKind.IMAGE).count() == 1

    @pytest.mark.django_db(transaction=True, serialized_rollback=True)
    def test_the_media_worker_re_enters_the_sitting_context(
        self, aarav: Child, fake_ai: Any
    ) -> None:
        """The seam most likely to leak spend into the unattributed pile.

        A `ContextVar` is per-task, so the trace `create_beat` opened does not
        follow a picture into the worker that renders it — and illustration plus
        voice is most of what a beat costs. `story.tasks.generate_beat_media`
        re-enters it from the row; this is the test that says so.

        `transaction=True` for the same reason `test_orchestrator.py`'s media-task
        test needs it: `attach_media` renders on worker threads with their own
        database connections, which cannot see rows an uncommitted test
        transaction is holding.
        """
        from apps.story import orchestrator, tasks

        adventure = orchestrator.create_adventure(aarav)
        beat = orchestrator.create_beat(adventure, seq=1)
        GenerationCost.objects.all().delete()

        # Run the task body outside any ambient trace, exactly as a worker does.
        assert cost.current().session_id is None
        tasks.generate_beat_media(str(beat.pk))

        media_spans = GenerationCost.objects.filter(kind__in=[CostKind.IMAGE, CostKind.TTS])
        assert media_spans.exists()
        assert not media_spans.filter(session_key__isnull=True).exists()


class TestCostPerSession:
    """§22.4's headline, and the two numbers that say when it is understating."""

    def _span(self, *, usd: str, session_key: Any, kind: str = CostKind.LLM) -> None:
        GenerationCost.objects.create(
            kind=kind,
            operation="beat",
            provider="anthropic",
            model="claude-sonnet-5",
            units={},
            cost_usd=Decimal(usd),
            priced=True,
            session_key=session_key,
            occurred_at=timezone.now(),
        )

    def test_it_divides_total_spend_by_attributed_sittings(self, aarav: Child) -> None:
        import uuid

        first, second = uuid.uuid4(), uuid.uuid4()
        self._span(usd="0.030", session_key=first)
        self._span(usd="0.010", session_key=first)
        self._span(usd="0.020", session_key=second)

        window = cost.cost_per_session(
            since=timezone.now() - __import__("datetime").timedelta(hours=1),
            until=timezone.now() + __import__("datetime").timedelta(hours=1),
        )
        assert window["sessions"] == 2
        assert window["total_usd"] == Decimal("0.060")
        assert window["cost_per_session_usd"] == Decimal("0.030")

    def test_unattributed_spend_is_reported_beside_the_ratio(self) -> None:
        import datetime
        import uuid

        self._span(usd="0.040", session_key=uuid.uuid4())
        self._span(usd="0.005", session_key=None)

        window = cost.cost_per_session(
            since=timezone.now() - datetime.timedelta(hours=1),
            until=timezone.now() + datetime.timedelta(hours=1),
        )
        # The ratio uses only what could be attributed; the leftover is printed
        # next to it so a reader can see the ratio is understating.
        assert window["sessions"] == 1
        assert window["cost_per_session_usd"] == Decimal("0.045")
        assert window["unattributed_usd"] == Decimal("0.005")

    def test_no_sessions_reads_as_no_answer(self) -> None:
        import datetime

        window = cost.cost_per_session(
            since=timezone.now() - datetime.timedelta(hours=1),
            until=timezone.now() + datetime.timedelta(hours=1),
        )
        # `None`, not `0.0000`. A cost/session of zero is a claim about a cheap
        # week; this is the absence of one.
        assert window["cost_per_session_usd"] is None

    def test_unpriced_spans_are_counted(self) -> None:
        import datetime

        cost.record(kind=CostKind.LLM, operation="beat", provider="somevendor", model="x")

        window = cost.cost_per_session(
            since=timezone.now() - datetime.timedelta(hours=1),
            until=timezone.now() + datetime.timedelta(hours=1),
        )
        assert window["unpriced_spans"] == 1

    def test_a_real_sitting_produces_a_cost_per_session(self, aarav: Child, fake_ai: Any) -> None:
        """End to end: play, then read the number §22.4 calls a headline metric."""
        import datetime

        from apps.analytics.tests.conftest import finish

        finish(play(aarav))

        window = cost.cost_per_session(
            since=timezone.now() - datetime.timedelta(hours=1),
            until=timezone.now() + datetime.timedelta(hours=1),
        )
        assert window["sessions"] == 1
        assert window["spans"] > 0
        assert window["cost_per_session_usd"] is not None
        assert {row["kind"] for row in window["by_kind"]} == {CostKind.LLM}

    def test_a_paid_route_produces_a_non_zero_cost_per_session(
        self, aarav: Child, fake_ai: Any, settings: Any
    ) -> None:
        """The test that stops an all-zero suite from being reassuring.

        Every route CI exercises is priced at zero — the scripted provider costs
        nothing and the offline media renderers cost nothing — which is *true*, and
        which means every other assertion in this class would pass just as happily
        if the token counts never reached the price table at all. Pricing the
        scripted provider for one test makes the whole path carry a real number:
        tokens are metered, multiplied by a rate, summed across a sitting, and
        divided.

        The rate is deliberately absurd (a dollar per thousand input tokens) so the
        arithmetic is visible rather than lost in the fourth decimal place.
        """
        import datetime

        from apps.analytics.tests.conftest import finish

        settings.AI_PRICE_TABLE = {"llm": {"scripted": ["1000.00", "2000.00"]}}

        finish(play(aarav))

        window = cost.cost_per_session(
            since=timezone.now() - datetime.timedelta(hours=1),
            until=timezone.now() + datetime.timedelta(hours=1),
        )
        per_session = window["cost_per_session_usd"]
        assert per_session is not None
        assert per_session > Decimal("0")
        # One sitting, so the ratio *is* the total — the property that catches a
        # denominator counting something other than sittings.
        assert per_session == window["total_usd"]
        assert window["unpriced_spans"] == 0
        assert window["unattributed_usd"] == Decimal(0)
