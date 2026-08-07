"""Model router (PRODUCT.md §19.3, §19.6).

Why a router at all: portability (no provider lock-in), cost control (the right
model per call-type), and resilience (a provider outage must not be a product
outage). Claude is the configured primary; OpenRouter is a real, selectable
provider so the pipeline can be exercised on a free model; OpenAI and Google are
declared as typed seams so Day 15's fallback work is wiring, not redesign.

Which provider runs is decided entirely by `AI_PROVIDER_ORDER` in the environment
— no code changes to switch.

Every provider call is wrapped by validate-and-repair. A generation that fails
the `Beat` schema gets exactly **one** repair attempt and then raises. The
bounded blast radius matters: the caller above us (Day 7) turns a raise into a
safe fallback beat, so the child sees a slightly generic story rather than an
error — but only if we fail fast instead of retrying forever.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast, runtime_checkable

from django.conf import settings
from pydantic import BaseModel, ValidationError

from apps.story.ai.schemas import Beat, beat_json_schema, strip_unsupported_keywords

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam

logger = logging.getLogger(__name__)

#: Any typed contract the router can enforce. `Beat` is the one that matters;
#: the companion's canonical visual descriptor (§8.2) is the second.
ModelT = TypeVar("ModelT", bound=BaseModel)

# How many tokens a single beat may cost. A beat is a few hundred words; the
# headroom is for adaptive thinking, which is on by default on Claude Opus 5.
MAX_OUTPUT_TOKENS = 8000


class StructuredOutputMode(StrEnum):
    """How hard to insist on provider-enforced structured output (§19.6).

    Applies to OpenAI-compatible providers, where support varies by model.
    Anthropic enforces the schema natively and ignores this.

    `AUTO` is the shipping default and the only self-healing option. The other two
    exist because "it degraded silently" and "it failed loudly" are both things you
    sometimes need to be sure of: `STRICT` in production, where a model quietly
    dropping to unenforced JSON is a regression worth a page rather than a shrug;
    `JSON_OBJECT` to exercise the repair path deliberately.
    """

    AUTO = "auto"
    STRICT = "strict"
    JSON_OBJECT = "json_object"


class AIError(Exception):
    """Base class for AI-layer failures."""


class AIConfigurationError(AIError):
    """The AI layer is not usable — missing key, no configured provider."""


class ProviderError(AIError):
    """A provider was reached but could not produce a usable response."""


class StructuredOutputError(AIError):
    """A response came back but never satisfied the requested schema."""


class BeatValidationError(StructuredOutputError):
    """A response came back but never satisfied the Beat schema."""


@runtime_checkable
class StructuredProvider(Protocol):
    """One LLM provider, reduced to the only thing the router needs from it."""

    name: str

    def is_configured(self) -> bool:
        """True when this provider has the credentials/config to be called."""
        ...

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        """Return the model's response as a raw JSON string."""
        ...


class AnthropicProvider:
    """Claude — the primary provider for beat generation (PRODUCT.md §16.1)."""

    name = "anthropic"
    credential_env_var = "ANTHROPIC_API_KEY"

    #: Claude enforces the schema natively — there is no degraded mode to report.
    output_mode = "json_schema"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._model = model or settings.AI_BEAT_MODEL

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        import anthropic

        # Structured output: the provider constrains generation to the Beat
        # schema, so we validate a well-formed candidate rather than parsing
        # prose (§19.6). `effort` keeps a single beat cheap — this is a short,
        # well-specified generation, not a reasoning problem.
        output_config: OutputConfigParam = {
            "format": {"type": "json_schema", "schema": schema},
            "effort": "medium",
        }

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=cast("list[MessageParam]", messages),
            output_config=output_config,
        )

        # A safety classifier can decline before any content is produced. That is
        # a 200 with an empty `content`, so check the stop reason first.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) or "unspecified"
            raise ProviderError(f"Claude declined the request (category: {category}).")

        for block in response.content:
            if block.type == "text":
                return block.text

        raise ProviderError("Claude returned no text content.")


class OpenRouterProvider:
    """OpenRouter — an OpenAI-API-compatible gateway in front of many models.

    Why it exists on Day 1: it lets the whole AI path be exercised end to end on a
    free model, without an Anthropic key. Anthropic stays the configured primary;
    this is selected purely by `AI_PROVIDER_ORDER` in `.env`, so switching back is
    a config change, not a code change.

    The model is never hardcoded — free-model availability on OpenRouter changes
    week to week, so it comes from `AI_BEAT_MODEL`.
    """

    name = "openrouter"
    credential_env_var = "OPENROUTER_API_KEY"

    #: Sent to OpenRouter for attribution. Neither is required, and neither
    #: identifies a child — this is the app's own name.
    _ATTRIBUTION_TITLE = "The Primer"

    # The two wire-level response formats, as OpenAI-compatible APIs name them.
    _STRICT = "json_schema"
    _JSON_MODE = "json_object"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        mode: str = "",
    ) -> None:
        self._api_key = api_key or settings.OPENROUTER_API_KEY
        self._model = model or settings.AI_BEAT_MODEL
        self._base_url = base_url or settings.OPENROUTER_BASE_URL

        configured = mode or settings.AI_STRUCTURED_OUTPUT_MODE
        try:
            self._policy = StructuredOutputMode(configured)
        except ValueError:
            raise AIConfigurationError(
                f"Unknown AI_STRUCTURED_OUTPUT_MODE '{configured}'. "
                f"Expected one of: {', '.join(m.value for m in StructuredOutputMode)}."
            ) from None

        # Many free models don't implement strict `json_schema` response formats,
        # so under AUTO we start strict and degrade on rejection, remembering the
        # downgrade so a repair attempt doesn't pay for the same rejection twice.
        self._mode = (
            self._JSON_MODE if self._policy is StructuredOutputMode.JSON_OBJECT else self._STRICT
        )

    @property
    def output_mode(self) -> str:
        """The response format last used — `json_schema` or `json_object`.

        Worth surfacing: a beat produced under `json_object` was validated by us
        rather than enforced by the provider, which is a materially weaker
        guarantee. `manage.py smoke_ai` reports it for exactly that reason.
        """
        return self._mode

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        from openai import BadRequestError, OpenAI

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            default_headers={
                "HTTP-Referer": settings.OPENROUTER_SITE_URL,
                "X-Title": self._ATTRIBUTION_TITLE,
            },
        )
        payload: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]

        if self._mode == self._STRICT:
            try:
                return self._complete(
                    client,
                    payload,
                    {
                        "type": "json_schema",
                        "json_schema": {"name": "beat", "strict": True, "schema": schema},
                    },
                )
            except BadRequestError:
                if self._policy is StructuredOutputMode.STRICT:
                    # The operator asked for enforced structure. Degrading here
                    # would hand back a weaker guarantee than they configured.
                    raise
                logger.warning(
                    "Model %s rejected a strict json_schema response format; "
                    "falling back to JSON mode + validate-and-repair.",
                    self._model,
                )
                self._mode = self._JSON_MODE

        # JSON mode constrains syntax but not shape, so the schema has to travel in
        # the prompt. ("json" appearing in the messages is also what OpenAI-compatible
        # APIs require before they will honour `json_object` at all.)
        payload[0] = {
            "role": "system",
            "content": (
                f"{system}\n\n"
                "Return only a single JSON object conforming exactly to this JSON Schema. "
                "No prose, no markdown fences.\n\n"
                f"{json.dumps(schema)}"
            ),
        }
        return self._complete(client, payload, {"type": self._JSON_MODE})

    def _complete(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> str:
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=messages,
            response_format=response_format,
        )

        if not response.choices:
            raise ProviderError(f"OpenRouter returned no choices for model '{self._model}'.")

        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderError(f"Model '{self._model}' declined the request.")

        content = choice.message.content
        if not content:
            # A truncated generation surfaces here as empty/partial content rather
            # than as an error, so name the likely cause instead of "no text".
            raise ProviderError(
                f"Model '{self._model}' returned no content "
                f"(finish_reason: {choice.finish_reason})."
            )
        return content


class _UnimplementedProvider:
    """Typed seam for a fallback provider that is declared but not yet built.

    It exists so the router's provider order, configuration, and error paths are
    real from Day 1 — adding the provider later is filling in `generate_json`,
    not restructuring the router.
    """

    name = "unimplemented"
    credential_env_var = ""
    output_mode = "n/a"

    def is_configured(self) -> bool:
        return False

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        raise NotImplementedError(f"Provider '{self.name}' is not implemented yet.")


class OpenAIProvider(_UnimplementedProvider):
    """Fallback seam — OpenAI (PRODUCT.md §19.3). Not implemented."""

    name = "openai"


class GoogleProvider(_UnimplementedProvider):
    """Fallback seam — Google (PRODUCT.md §19.3). Not implemented."""

    name = "google"


PROVIDERS: dict[str, type] = {
    AnthropicProvider.name: AnthropicProvider,
    OpenRouterProvider.name: OpenRouterProvider,
    OpenAIProvider.name: OpenAIProvider,
    GoogleProvider.name: GoogleProvider,
}


@dataclass
class ModelRouter:
    """Selects a provider and enforces a typed contract on whatever comes back."""

    providers: list[StructuredProvider]

    @classmethod
    def from_settings(cls) -> ModelRouter:
        order = list(settings.AI_PROVIDER_ORDER) or [AnthropicProvider.name]
        providers: list[StructuredProvider] = []
        for name in order:
            factory = PROVIDERS.get(name)
            if factory is None:
                raise AIConfigurationError(
                    f"Unknown AI provider '{name}' in AI_PROVIDER_ORDER. "
                    f"Known providers: {', '.join(sorted(PROVIDERS))}."
                )
            providers.append(factory())
        return cls(providers=providers)

    def select_provider(self) -> StructuredProvider:
        """The first provider in `AI_PROVIDER_ORDER` that has credentials.

        Public because callers legitimately need to know *who* answered before or
        after a generation — `manage.py smoke_ai` reports it, and Day 15's fallback
        work will log it. Selection is pure, so calling it twice is free.
        """
        for provider in self.providers:
            if provider.is_configured():
                return provider
        configured = ", ".join(p.name for p in self.providers) or "(none)"
        raise AIConfigurationError(
            # Plain ASCII on purpose: this prints to a terminal, and the Windows
            # console mangles anything that isn't.
            "No AI provider is configured. Set ANTHROPIC_API_KEY in your .env, or "
            "OPENROUTER_API_KEY with AI_PROVIDER_ORDER=openrouter,anthropic "
            f"(providers tried: {configured})."
        )

    def generate_beat(self, context: dict[str, Any]) -> Beat:
        """Generate one validated Beat.

        `context` carries the assembled prompt:
          - ``system``: the system layer (persona + non-negotiable rules)
          - ``user``: the beat request

        From Day 4 this is built by the prompt-assembly step from the child
        profile, memory bundle, and learning objective. Today the caller passes
        it directly.
        """
        try:
            return self.generate_structured(context, model=Beat, schema=beat_json_schema())
        except StructuredOutputError as exc:
            # The beat path keeps its own error type: Day 7 turns *this* one into
            # a safe fallback beat for a child, which is a different response
            # from a failed side-generation like a visual descriptor.
            raise BeatValidationError(str(exc)) from exc

    def generate_structured(
        self,
        context: dict[str, Any],
        *,
        model: type[ModelT],
        schema: dict[str, Any] | None = None,
    ) -> ModelT:
        """Generate one response validated against `model`, with one repair.

        The Beat loop is the hot path but not the only caller: the companion's
        canonical visual descriptor (§8.2) is generated the same way, and so is
        anything else that needs typed JSON out of a model. Validate-and-repair
        (§19.6) is a property of the *router*, not of beats, so it lives here
        once — a caller cannot accidentally opt out of it.
        """
        system = context.get("system")
        user = context.get("user")
        if not system or not user:
            raise AIConfigurationError("context must contain non-empty 'system' and 'user' keys.")

        provider = self.select_provider()
        json_schema = (
            schema if schema is not None else strip_unsupported_keywords(model.model_json_schema())
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

        raw = provider.generate_json(system=system, messages=messages, schema=json_schema)
        try:
            return self._parse(raw, model)
        except (ValidationError, json.JSONDecodeError) as first_error:
            logger.warning(
                "%s validation failed on provider %s; attempting one repair.",
                model.__name__,
                provider.name,
                exc_info=first_error,
            )

        # One repair attempt, then give up (§19.6). Repeated repair burns latency
        # and money on a generation that is usually structurally wrong.
        messages += [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "That response did not satisfy the required schema. "
                    "Return the corrected object as valid JSON matching the schema "
                    "exactly. Do not add fields, and do not omit any."
                ),
            },
        ]
        repaired = provider.generate_json(system=system, messages=messages, schema=json_schema)
        try:
            return self._parse(repaired, model)
        except (ValidationError, json.JSONDecodeError) as second_error:
            raise StructuredOutputError(
                f"Provider '{provider.name}' returned an invalid "
                f"{model.__name__} twice: {second_error}"
            ) from second_error

    @staticmethod
    def _parse(raw: str, model: type[ModelT]) -> ModelT:
        return model.model_validate(json.loads(raw))


def generate_beat(context: dict[str, Any]) -> Beat:
    """Module-level entry point — the seam every caller should use."""
    return ModelRouter.from_settings().generate_beat(context)


def generate_structured(
    context: dict[str, Any],
    *,
    model: type[ModelT],
    schema: dict[str, Any] | None = None,
) -> ModelT:
    """Module-level entry point for any non-beat typed generation."""
    return ModelRouter.from_settings().generate_structured(context, model=model, schema=schema)
