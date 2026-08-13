"""Model router (PRODUCT.md §19.3, §19.6).

Why a router at all: portability (no provider lock-in), cost control (the right
model per call-type), and resilience (a provider outage must not be a product
outage). Claude is the configured primary; OpenRouter is a real, selectable
provider so the pipeline can be exercised on a free model; Google is Gemini on
Vertex AI, which is what the all-Google deployment runs on (`Docs/Hosting.md §3`);
OpenAI stays a typed seam so filling it in is wiring, not redesign.

Which provider runs is decided entirely by `AI_PROVIDER_ORDER` in the environment
— no code changes to switch.

Every provider call is wrapped by validate-and-repair. A generation that fails
the `Beat` schema gets exactly **one** repair attempt and then raises. The
bounded blast radius matters: the caller above us (Day 7) turns a raise into a
safe fallback beat, so the child sees a slightly generic story rather than an
error — but only if we fail fast instead of retrying forever.

**This module raises `AIError` and nothing else.** That is a contract, not a
tendency — see `_transport_error`.

**Every call through here is traced with its cost** (§19.9, §22.4). The router is
the only way a caller reaches an LLM, which is exactly why the span is written
here: instrumentation a caller has to remember is instrumentation that is missing
from the newest call site. See `_trace_llm_cost`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast, runtime_checkable

from django.conf import settings
from pydantic import BaseModel, ValidationError

from apps.common import flags
from apps.story.ai.schemas import Beat, beat_json_schema, json_schema_for

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


class BudgetExceededError(ProviderError):
    """The global inference budget for this window is spent (§18.8, `analytics.budget`).

    A `ProviderError` on purpose, and this is the whole design of the breaker: the
    orchestrator has caught `AIError` and answered with a pre-vetted warm fallback
    beat since Day 7, and `safety.classifier` has turned one into a recorded
    degradation for just as long. Giving the breaker its own error type *outside*
    that hierarchy would have meant writing a second degradation path on launch
    day — untested, on the one surface where an unhandled exception is a child
    looking at a dead screen.

    It is a distinct subclass so an operator reading a log can tell "we ran out of
    budget" from "Anthropic returned a 500", which are the same experience for a
    child and completely different work for a person.
    """


class BeatValidationError(StructuredOutputError):
    """A response came back but never satisfied the Beat schema."""


def _transport_error(provider: str, model: str, exc: BaseException) -> ProviderError:
    """Wrap anything an SDK threw. **This is the AI layer's boundary.**

    Everything above this module keys on `AIError`: `orchestrator.create_beat`
    turns one into a pre-vetted fallback beat (§19.6), `safety.classifier` turns
    one into a degradation rather than a pass (§13.3), and `streaming.stream_beat`
    turns one into a clean `done` frame. None of those handlers fire for an
    `openai.APITimeoutError`, an `anthropic.APIStatusError`, a 429 or a 400 —
    those are SDK types, and they are not `AIError` subclasses.

    So the commonest real failure in the product used to escape all three at once:
    no warm fallback, no degradation incident, and an SSE generator that died
    after its open frame, leaving a four-year-old watching the companion think
    forever. `StoryPage` has no error state *by design* (§4.9 forbids the spinner
    and §13 forbids showing a child an error), which is exactly why the layer
    below it must never hand it something it cannot render.

    Every `generate_json` therefore catches `Exception` and comes through here.
    The original is kept as `__cause__` — a caller that genuinely needs to know it
    was a 429 can still ask, and the operator log keeps the type name because
    "rate-limited" and "wrong model id" read identically in the message alone.
    """
    return ProviderError(
        f"Provider '{provider}' (model '{model}') failed: {type(exc).__name__}: {exc}"
    )


def _usage_from(response: Any, input_field: str, output_field: str) -> dict[str, int] | None:
    """Read a provider's token meter, or `None` if it did not report one.

    Every SDK spells the same two numbers differently and every one of them treats
    `usage` as optional, so this is `getattr` all the way down rather than
    attribute access: a provider that stops sending usage must cost us a *priced*
    span, never a `AttributeError` between a child and their story.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    incoming = getattr(usage, input_field, None)
    outgoing = getattr(usage, output_field, None)
    if incoming is None and outgoing is None:
        return None
    return {"input_tokens": int(incoming or 0), "output_tokens": int(outgoing or 0)}


def _add_usage(
    running: dict[str, int] | None, addition: dict[str, int] | None
) -> dict[str, int] | None:
    """Sum two meter readings. Either may be absent; both absent stays absent."""
    if addition is None:
        return running
    if running is None:
        return dict(addition)
    return {key: running.get(key, 0) + addition.get(key, 0) for key in {*running, *addition}}


#: Pydantic model name -> the span name a cost row is filed under. Mapped rather
#: than derived so the internal dashboard's "which generation is expensive" table
#: reads in the product's vocabulary (`plan`, `beat`, `safety`) instead of in
#: class names, and so renaming a schema does not silently split one operation's
#: history into two.
_OPERATIONS = {
    "Beat": "beat",
    "StoryPlan": "plan",
    "SessionMemories": "memories",
    "SafetyVerdict": "safety",
    "ReturningGreetingText": "greeting",
    "SessionHighlight": "highlight",
    "CharacterSheet": "character",
}


def operation_for(model: type[BaseModel]) -> str:
    """The span name for a typed generation. Unknown schemas keep their own name
    rather than being filed under "other" — an unfamiliar row in the cost table is
    a prompt to add it here, and an "other" bucket is where spend goes to hide."""
    return _OPERATIONS.get(model.__name__, model.__name__.lower())


def _trace_llm_cost(
    provider: StructuredProvider,
    *,
    operation: str,
    system: str,
    messages: list[dict[str, Any]],
    completion: str,
    started: float,
) -> None:
    """Write one `GenerationCost` span for a call that has just returned (§19.9).

    Called after **every** `generate_json`, including the repair attempt, because
    a repair is a second billed generation and §19.6's "one repair" budget is a
    cost decision as much as a latency one — it should be visible as one.

    **The estimate.** A provider that reports no usage is priced from character
    counts at `_CHARS_PER_TOKEN`, and the span is stamped `estimated: true` so
    nobody reads it as a measurement. Four characters per token is the usual rule
    of thumb for English and is wrong for other scripts; it is here because
    "roughly right, and labelled" beats a zero that looks like a fact.

    Import-local: `apps.analytics` imports nothing from `story`, but the story app
    is loaded during migrations and app-registry setup, and a module-level import
    of a model here would make the AI layer's importability depend on the app
    registry being ready.
    """
    from apps.analytics import cost
    from apps.analytics.models import CostKind

    usage = getattr(provider, "last_usage", None)
    if usage is None:
        prompt_chars = len(system) + sum(len(str(m.get("content", ""))) for m in messages)
        usage = {
            "input_tokens": prompt_chars // _CHARS_PER_TOKEN,
            "output_tokens": len(completion) // _CHARS_PER_TOKEN,
            "estimated": True,
        }

    cost.record(
        kind=CostKind.LLM,
        operation=operation,
        provider=provider.name,
        model=getattr(provider, "model", ""),
        units=usage,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


#: Only used when a provider reports no usage. See `_trace_llm_cost`.
_CHARS_PER_TOKEN = 4


def _check_budget(operation: str) -> None:
    """Refuse a paid generation when the window's budget is gone (§18.8).

    Translated into `BudgetExceededError` here — see that class: the breaker's
    whole design is that it lands in a degradation path that already exists and
    is already tested, rather than in a new one written on launch day.

    Import-local for the same reason `_trace_llm_cost`'s is: the AI layer's
    importability must not depend on the app registry being ready.
    """
    from apps.analytics import budget

    try:
        budget.check(operation=operation)
    except budget.BudgetExceeded as exc:
        raise BudgetExceededError(str(exc)) from exc


def _generate_traced(
    provider: StructuredProvider,
    *,
    operation: str,
    system: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
) -> str:
    """One `generate_json`, with its cost span written whichever way the call ends.

    Both providers read their token meter *before* the checks that can raise, and
    they say why: a refusal, an empty `choices` list and a truncated response all
    cost input tokens. Until Day 14's review that was only half true — the meter
    was read early and then discarded, because the span was written out here after
    `generate_json` returned, so every one of those endings lost its spend
    silently. §22.4 wants exactly those generations visible: they are the
    expensive failures (P1-5).

    **The span is written on failure only when the provider actually metered
    something.** A transport error that never reached the model was never billed,
    and `_trace_llm_cost`'s character estimate would happily invent a price for
    it — over-reporting spend that does not exist, which is the same dishonesty as
    under-reporting spend that does. `last_usage is not None` is the difference
    between "the model answered and we did not like the answer" and "we never got
    there", and it is the provider's own report either way.

    **The budget breaker is checked here** (§18.8), which is to say: at the one
    line in this module that actually pays a provider, and therefore on the repair
    attempt as well as the first call — a repair is a second billed generation,
    and a cap that only counted first attempts would have a hole in it exactly
    where the expensive generations are.
    """
    _check_budget(operation)

    started = time.monotonic()
    try:
        completion = provider.generate_json(system=system, messages=messages, schema=schema)
    except Exception:
        if getattr(provider, "last_usage", None) is not None:
            # No completion to measure: the meter is authoritative here, and
            # `_trace_llm_cost` only falls back to counting characters when the
            # provider reported nothing — which the guard above has excluded.
            _trace_llm_cost(
                provider,
                operation=operation,
                system=system,
                messages=messages,
                completion="",
                started=started,
            )
        raise

    _trace_llm_cost(
        provider,
        operation=operation,
        system=system,
        messages=messages,
        completion=completion,
        started=started,
    )
    return completion


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
        """Return the model's response as a raw JSON string.

        A provider **may** also set `self.last_usage` to
        `{"input_tokens": n, "output_tokens": n}` after each call, which is what
        `_trace_llm_cost` prices. Deliberately not part of this Protocol: it is
        optional (a provider that cannot report usage is still a valid provider,
        and gets an estimate instead), and the return type stays the one thing
        every caller actually wants. Read through `getattr`, never assumed.
        """
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
        #: See `StructuredProvider.generate_json`. Reset on every call so a failed
        #: generation can never be priced with the previous one's meter reading.
        self.last_usage: dict[str, int] | None = None

    @property
    def model(self) -> str:
        """Which model this provider will call — stamped on the rows it produces."""
        return self._model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        # Everything is inside the try, including the import and the client
        # construction: `_transport_error` is a boundary, and a boundary with a
        # line above it is not one. `except AIError: raise` keeps this method's
        # own considered refusals ("Claude declined the request") from being
        # re-labelled as transport failures.
        self.last_usage = None
        try:
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

            # Read the meter before anything below can raise. A refusal and a
            # truncated response both cost input tokens, and a span we only write
            # on the happy path would under-report exactly the generations worth
            # knowing the price of (§22.4).
            self.last_usage = _usage_from(response, "input_tokens", "output_tokens")

            # A safety classifier can decline before any content is produced. That
            # is a 200 with an empty `content`, so check the stop reason first.
            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                category = getattr(detail, "category", None) or "unspecified"
                raise ProviderError(f"Claude declined the request (category: {category}).")

            for block in response.content:
                if block.type == "text":
                    return block.text

            raise ProviderError("Claude returned no text content.")
        except AIError:
            raise
        except Exception as exc:
            raise _transport_error(self.name, self._model, exc) from exc


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
        #: See `StructuredProvider.generate_json`.
        self.last_usage: dict[str, int] | None = None

    @property
    def model(self) -> str:
        """Which model this provider will call — stamped on the rows it produces."""
        return self._model

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
        self.last_usage = None
        # See `_transport_error`. The inner `except BadRequestError` still runs
        # first — it is the nearer handler — so the strict-to-JSON-mode degrade is
        # unaffected; what changes is that the *re-raise* under `strict`, and every
        # timeout, 429 and 5xx underneath it, leaves here as a `ProviderError`
        # rather than as an SDK type nothing above us catches.
        try:
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

            # JSON mode constrains syntax but not shape, so the schema has to travel
            # in the prompt. ("json" appearing in the messages is also what
            # OpenAI-compatible APIs require before they will honour `json_object`.)
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
        except AIError:
            raise
        except Exception as exc:
            raise _transport_error(self.name, self._model, exc) from exc

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

        # Before the checks below, for the same reason the Anthropic branch reads
        # its meter early: a rejected or truncated generation was still paid for.
        # Note the *accumulate*: under AUTO a strict attempt can be rejected and
        # retried in JSON mode inside one `generate_json`, and both attempts are
        # billed. Overwriting here would silently halve the cost of every model
        # that does not support strict schemas.
        self.last_usage = _add_usage(
            self.last_usage,
            _usage_from(response, "prompt_tokens", "completion_tokens"),
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

    def __init__(self, api_key: str = "", model: str = "") -> None:
        # Accepted and kept so every entry in `PROVIDERS` is constructed the same
        # way. `ModelRouter.from_settings(model=…)` builds the whole order, and a
        # seam that raised on the keyword would make pinning a model to the
        # safety classifier depend on which providers happened to be listed.
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return False

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        # An `AIError`, not a `NotImplementedError`: this module's contract with
        # everything above it is that it raises `AIError` and nothing else
        # (`_transport_error`), and a seam is not an exception to a contract.
        # Unreachable through `select_provider`, which skips it for having no
        # credentials — but a caller holding a provider directly is not a bug that
        # should reach a child as a dead stream.
        raise AIConfigurationError(f"Provider '{self.name}' is not implemented yet.")


class OpenAIProvider(_UnimplementedProvider):
    """Fallback seam — OpenAI (PRODUCT.md §19.3). Not implemented."""

    name = "openai"


#: Gemini's own content filters, turned **off** for every category it will let us
#: turn off (§13.3).
#:
#: This looks like the wrong direction to move a child-safety product's dials and
#: it is the opposite. §13.3 makes *our* grader the authority — a separate model,
#: a written rubric, a recorded verdict on every beat, an incident row when it
#: refuses. A vendor filter sitting in front of that adds no safety we do not
#: already have and takes something away: it is tuned for a general audience,
#: it has no idea a wolf in a bedtime story is the point, and when it fires we get
#: a blocked response with a category name rather than a graded refusal. That is a
#: silent availability failure on the one surface where the child is waiting.
#:
#: So: Gemini's filter is off, ours runs, and a block that happens anyway (the
#: unfilterable categories, or a policy change) is translated into `ProviderError`
#: below and lands in the Day-5 degradation path like any other refusal.
#:
#: The four categories are the ones Vertex accepts a threshold for on the Gemini
#: 2.5 models. Naming them explicitly rather than iterating `HarmCategory` is
#: deliberate: the enum also holds image-only and unspecified members, and a
#: category the endpoint does not accept is a 400 on every generation.
_PERMISSIVE_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)

#: The finish reasons that mean "the model finished writing". Everything else —
#: SAFETY, PROHIBITED_CONTENT, RECITATION, BLOCKLIST, SPII, MAX_TOKENS, OTHER —
#: is a generation we cannot use, and each one becomes a `ProviderError`.
_GEMINI_COMPLETE = frozenset({"", "STOP", "FINISH_REASON_UNSPECIFIED"})


def _gemini_safety_settings() -> list[Any]:
    from google.genai import types

    return [
        types.SafetySetting(category=category, threshold="OFF")  # type: ignore[arg-type]
        for category in _PERMISSIVE_SAFETY_CATEGORIES
    ]


def _gemini_contents(messages: list[dict[str, Any]]) -> list[Any]:
    """Our `{role, content}` turns as Gemini `Content` objects.

    Gemini's assistant role is spelled `model`, which matters on the repair turn:
    `generate_structured` replays the bad generation as an `assistant` message,
    and a Gemini call that sent it as `user` would be asking the model to fix
    something it would read as the child's words (§19.1's layering, at the
    transport).
    """
    from google.genai import types

    return [
        types.Content(
            role="model" if message.get("role") == "assistant" else "user",
            parts=[types.Part(text=str(message.get("content", "")))],
        )
        for message in messages
    ]


def _gemini_usage(response: Any) -> dict[str, int] | None:
    """Gemini's token meter, or `None` if it did not report one.

    Not `_usage_from`: that reads `response.usage`, and Gemini calls it
    `usage_metadata`. Same contract, same `getattr`-all-the-way-down caution, and
    the same reason — a provider that stops reporting usage must cost us a priced
    span, never an `AttributeError` between a child and their story.

    `thoughts_token_count` is folded into the output side. Gemini bills thinking
    tokens at the output rate and does not include them in
    `candidates_token_count`, so leaving them out would under-report the price of
    exactly the generations that cost most (§22.4).
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    incoming = getattr(usage, "prompt_token_count", None)
    outgoing = getattr(usage, "candidates_token_count", None)
    thoughts = getattr(usage, "thoughts_token_count", None)
    if incoming is None and outgoing is None and thoughts is None:
        return None
    return {
        "input_tokens": int(incoming or 0),
        "output_tokens": int(outgoing or 0) + int(thoughts or 0),
    }


def _enum_name(value: Any) -> str:
    """An SDK enum member as its wire name, and anything else as its string."""
    return str(getattr(value, "value", value) or "")


def _gemini_text(candidate: Any) -> str:
    """The text parts of one candidate, joined.

    Thought parts are skipped. A thinking model returns its reasoning as parts
    flagged `thought=True` alongside the answer, and concatenating them would hand
    `json.loads` a JSON object with an essay in front of it — a validation failure
    that would burn the repair attempt on a generation that was actually fine.
    """
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    return "".join(
        str(text)
        for part in parts
        if not getattr(part, "thought", False) and (text := getattr(part, "text", ""))
    ).strip()


class GoogleProvider:
    """Gemini on Vertex AI — the provider for the all-Google deployment (§16.1).

    **Vertex, not AI Studio**, and the difference is the product's privacy
    promise rather than a preference: a Vertex call is authenticated by a service
    account, billed to the GCP project, and covered by the enterprise terms that
    exclude our inputs from training (§13.8, `Docs/Hosting.md §3`). The AI Studio
    free tier trains on prompts, and a beat prompt carries a child's first name,
    age band, fears and retrieved memories. `apps.common.vertex` is where that
    distinction is enforced; this class only ever asks it for a client.

    Two things it does differently from `AnthropicProvider`, both because Gemini
    does:

    * **The schema is translated** (`schemas.gemini_schema`). Gemini's
      `response_schema` is an OpenAPI 3.0 subset that rejects `$ref`, `$defs` and
      `additionalProperties` — all three of which Pydantic emits for `Beat`. The
      wire schema changes; the contract does not.
    * **Its own content filters are off** (`_PERMISSIVE_SAFETY_CATEGORIES`), and a
      block that happens anyway becomes a `ProviderError` rather than an empty
      string. See that constant, and `_transport_error`: everything above this
      module keys on `AIError`, and a blocked beat that returned `""` would sail
      past validate-and-repair as a JSON error and cost a second paid call for
      nothing.
    """

    name = "google"
    credential_env_var = "GOOGLE_APPLICATION_CREDENTIALS"

    #: Gemini enforces `response_schema` natively, like Claude does — there is no
    #: degraded JSON-mode to report.
    output_mode = "json_schema"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        # `api_key` is accepted and ignored so every entry in `PROVIDERS` is
        # constructed the same way. Vertex authenticates with a service account;
        # a provider that quietly accepted a key here would be an invitation to
        # configure the one route §13.8 forbids.
        self._model = model or settings.AI_BEAT_MODEL
        #: See `StructuredProvider.generate_json`. Reset on every call so a failed
        #: generation can never be priced with the previous one's meter reading.
        self.last_usage: dict[str, int] | None = None

    @property
    def model(self) -> str:
        """Which model this provider will call — stamped on the rows it produces."""
        return self._model

    def is_configured(self) -> bool:
        """True when project, location and a service-account file are all set.

        False rather than an exception on a half-set deployment, deliberately:
        this is what `select_provider` reads, so a missing location falls through
        to the next provider in `AI_PROVIDER_ORDER` instead of raising inside a
        beat that a child is waiting for.
        """
        from apps.common import vertex

        return vertex.is_configured()

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        # Everything inside the try, for the reason `AnthropicProvider` says it:
        # `_transport_error` is a boundary, and a boundary with a line above it is
        # not one. The schema translation is in here too — a contract Gemini
        # cannot express is a failure that must reach the child as a fallback
        # beat, not as a `ValueError` nothing above us catches.
        self.last_usage = None
        try:
            from google.genai import types

            from apps.common import vertex
            from apps.story.ai.schemas import gemini_schema

            config = types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=gemini_schema(schema),
                safety_settings=_gemini_safety_settings(),
            )
            response = vertex.client().models.generate_content(
                model=self._model,
                contents=_gemini_contents(messages),
                config=config,
            )

            # The meter, before anything below can raise. A blocked prompt, a
            # safety-stopped candidate and a truncated response all cost input
            # tokens, and a span written only on the happy path would under-report
            # exactly the generations worth knowing the price of (§22.4, Day-14
            # P1-5). Same ordering as the Anthropic branch, for the same reason.
            self.last_usage = _gemini_usage(response)

            # Three ways Gemini declines, in the order it produces them. Each is a
            # `ProviderError` so it lands in the Day-5 degradation path — the
            # child gets a pre-vetted warm beat, never a dead stream (§13.3).
            feedback = getattr(response, "prompt_feedback", None)
            if (blocked := getattr(feedback, "block_reason", None)) is not None:
                raise ProviderError(
                    f"Gemini blocked the request before generating "
                    f"(block_reason: {_enum_name(blocked) or 'unspecified'})."
                )

            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                raise ProviderError(f"Gemini returned no candidates for '{self._model}'.")

            candidate = candidates[0]
            finish = _enum_name(getattr(candidate, "finish_reason", None))
            if finish not in _GEMINI_COMPLETE:
                # Includes MAX_TOKENS on purpose. A truncated response is invalid
                # JSON, so returning it would spend the one repair attempt (§19.6)
                # re-generating something that will truncate again; the fallback
                # beat is both cheaper and what the child ends up with either way.
                raise ProviderError(
                    f"Gemini stopped without a usable response "
                    f"(finish_reason: {finish}) for '{self._model}'."
                )

            if not (text := _gemini_text(candidate)):
                raise ProviderError(f"Gemini returned no text content for '{self._model}'.")
            return text
        except AIError:
            raise
        except Exception as exc:
            raise _transport_error(self.name, self._model, exc) from exc


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
    def from_settings(cls, *, model: str = "") -> ModelRouter:
        """The configured provider order, optionally pinned to a specific model.

        `model` is what makes §13.3's "separate model, not self-grading" a fact
        about the weights rather than about the request: the safety classifier
        builds its router with `AI_SAFETY_MODEL`, so the guard and the generator
        can never be the same model unless someone configures them to be. Empty
        means "whatever `AI_BEAT_MODEL` says", which is every other caller — and
        *that* is the one the `ai.beat_model` flag may override, so a bad model
        change can be rolled back in seconds without a deploy (§16.10).

        The flag is deliberately not consulted when a caller has pinned a model.
        The safety grader's model is a §13.3 guarantee about the weights, not a
        knob, and a runtime override of it is exactly the kind of switch
        `apps.common.flags` refuses to have.
        """
        if not model:
            model = flags.value("ai.beat_model")

        order = list(settings.AI_PROVIDER_ORDER) or [AnthropicProvider.name]
        providers: list[StructuredProvider] = []
        for name in order:
            factory = PROVIDERS.get(name)
            if factory is None:
                raise AIConfigurationError(
                    f"Unknown AI provider '{name}' in AI_PROVIDER_ORDER. "
                    f"Known providers: {', '.join(sorted(PROVIDERS))}."
                )
            providers.append(factory(model=model))
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
        # `json_schema_for` and not `strip_unsupported_keywords`: the two differ by
        # `trim_descriptions`, and this is the branch every caller that does not
        # build its own schema arrives on — `StoryPlan`, `ReturningGreetingText`,
        # `VisualDescriptor`. Sending Pydantic's untrimmed docstrings here put our
        # engineering rationale in front of the model as field guidance on three
        # contracts, which is the §19.1 layering problem `trim_descriptions` exists
        # to fix, and made `json_schema_for`'s "every schema we send passes through
        # this" untrue (Day-14 review, P1-3).
        json_schema = schema if schema is not None else json_schema_for(model)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        operation = operation_for(model)

        raw = _generate_traced(
            provider,
            operation=operation,
            system=system,
            messages=messages,
            schema=json_schema,
        )
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
        # `operation` is unchanged and the span is separate, so the cost table
        # shows a repaired beat as two `beat` spans. That is the honest shape: it
        # is what was billed, and a repair rate is then `spans / beats` rather than
        # a counter somebody has to remember to add.
        repaired = _generate_traced(
            provider,
            operation=operation,
            system=system,
            messages=messages,
            schema=json_schema,
        )
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


def model_label(provider: StructuredProvider) -> str:
    """`provider:model`, as a generated row records who wrote it.

    Two names rather than one because neither is enough on its own: the same model
    id is reachable through more than one provider, and a provider name alone says
    nothing about which model behind it produced a beat. A provider that does not
    name a model — the scripted one in the test suite, an unimplemented seam —
    reduces to its own name rather than to a lie about a default.
    """
    model = getattr(provider, "model", "")
    return f"{provider.name}:{model}" if model else provider.name


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
