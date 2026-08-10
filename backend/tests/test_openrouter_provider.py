"""OpenRouter as an env-selected provider (PRODUCT.md §19.3, §19.6).

OpenRouter is OpenAI-API-compatible, so it is an OpenAI-style adapter rather than a
second router. What matters here, and what these tests pin:

* the router picks it from `AI_PROVIDER_ORDER` alone — reverting to Anthropic is a
  one-line `.env` change, never a code change;
* it asks for a strict `json_schema` first and degrades to JSON mode when the model
  doesn't support one, instead of failing the beat.

The OpenAI client is replaced wholesale, so nothing here touches the network.
"""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from apps.story.ai.router import (
    AIConfigurationError,
    AnthropicProvider,
    ModelRouter,
    OpenRouterProvider,
    ProviderError,
)
from tests.test_ai_router import CONTEXT, VALID_BEAT

SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def bad_request(message: str = "response_format json_schema is not supported") -> Exception:
    """A real `openai.BadRequestError`, as a model without strict-schema support sends."""
    return openai.BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://openrouter.test")),
        body=None,
    )


def completion(content: str | None, refusal: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class FakeOpenAI:
    """Stands in for `openai.OpenAI`, replaying scripted outcomes and recording calls."""

    calls: list[dict[str, Any]] = []
    init_kwargs: dict[str, Any] = {}
    outcomes: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> Any:
        type(self).calls.append(kwargs)
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_openai(monkeypatch) -> type[FakeOpenAI]:
    FakeOpenAI.calls = []
    FakeOpenAI.init_kwargs = {}
    FakeOpenAI.outcomes = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return FakeOpenAI


@pytest.fixture
def openrouter_settings(settings) -> None:
    settings.OPENROUTER_API_KEY = "test-openrouter-key"
    settings.AI_BEAT_MODEL = "vendor/some-free-model:free"
    settings.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    settings.OPENROUTER_SITE_URL = "http://localhost:5173"
    # Pinned so these tests describe the shipping default rather than whatever
    # happens to be in the developer's .env.
    settings.AI_STRUCTURED_OUTPUT_MODE = "auto"


pytestmark = pytest.mark.usefixtures("openrouter_settings")


def test_points_the_openai_sdk_at_openrouter(fake_openai) -> None:
    fake_openai.outcomes = [completion(json.dumps(VALID_BEAT))]

    OpenRouterProvider().generate_json(system="be warm", messages=[], schema=SCHEMA)

    assert fake_openai.init_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert fake_openai.init_kwargs["api_key"] == "test-openrouter-key"
    # Attribution headers are optional for OpenRouter but polite, and carry no child data.
    assert fake_openai.init_kwargs["default_headers"]["X-Title"] == "The Primer"
    assert fake_openai.init_kwargs["default_headers"]["HTTP-Referer"] == "http://localhost:5173"


def test_asks_for_a_strict_schema_first(fake_openai) -> None:
    fake_openai.outcomes = [completion(json.dumps(VALID_BEAT))]

    raw = OpenRouterProvider().generate_json(
        system="be warm", messages=[{"role": "user", "content": "one beat"}], schema=SCHEMA
    )

    assert json.loads(raw)["beat_id"] == "b1"
    (call,) = fake_openai.calls
    assert call["model"] == "vendor/some-free-model:free"
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["schema"] == SCHEMA
    assert call["messages"][0] == {"role": "system", "content": "be warm"}


def test_degrades_to_json_mode_when_the_model_rejects_a_strict_schema(fake_openai) -> None:
    # The common case on free models: no strict schema support. The beat must still
    # be produced — JSON mode plus the router's validate-and-repair covers it.
    fake_openai.outcomes = [bad_request(), completion(json.dumps(VALID_BEAT))]

    raw = OpenRouterProvider().generate_json(
        system="be warm", messages=[{"role": "user", "content": "one beat"}], schema=SCHEMA
    )

    assert json.loads(raw)["beat_id"] == "b1"
    strict_call, fallback_call = fake_openai.calls
    assert strict_call["response_format"]["type"] == "json_schema"
    assert fallback_call["response_format"] == {"type": "json_object"}
    # JSON mode constrains syntax only, so the schema has to travel in the prompt.
    assert json.dumps(SCHEMA) in fallback_call["messages"][0]["content"]
    assert "be warm" in fallback_call["messages"][0]["content"]
    assert fallback_call["messages"][1] == {"role": "user", "content": "one beat"}


def test_remembers_the_downgrade_for_the_repair_attempt(fake_openai) -> None:
    # The router's repair attempt reuses the same provider instance. Re-learning the
    # rejection would burn a second round trip on every repaired beat.
    provider = OpenRouterProvider()
    fake_openai.outcomes = [bad_request(), completion("{}"), completion(json.dumps(VALID_BEAT))]

    provider.generate_json(system="be warm", messages=[], schema=SCHEMA)
    provider.generate_json(system="be warm", messages=[], schema=SCHEMA)

    assert len(fake_openai.calls) == 3  # one rejection, then two JSON-mode calls
    assert fake_openai.calls[2]["response_format"] == {"type": "json_object"}


def test_auto_is_the_default_policy(settings) -> None:
    # The shipping default must be the self-healing one. If this flips, every
    # deployment that never set the knob changes behaviour silently.
    assert settings.AI_STRUCTURED_OUTPUT_MODE == "auto"
    assert OpenRouterProvider().output_mode == "json_schema"


def test_json_object_mode_skips_the_strict_attempt(settings, fake_openai) -> None:
    # Straight to the fallback: no wasted round trip on a model already known not
    # to support strict schemas. This is the switch `smoke_ai` Check B flips.
    settings.AI_STRUCTURED_OUTPUT_MODE = "json_object"
    fake_openai.outcomes = [completion(json.dumps(VALID_BEAT))]

    provider = OpenRouterProvider()
    provider.generate_json(system="be warm", messages=[], schema=SCHEMA)

    (call,) = fake_openai.calls
    assert call["response_format"] == {"type": "json_object"}
    assert json.dumps(SCHEMA) in call["messages"][0]["content"]
    assert provider.output_mode == "json_object"


def test_strict_mode_refuses_to_degrade(settings, fake_openai) -> None:
    # An operator who asked for enforced structure gets an error, not a quietly
    # weaker guarantee — that is the whole point of choosing `strict` over `auto`.
    settings.AI_STRUCTURED_OUTPUT_MODE = "strict"
    fake_openai.outcomes = [bad_request()]

    provider = OpenRouterProvider()
    # A `ProviderError`, not the raw `openai.BadRequestError` it wraps: since the
    # Day-7 fix this adapter is a boundary, and everything above it — the fallback
    # beat, the classifier's degradation, the SSE handler — keys on `AIError`. The
    # original is still reachable as `__cause__`, so nothing is lost but the leak.
    with pytest.raises(ProviderError) as caught:
        provider.generate_json(system="be warm", messages=[], schema=SCHEMA)

    assert isinstance(caught.value.__cause__, openai.BadRequestError)
    assert len(fake_openai.calls) == 1  # no fallback attempt
    assert provider.output_mode == "json_schema"


def test_auto_mode_degrades_where_strict_would_raise(settings, fake_openai) -> None:
    settings.AI_STRUCTURED_OUTPUT_MODE = "auto"
    fake_openai.outcomes = [bad_request(), completion(json.dumps(VALID_BEAT))]

    provider = OpenRouterProvider()
    provider.generate_json(system="be warm", messages=[], schema=SCHEMA)

    assert provider.output_mode == "json_object"


def test_an_unknown_mode_fails_at_construction(settings) -> None:
    # Fail on a typo when the provider is built, not on the first beat a child is
    # waiting for.
    settings.AI_STRUCTURED_OUTPUT_MODE = "jsonobject"

    with pytest.raises(AIConfigurationError, match="AI_STRUCTURED_OUTPUT_MODE"):
        OpenRouterProvider()


def test_reports_a_refusal_as_a_provider_error(fake_openai) -> None:
    fake_openai.outcomes = [completion(None, refusal="I can't help with that.")]

    with pytest.raises(ProviderError, match="declined"):
        OpenRouterProvider().generate_json(system="be warm", messages=[], schema=SCHEMA)


def test_reports_empty_content_with_the_finish_reason(fake_openai) -> None:
    # A truncated generation arrives as empty content, not an error — say so plainly.
    fake_openai.outcomes = [completion("")]

    with pytest.raises(ProviderError, match="no content"):
        OpenRouterProvider().generate_json(system="be warm", messages=[], schema=SCHEMA)


def test_unconfigured_without_a_key(settings) -> None:
    settings.OPENROUTER_API_KEY = ""

    assert OpenRouterProvider().is_configured() is False


def test_env_alone_switches_the_router_to_openrouter(settings, fake_openai) -> None:
    settings.ANTHROPIC_API_KEY = ""
    settings.AI_PROVIDER_ORDER = ["openrouter", "anthropic"]
    fake_openai.outcomes = [completion(json.dumps(VALID_BEAT))]

    router = ModelRouter.from_settings()
    beat = router.generate_beat(CONTEXT)

    assert [p.name for p in router.providers] == ["openrouter", "anthropic"]
    assert isinstance(router.select_provider(), OpenRouterProvider)
    assert beat.beat_id == "b1"


def test_env_alone_switches_the_router_back_to_anthropic(settings) -> None:
    # Criterion 3 of the fix brief: reverting is a .env change, not a code change.
    settings.ANTHROPIC_API_KEY = "test-anthropic-key"
    settings.AI_PROVIDER_ORDER = ["anthropic"]

    router = ModelRouter.from_settings()

    assert [p.name for p in router.providers] == ["anthropic"]
    assert isinstance(router.select_provider(), AnthropicProvider)


def test_openrouter_is_skipped_when_it_has_no_key(settings) -> None:
    settings.OPENROUTER_API_KEY = ""
    settings.ANTHROPIC_API_KEY = "test-anthropic-key"
    settings.AI_PROVIDER_ORDER = ["openrouter", "anthropic"]

    assert isinstance(ModelRouter.from_settings().select_provider(), AnthropicProvider)
