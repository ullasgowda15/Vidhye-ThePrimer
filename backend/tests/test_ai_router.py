"""The AI layer's contract: a Beat is validated, and a bad one gets exactly one
repair attempt before the router gives up (PRODUCT.md §19.6).

No network here — a fake provider stands in for Claude so the guarantees are
testable in CI without a key.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from apps.story.ai.router import (
    AIConfigurationError,
    BeatValidationError,
    ModelRouter,
)
from apps.story.ai.schemas import Beat, beat_json_schema

VALID_BEAT: dict[str, Any] = {
    "beat_id": "b1",
    "narration": "Pip peeks over the log. 'Three little frogs! Can you count them with me?'",
    "scene": {
        "setting": "mossy riverbank, dawn light, three green frogs on a log",
        "illustration_prompt": "storybook watercolor, mossy riverbank at dawn, three frogs",
        "characters_present": ["pip", "frogs"],
        # Pip's appearance is held; the frogs are met here for the first time, so
        # this beat is what writes down what they look like (§8.2).
        "new_characters": [
            {
                "key": "frogs",
                "name": "the frog chorus",
                "visual_descriptor": (
                    "three small round frogs, moss-green with paler bellies and "
                    "large friendly amber eyes; one has a single cream stripe down "
                    "its back"
                ),
            }
        ],
    },
    "interaction": {
        "type": "count",
        "prompt_audio": "How many frogs do you see?",
        "expected": 3,
        "choices": None,
    },
    "learning": {
        "objective_id": "count_to_5",
        "difficulty": 0.4,
        "skill_evidence_key": "count_frogs",
    },
    "emotion": "playful_curious",
    "continuity_refs": ["companion:pip", "world:whispering_woods"],
    "next_hint": "if child succeeds -> cross river; if struggles -> Pip counts along",
}

CONTEXT = {"system": "be warm", "user": "one beat about frogs"}


class FakeProvider:
    """Replays a scripted list of raw responses and records what it was asked."""

    name = "fake"

    def __init__(self, responses: list[str], configured: bool = True) -> None:
        self._responses = list(responses)
        self._configured = configured
        self.calls: list[list[dict[str, Any]]] = []

    def is_configured(self) -> bool:
        return self._configured

    def generate_json(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> str:
        self.calls.append(messages)
        return self._responses.pop(0)


def test_beat_schema_matches_the_product_contract() -> None:
    beat = Beat.model_validate(VALID_BEAT)
    assert beat.beat_id == "b1"
    assert beat.scene.characters_present == ["pip", "frogs"]
    assert beat.learning.objective_id == "count_to_5"
    assert beat.interaction.expected == 3


def test_beat_schema_rejects_unknown_fields() -> None:
    # Strictness is the point: an unexpected key means the prompt and the
    # consumers have drifted apart, and we want to know now.
    with pytest.raises(ValidationError):
        Beat.model_validate({**VALID_BEAT, "surprise": "extra"})


def test_json_schema_is_strict_for_structured_output() -> None:
    schema = beat_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"beat_id", "narration", "scene", "interaction", "learning"}


def test_router_returns_validated_beat_on_first_try() -> None:
    provider = FakeProvider([json.dumps(VALID_BEAT)])
    beat = ModelRouter(providers=[provider]).generate_beat(CONTEXT)

    assert beat.beat_id == "b1"
    assert len(provider.calls) == 1


def test_router_repairs_once_then_succeeds() -> None:
    provider = FakeProvider(['{"beat_id": "b1"}', json.dumps(VALID_BEAT)])
    beat = ModelRouter(providers=[provider]).generate_beat(CONTEXT)

    assert beat.beat_id == "b1"
    assert len(provider.calls) == 2
    # The repair turn must show the model its own bad output.
    assert provider.calls[1][1]["role"] == "assistant"


def test_router_raises_after_a_single_failed_repair() -> None:
    provider = FakeProvider(['{"beat_id": "b1"}', "not json at all"])

    with pytest.raises(BeatValidationError):
        ModelRouter(providers=[provider]).generate_beat(CONTEXT)

    assert len(provider.calls) == 2  # one repair, no more


def test_router_skips_unconfigured_providers() -> None:
    unconfigured = FakeProvider([], configured=False)
    configured = FakeProvider([json.dumps(VALID_BEAT)])

    beat = ModelRouter(providers=[unconfigured, configured]).generate_beat(CONTEXT)

    assert beat.beat_id == "b1"
    assert unconfigured.calls == []


def test_router_fails_clearly_when_nothing_is_configured() -> None:
    router = ModelRouter(providers=[FakeProvider([], configured=False)])

    with pytest.raises(AIConfigurationError, match="ANTHROPIC_API_KEY"):
        router.generate_beat(CONTEXT)


def test_a_generated_row_can_say_who_wrote_it() -> None:
    """`provider:model`, which is what a beat records (`Beat.model`).

    Both names, because neither is enough on its own: the same model id is
    reachable through more than one provider, and a provider name says nothing
    about which model behind it answered.
    """
    from apps.story.ai.router import AnthropicProvider, model_label

    assert model_label(AnthropicProvider(api_key="k", model="claude-opus-5")) == (
        "anthropic:claude-opus-5"
    )
    # A provider that names no model reduces to itself rather than to a lie about
    # some default.
    assert model_label(FakeProvider([])) == "fake"


def test_router_rejects_an_empty_context() -> None:
    router = ModelRouter(providers=[FakeProvider([json.dumps(VALID_BEAT)])])

    with pytest.raises(AIConfigurationError):
        router.generate_beat({"system": "", "user": ""})


# ---------------------------------------------------------------------------
# The adapter boundary (Day 7 fix, P0-1)
# ---------------------------------------------------------------------------
# The invariant every layer above this one depends on: **the AI layer raises
# `AIError` and nothing else.** `create_beat` catches `AIError` to serve a warm
# fallback, `classifier.classify` catches it to degrade rather than pass, and
# `stream_beat` catches it to end the stream cleanly. Before the fix an SDK
# timeout satisfied none of those handlers and blew through all three at once,
# leaving a four-year-old on the thinking companion indefinitely.
def _timeout() -> Exception:
    import httpx
    import openai

    return openai.APITimeoutError(request=httpx.Request("POST", "https://openrouter.test"))


def test_an_sdk_timeout_leaves_the_anthropic_adapter_as_an_aierror(monkeypatch) -> None:
    import anthropic

    from apps.story.ai.router import AIError, AnthropicProvider, ProviderError

    class Timeout:
        def __init__(self, **_: Any) -> None:
            self.messages = self

        def create(self, **_: Any) -> Any:
            raise _timeout()

    monkeypatch.setattr(anthropic, "Anthropic", Timeout)

    with pytest.raises(ProviderError) as caught:
        AnthropicProvider(api_key="k", model="claude-opus-5").generate_json(
            system="be warm", messages=[], schema={}
        )

    assert isinstance(caught.value, AIError), "the whole point: handlers key on AIError"
    # The original is kept rather than swallowed — "we were rate-limited" and
    # "the model id is wrong" call for different actions.
    assert isinstance(caught.value.__cause__, type(_timeout()))
    assert "APITimeoutError" in str(caught.value)


def test_an_sdk_timeout_leaves_the_openrouter_adapter_as_an_aierror(monkeypatch, settings) -> None:
    import openai

    from apps.story.ai.router import AIError, OpenRouterProvider, ProviderError

    class Timeout:
        def __init__(self, **_: Any) -> None:
            self.chat = self
            self.completions = self

        def create(self, **_: Any) -> Any:
            raise _timeout()

    monkeypatch.setattr(openai, "OpenAI", Timeout)
    settings.AI_STRUCTURED_OUTPUT_MODE = "auto"

    with pytest.raises(ProviderError) as caught:
        OpenRouterProvider(api_key="k", model="vendor/model").generate_json(
            system="be warm", messages=[], schema={}
        )

    assert isinstance(caught.value, AIError)
    assert isinstance(caught.value.__cause__, type(_timeout()))


def test_an_unimplemented_seam_raises_an_aierror_too() -> None:
    """A seam is not an exception to the contract.

    `NotImplementedError` is not an `AIError`, so a caller holding this provider
    directly would have produced the same dead stream as a timeout did.
    """
    from apps.story.ai.router import AIError, OpenAIProvider

    with pytest.raises(AIError):
        OpenAIProvider().generate_json(system="s", messages=[], schema={})
