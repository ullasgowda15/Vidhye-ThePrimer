"""Gemini on Vertex AI: the schema it is sent and the refusals it comes back with.

Two things are being proven here, and they fail in opposite directions.

**The schema.** Gemini's `response_schema` is an OpenAPI 3.0 subset, not JSON
Schema, and it 400s the whole request on a keyword it does not know. A `Beat`
schema Gemini rejects is not a degraded beat — it is *every* story in the product
dead, on every call, from the moment the provider is switched on. So the tests
below run the **real** beat contract through `gemini_schema` and then validate the
result against `google.genai.types.Schema`, which is the SDK's own model of what
the wire accepts and which forbids extra keys. That is as close to "Gemini
accepts it" as anything without a credential gets, and it is a stronger check
than a hand-written list of forbidden keywords: a keyword nobody thought of still
fails.

**The refusals.** Everything above `apps.story.ai.router` keys on `AIError`
(`_transport_error`). A Gemini block that came back as an empty string, or as a
`google.genai.errors.ClientError`, would satisfy none of the three handlers that
exist to catch it — and §4.9 forbids showing a child an error, which is exactly
why the layer below must never hand one up. Each way Gemini can decline is driven
through the provider here, and the last test drives one all the way through
`orchestrator.create_beat` to prove what a four-year-old actually gets.

No network: `apps.common.vertex.client` is replaced with a fake whose responses
are built out of the **real SDK response types**, so an attribute this provider
reads and the SDK does not have is a failure here rather than in production.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from google.genai import types

from apps.story.ai.router import (
    AIError,
    GoogleProvider,
    ProviderError,
)
from apps.story.ai.schemas import (
    Beat,
    GeminiSchemaError,
    beat_json_schema,
    gemini_schema,
    json_schema_for,
)
from tests.test_ai_router import VALID_BEAT

CONTEXT = {"system": "be warm", "user": "one beat about frogs"}


# ---------------------------------------------------------------------------
# A fake Vertex client, built from the SDK's own response types
# ---------------------------------------------------------------------------
def _response(
    text: str = "",
    *,
    finish: str = "STOP",
    block: str = "",
    candidates: bool = True,
    usage: tuple[int, int, int] | None = (11, 7, 0),
) -> types.GenerateContentResponse:
    """One Gemini response, in whatever shape the test needs it."""
    metadata = (
        types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0],
            candidates_token_count=usage[1],
            thoughts_token_count=usage[2],
        )
        if usage is not None
        else None
    )
    return types.GenerateContentResponse(
        candidates=(
            [
                types.Candidate(
                    content=(
                        types.Content(role="model", parts=[types.Part(text=text)]) if text else None
                    ),
                    finish_reason=types.FinishReason(finish),
                )
            ]
            if candidates
            else []
        ),
        prompt_feedback=(
            types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason(block))
            if block
            else None
        ),
        usage_metadata=metadata,
    )


class FakeVertex:
    """Stands in for `genai.Client`, recording exactly what it was asked."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.models = self

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def vertex(monkeypatch: Any, settings: Any) -> Any:
    """Configure Vertex and hand the test control of what Gemini answers."""
    settings.GOOGLE_CLOUD_PROJECT = "primer-test"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/service-account.json"

    def install(*responses: Any) -> FakeVertex:
        from apps.common import vertex as vertex_module

        client = FakeVertex(*responses)
        monkeypatch.setattr(vertex_module, "client", lambda: client)
        return client

    return install


# ---------------------------------------------------------------------------
# The schema (acceptance criterion 2 — the highest-risk item)
# ---------------------------------------------------------------------------
class TestTheBeatContractSurvivesTranslation:
    def test_the_real_beat_schema_is_something_the_sdk_will_send(self) -> None:
        """The whole ballgame. `types.Schema` forbids extra keys, so this fails on
        any keyword Gemini's subset does not have — including one nobody here
        thought to blacklist."""
        types.Schema.model_validate(gemini_schema(beat_json_schema()))

    @pytest.mark.parametrize(
        "model_name", ["Beat", "StoryPlan", "SessionMemories", "SafetyVerdict"]
    )
    def test_every_contract_we_actually_generate_survives_it(self, model_name: str) -> None:
        """Not just the beat. The safety grader's verdict and the memory digest go
        through the same provider, and a grader whose schema 400s is §13.3's
        guarantee silently off — the exact failure the Day-7 review found once
        already, arriving by a different road."""
        from pydantic import BaseModel

        from apps.memory.prompts import SessionMemories
        from apps.safety.classifier import SafetyVerdict
        from apps.story.ai.schemas import StoryPlan

        contracts: dict[str, type[BaseModel]] = {
            "Beat": Beat,
            "StoryPlan": StoryPlan,
            "SessionMemories": SessionMemories,
            "SafetyVerdict": SafetyVerdict,
        }
        types.Schema.model_validate(gemini_schema(json_schema_for(contracts[model_name])))

    def test_no_reference_survives_anywhere_in_the_tree(self) -> None:
        """Gemini resolves no references, so a `$ref` left in a nested `$def` is a
        400 the top-level shape would not reveal."""
        rendered = json.dumps(gemini_schema(beat_json_schema()))
        for forbidden in ("$ref", "$defs", "additionalProperties", "$schema", "allOf", "oneOf"):
            assert forbidden not in rendered, f"{forbidden} reached the wire schema"

    def test_the_required_but_nullable_fields_keep_both_halves(self) -> None:
        """§8.1's shape: required so an omission is caught, nullable so "no
        question this beat" is expressible. `type: "null"` is not a Gemini type,
        so this is where a careless translation drops one half."""
        interaction = gemini_schema(beat_json_schema())["properties"]["interaction"]

        assert set(interaction["required"]) == {"type", "prompt_audio", "expected", "choices"}
        assert interaction["properties"]["prompt_audio"] == {
            "type": "string",
            "nullable": True,
            "description": interaction["properties"]["prompt_audio"]["description"],
        }
        # A genuine union keeps its branches and gains nullability.
        expected = interaction["properties"]["expected"]
        assert expected["nullable"] is True
        assert expected["anyOf"] == [{"type": "integer"}, {"type": "string"}]

    def test_the_enum_survives_inlining(self) -> None:
        """`InteractionType` is a `$def` referenced once. Inlining it wrongly would
        leave the client's dispatch unconstrained at generation time."""
        interaction = gemini_schema(beat_json_schema())["properties"]["interaction"]
        assert interaction["properties"]["type"]["enum"] == [
            "count",
            "choose",
            "say",
            "tap",
            "none",
        ]

    def test_it_changes_the_wire_schema_and_not_the_contract(self) -> None:
        """The property the review presses on: `Beat` still validates exactly what
        it did. A sanitizer that "fixed" the schema by loosening the model would
        have moved the problem into the part nobody re-reads."""
        from pydantic import ValidationError

        assert gemini_schema(beat_json_schema()) != beat_json_schema()
        assert Beat.model_validate(VALID_BEAT).beat_id == "b1"
        with pytest.raises(ValidationError):
            Beat.model_validate({**VALID_BEAT, "surprise": "extra"})

    def test_a_recursive_contract_is_refused_rather_than_inlined_forever(self) -> None:
        """There is no such contract today, and the failure mode if one arrives is
        a hung process rather than an error, so it is worth a named exception."""
        recursive = {
            "type": "object",
            "$defs": {
                "Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}
            },
            "properties": {"root": {"$ref": "#/$defs/Node"}},
        }
        with pytest.raises(GeminiSchemaError, match="refers to itself"):
            gemini_schema(recursive)

    @pytest.mark.parametrize("keyword", ["oneOf", "allOf"])
    def test_a_union_gemini_cannot_express_is_refused_rather_than_dropped(
        self, keyword: str
    ) -> None:
        """The Vertex review's P2-1, and the one failure in this file that is about
        what does *not* happen.

        `allOf`/`oneOf` used to sit in `_GEMINI_UNSUPPORTED_KEYWORDS` and be
        dropped like `title` and `additionalProperties`. Dropping those two is
        free — the strictness they buy is applied when the response is validated.
        Dropping a union is not: the field reaches Gemini as a description with no
        `type` at all, the request succeeds, and the model fills the node in by
        guesswork. That is a silent contract loss on a request that never errors,
        which is the exact thing `GeminiSchemaError` exists to prevent and the one
        shape a 400 would never reveal.

        Neither appears in any contract we generate today (the parametrized test
        above covers all four). This is here for the day a `Literal` union or a
        constrained subclass arrives and somebody has to be told.
        """
        toy = {
            "type": "object",
            "properties": {
                "expected": {
                    "description": "what the child should answer",
                    keyword: [{"type": "integer"}, {"type": "string"}],
                }
            },
        }
        with pytest.raises(GeminiSchemaError, match=keyword):
            gemini_schema(toy)

    def test_a_dangling_reference_is_refused_too(self) -> None:
        with pytest.raises(GeminiSchemaError, match="does not resolve"):
            gemini_schema({"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}})

    def test_a_format_gemini_does_not_know_is_dropped(self) -> None:
        """Pydantic emits `uuid`, `email` and `date` from ordinary field types and
        Gemini 400s on all three."""
        converted = gemini_schema(
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "when": {"type": "string", "format": "date-time"},
                },
            }
        )
        assert converted["properties"]["id"] == {"type": "string"}
        assert converted["properties"]["when"]["format"] == "date-time"


# ---------------------------------------------------------------------------
# Configuration and selection
# ---------------------------------------------------------------------------
class TestWhenItConsidersItselfConfigured:
    def test_all_three_settings_are_needed(self, settings: Any) -> None:
        settings.GOOGLE_CLOUD_PROJECT = "primer-test"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = ""

        assert GoogleProvider(model="gemini-2.5-flash").is_configured() is False

        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"
        assert GoogleProvider(model="gemini-2.5-flash").is_configured() is True

    def test_a_half_set_deployment_falls_through_rather_than_raising(self, settings: Any) -> None:
        """`select_provider` reads `is_configured`, so this is the difference
        between "the next provider answers" and an exception inside a beat a child
        is waiting for."""
        from apps.story.ai.router import ModelRouter

        settings.GOOGLE_CLOUD_PROJECT = ""
        settings.AI_PROVIDER_ORDER = ["google", "anthropic"]
        settings.ANTHROPIC_API_KEY = "test-key"

        assert ModelRouter.from_settings().select_provider().name == "anthropic"

    def test_it_is_registered_under_the_name_the_config_uses(self) -> None:
        """`AI_PROVIDER_ORDER=gemini` was in three documents and is not a provider.
        The registered name is `google`."""
        from apps.story.ai.router import PROVIDERS

        assert PROVIDERS["google"] is GoogleProvider
        assert "gemini" not in PROVIDERS


# ---------------------------------------------------------------------------
# The request it builds
# ---------------------------------------------------------------------------
class TestWhatItAsksGeminiFor:
    def test_it_sends_the_translated_schema_and_asks_for_json(self, vertex: Any) -> None:
        client = vertex(_response(json.dumps(VALID_BEAT)))
        GoogleProvider(model="gemini-2.5-flash").generate_json(
            system="be warm",
            messages=[{"role": "user", "content": "a beat"}],
            schema=beat_json_schema(),
        )

        config = client.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.system_instruction == "be warm"
        assert config.max_output_tokens == 8000
        # The translated schema, not the one every other provider gets — and it
        # is validated here rather than merely compared, because what matters is
        # that the SDK will accept it. (The SDK's own conversion happens inside
        # `generate_content`, which this test has replaced.)
        assert config.response_schema == gemini_schema(beat_json_schema())
        types.Schema.model_validate(config.response_schema)

    def test_geminis_own_filters_are_off_so_our_grader_stays_the_authority(
        self, vertex: Any
    ) -> None:
        """§13.3. The vendor filter is tuned for a general audience and has no idea
        the wolf in a bedtime story is the point; when it fires we get a blocked
        response instead of a graded refusal, which is a silent availability
        failure on the surface where the child is waiting."""
        client = vertex(_response(json.dumps(VALID_BEAT)))
        GoogleProvider(model="gemini-2.5-flash").generate_json(
            system="s", messages=[{"role": "user", "content": "u"}], schema=beat_json_schema()
        )

        settings_sent = client.calls[0]["config"].safety_settings
        assert {str(s.threshold.value) for s in settings_sent} == {"OFF"}
        assert {str(s.category.value) for s in settings_sent} == {
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        }

    def test_the_repair_turn_is_sent_as_the_model_and_not_as_the_child(self, vertex: Any) -> None:
        """`generate_structured` replays a bad generation as an `assistant` turn.
        Gemini spells that role `model`, and sending it as `user` would ask the
        model to fix something it reads as the child's own words (§19.1)."""
        client = vertex(_response(json.dumps(VALID_BEAT)))
        GoogleProvider(model="gemini-2.5-flash").generate_json(
            system="s",
            messages=[
                {"role": "user", "content": "write one"},
                {"role": "assistant", "content": "{bad}"},
                {"role": "user", "content": "fix it"},
            ],
            schema=beat_json_schema(),
        )

        assert [c.role for c in client.calls[0]["contents"]] == ["user", "model", "user"]


# ---------------------------------------------------------------------------
# The refusals (acceptance criteria 3 and 4)
# ---------------------------------------------------------------------------
class TestEveryWayGeminiDeclinesIsAnAIError:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (_response(block="SAFETY", candidates=False), "blocked the request"),
            (_response(candidates=False), "no candidates"),
            (_response(finish="SAFETY"), "finish_reason: SAFETY"),
            (_response(finish="PROHIBITED_CONTENT"), "finish_reason: PROHIBITED_CONTENT"),
            (_response('{"partial"', finish="MAX_TOKENS"), "finish_reason: MAX_TOKENS"),
            (_response(""), "no text content"),
        ],
        ids=["prompt-blocked", "no-candidates", "safety", "prohibited", "truncated", "empty"],
    )
    def test_it_becomes_a_provider_error(self, vertex: Any, response: Any, expected: str) -> None:
        vertex(response)

        with pytest.raises(ProviderError) as caught:
            GoogleProvider(model="gemini-2.5-flash").generate_json(
                system="s", messages=[], schema=beat_json_schema()
            )

        assert expected in str(caught.value)
        assert isinstance(caught.value, AIError), "the whole point: handlers key on AIError"

    def test_an_sdk_error_leaves_the_adapter_as_an_aierror(self, vertex: Any) -> None:
        """The Day-7 P0-1 invariant, for the third provider. A
        `google.genai.errors.ClientError` is not an `AIError`, so a 404 on a
        mistyped model id would blow through the fallback handler, the degradation
        handler and the stream's `done` frame all at once."""
        from google.genai import errors

        vertex(errors.ClientError(404, {"error": {"message": "model not found"}}))

        with pytest.raises(ProviderError) as caught:
            GoogleProvider(model="gemini-2.5-flash").generate_json(
                system="s", messages=[], schema=beat_json_schema()
            )

        assert isinstance(caught.value, AIError)
        assert isinstance(caught.value.__cause__, errors.ClientError)
        assert "ClientError" in str(caught.value)

    def test_a_schema_gemini_cannot_express_is_an_aierror_too(self, vertex: Any) -> None:
        """`GeminiSchemaError` is a `ValueError`, and a `ValueError` reaching the
        stream is the same dead screen a timeout used to be."""
        vertex(_response(json.dumps(VALID_BEAT)))

        with pytest.raises(ProviderError):
            GoogleProvider(model="gemini-2.5-flash").generate_json(
                system="s",
                messages=[],
                schema={"type": "object", "properties": {"a": {"$ref": "#/$defs/Gone"}}},
            )


class TestTheMeter:
    def test_usage_is_read_from_gemini_and_thinking_is_priced(self, vertex: Any) -> None:
        """Gemini reports thinking tokens *outside* `candidates_token_count` and
        bills them at the output rate. Reporting only the candidates would
        under-report the price of exactly the generations that cost most (§22.4)."""
        vertex(_response(json.dumps(VALID_BEAT), usage=(986, 1136, 220)))

        provider = GoogleProvider(model="gemini-2.5-flash")
        provider.generate_json(system="s", messages=[], schema=beat_json_schema())

        assert provider.last_usage == {
            "input_tokens": 986,
            "output_tokens": 1356,
            "thoughts_tokens": 220,
        }

    def test_thinking_is_reported_on_its_own_as_well_as_folded_in(self, vertex: Any) -> None:
        """The Vertex review's P2-4, instrumented rather than guessed at.

        Folding thinking into the output side is right for the *price* and useless
        for the decision the first live sitting has to make: whether to set a
        `thinking_config` cap. The build measured 1,136 against 1,572 output tokens
        for the same prompt — a 38% spread that is entirely thinking — and that
        spread is invisible in a number with thinking already added into it.

        So it is carried twice, and the second copy must cost nothing: `cost.price`
        reads `input_tokens` and `output_tokens` only, which is what this asserts.
        A key that changed the arithmetic would be a cost regression dressed as
        instrumentation.
        """
        from apps.analytics import cost
        from apps.analytics.models import CostKind

        vertex(_response(json.dumps(VALID_BEAT), usage=(986, 1136, 220)))
        provider = GoogleProvider(model="gemini-2.5-flash")
        provider.generate_json(system="s", messages=[], schema=beat_json_schema())

        assert provider.last_usage is not None
        assert provider.last_usage["thoughts_tokens"] == 220
        label = "google:gemini-2.5-flash"
        assert cost.price(CostKind.LLM, label, provider.last_usage) == cost.price(
            CostKind.LLM, label, {"input_tokens": 986, "output_tokens": 1356}
        )

    def test_a_vendor_that_reports_no_thinking_count_is_not_given_a_zero(self) -> None:
        """A reported zero and an unreported count are different facts, and the
        one that would mislead is inventing the first. Exercised through
        `_gemini_usage` directly, because it is the shape of the *response* that
        is under test."""
        from apps.story.ai.router import _gemini_usage

        metadata = type(
            "U",
            (),
            {"prompt_token_count": 40, "candidates_token_count": 12, "thoughts_token_count": None},
        )()
        usage = _gemini_usage(type("R", (), {"usage_metadata": metadata})())

        assert usage == {"input_tokens": 40, "output_tokens": 12}

    def test_a_metered_generation_that_is_then_refused_is_still_costed(self, vertex: Any) -> None:
        """Day-14 P1-5, mirrored. A blocked beat cost input tokens; the meter is
        read *before* the checks that raise, so `_generate_traced` can still write
        the span."""
        vertex(_response(finish="SAFETY", usage=(900, 0, 0)))

        provider = GoogleProvider(model="gemini-2.5-flash")
        with pytest.raises(ProviderError):
            provider.generate_json(system="s", messages=[], schema=beat_json_schema())

        assert provider.last_usage == {
            "input_tokens": 900,
            "output_tokens": 0,
            "thoughts_tokens": 0,
        }

    def test_a_call_that_never_reached_the_model_reports_no_usage(self, vertex: Any) -> None:
        """The other half: `_generate_traced` keys on `last_usage is not None` to
        tell "the model answered and we did not like it" from "we never got
        there", and inventing a price for the second is the same dishonesty as
        losing the first."""
        vertex(RuntimeError("connection refused"))

        provider = GoogleProvider(model="gemini-2.5-flash")
        with pytest.raises(ProviderError):
            provider.generate_json(system="s", messages=[], schema=beat_json_schema())

        assert provider.last_usage is None

    def test_the_route_is_priced_so_cost_per_session_is_a_real_number(self) -> None:
        from apps.analytics import pricing

        assert (
            pricing.llm_cost("google:gemini-2.5-flash", input_tokens=1_000_000, output_tokens=0)
            is not None
        )
        assert (
            pricing.llm_cost(
                "google:gemini-2.5-flash-lite", input_tokens=0, output_tokens=1_000_000
            )
            is not None
        )


# ---------------------------------------------------------------------------
# What a child gets (acceptance criterion 3, end to end)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAGeminiBlockReachesTheChildAsAStory:
    def test_a_safety_blocked_generation_becomes_a_warm_fallback_beat(
        self, child: Any, vertex: Any, monkeypatch: Any, settings: Any
    ) -> None:
        """The end the review asks to see: not that a `ProviderError` was raised,
        but that a four-year-old whose beat Gemini refused still gets a beat.

        The real `GoogleProvider` is the only provider in the router, and Gemini
        answers every call with a SAFETY block — the plan call included, which is
        why the adventure still opens.
        """
        from apps.companion.models import Companion
        from apps.safety import policy
        from apps.safety.fallbacks import FALLBACK_EVIDENCE_KEY
        from apps.story import orchestrator
        from apps.story.ai.router import ModelRouter
        from apps.story.models import Beat as BeatRow

        settings.AI_PROVIDER_ORDER = ["google"]
        vertex(*[_response(finish="SAFETY") for _ in range(12)])
        monkeypatch.setattr(
            ModelRouter,
            "from_settings",
            classmethod(
                lambda cls, **_: ModelRouter(providers=[GoogleProvider(model="gemini-2.5-flash")])
            ),
        )

        Companion.objects.create(child=child, archetype="dragon", name="Pip")
        adventure = orchestrator.create_adventure(child)
        beat = orchestrator.create_beat(adventure, seq=1)

        # A story, not an error. The same standard `test_degradation.py` holds
        # every other degraded mode to.
        assert beat.narration.strip(), "a beat with no words is a blank screen"
        assert (beat.scene or {}).get("illustration_prompt")
        assert policy.check_text(beat.narration, child) == []
        for word in ("error", "sorry", "unavailable", "try again", "problem", "failed"):
            assert word not in beat.narration.lower()

        # And it is honestly labelled as a fallback rather than passed off as
        # generated: the incident trail and the learning tag both say so.
        assert beat.safety_status == BeatRow.SafetyStatus.BLOCKED
        assert beat.model.startswith("fallback:")
        assert (beat.learning or {}).get("skill_evidence_key") == FALLBACK_EVIDENCE_KEY
