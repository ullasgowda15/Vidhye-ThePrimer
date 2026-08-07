"""Model router (PRODUCT.md §19.3, §19.6).

Why a router at all: portability (no provider lock-in), cost control (the right
model per call-type), and resilience (a provider outage must not be a product
outage). Claude is primary; OpenAI and Google are declared here as typed seams
so Day 15's fallback work is wiring, not redesign.

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
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from django.conf import settings
from pydantic import ValidationError

from apps.story.ai.schemas import Beat, beat_json_schema

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam

logger = logging.getLogger(__name__)

# How many tokens a single beat may cost. A beat is a few hundred words; the
# headroom is for adaptive thinking, which is on by default on Claude Opus 5.
MAX_OUTPUT_TOKENS = 8000


class AIError(Exception):
    """Base class for AI-layer failures."""


class AIConfigurationError(AIError):
    """The AI layer is not usable — missing key, no configured provider."""


class ProviderError(AIError):
    """A provider was reached but could not produce a usable response."""


class BeatValidationError(AIError):
    """A response came back but never satisfied the Beat schema."""


@runtime_checkable
class BeatProvider(Protocol):
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


class _UnimplementedProvider:
    """Typed seam for a fallback provider that is declared but not yet built.

    It exists so the router's provider order, configuration, and error paths are
    real from Day 1 — adding the provider later is filling in `generate_json`,
    not restructuring the router.
    """

    name = "unimplemented"

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
    OpenAIProvider.name: OpenAIProvider,
    GoogleProvider.name: GoogleProvider,
}


@dataclass
class ModelRouter:
    """Selects a provider and enforces the Beat contract on whatever comes back."""

    providers: list[BeatProvider]

    @classmethod
    def from_settings(cls) -> ModelRouter:
        order = list(settings.AI_PROVIDER_ORDER) or [AnthropicProvider.name]
        providers: list[BeatProvider] = []
        for name in order:
            factory = PROVIDERS.get(name)
            if factory is None:
                raise AIConfigurationError(
                    f"Unknown AI provider '{name}' in AI_PROVIDER_ORDER. "
                    f"Known providers: {', '.join(sorted(PROVIDERS))}."
                )
            providers.append(factory())
        return cls(providers=providers)

    def _select(self) -> BeatProvider:
        for provider in self.providers:
            if provider.is_configured():
                return provider
        configured = ", ".join(p.name for p in self.providers) or "(none)"
        raise AIConfigurationError(
            "No AI provider is configured. Set ANTHROPIC_API_KEY in your .env "
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
        system = context.get("system")
        user = context.get("user")
        if not system or not user:
            raise AIConfigurationError("context must contain non-empty 'system' and 'user' keys.")

        provider = self._select()
        schema = beat_json_schema()
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

        raw = provider.generate_json(system=system, messages=messages, schema=schema)
        try:
            return self._parse(raw)
        except (ValidationError, json.JSONDecodeError) as first_error:
            logger.warning(
                "Beat validation failed on provider %s; attempting one repair.",
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
                    "That response did not satisfy the required beat schema. "
                    "Return the corrected beat as valid JSON matching the schema "
                    "exactly. Do not add fields, and do not omit any."
                ),
            },
        ]
        repaired = provider.generate_json(system=system, messages=messages, schema=schema)
        try:
            return self._parse(repaired)
        except (ValidationError, json.JSONDecodeError) as second_error:
            raise BeatValidationError(
                f"Provider '{provider.name}' returned an invalid beat twice: {second_error}"
            ) from second_error

    @staticmethod
    def _parse(raw: str) -> Beat:
        return Beat.model_validate(json.loads(raw))


def generate_beat(context: dict[str, Any]) -> Beat:
    """Module-level entry point — the seam every caller should use."""
    return ModelRouter.from_settings().generate_beat(context)
