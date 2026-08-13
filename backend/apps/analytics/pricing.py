"""What a generation costs, as a table (PRODUCT.md §19.9, §22.4).

Prices are data, not code, and they belong in exactly one place. Every rate below
is per the unit the vendor bills in, converted to US dollars:

* **LLM** — dollars per *million* tokens, input and output priced separately,
  because output is typically 3-5x input and a blended rate would make prompt
  minimisation (§19.9's second lever) look like it did nothing.
* **Image** — dollars per generated image.
* **TTS** — dollars per *thousand* characters of text spoken.

**A missing entry is not a zero.** `price_for` returns `None`, `cost.record`
stores `priced=False`, and every aggregate reports how many spans it could not
price alongside the total. This is the single most important property of this
module: the day someone swaps `AI_BEAT_MODEL` for a paid Claude route, an
unpriced-as-zero table would show cost/session *falling* to nothing and read as
good news. §22.4 makes cost/session a headline metric from day one, and a
headline metric that silently rounds new spend to zero is worse than no metric.

**Free routes are priced, at zero.** `nvidia/nemotron-...:free` costs nothing and
we know it costs nothing — that is `(0.0, 0.0)` and `priced=True`, which is a
different claim from "we have no idea". The dashboard can tell them apart.

**Overridable without a deploy.** `AI_PRICE_TABLE` in the environment is merged
over the defaults, keyed the same way, so a vendor's price change on a Tuesday is
a config edit. Local pricing beats the table; the table beats nothing.

*Rates below are list prices as configured for this build and are the number the
dashboard is honest about, not a quote. They are the thing to re-check before any
pricing decision is made off cost/session.*
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Mapping

#: `provider:model` -> (dollars per 1M input tokens, dollars per 1M output tokens).
#:
#: Keyed on the same `provider:model` string `ai.router.model_label` stamps on a
#: generated beat, so a row in `story.Beat.model` and a row in `GenerationCost`
#: name the same thing and can be joined by eye.
LLM_PRICES: dict[str, tuple[str, str]] = {
    # Anthropic (the configured primary, §16.1).
    "anthropic:claude-opus-5": ("5.00", "25.00"),
    "anthropic:claude-sonnet-5": ("3.00", "15.00"),
    "anthropic:claude-haiku-4-5-20251001": ("1.00", "5.00"),
    # Gemini on Vertex AI (Docs/Hosting.md §3 — the all-Google deployment).
    # Text list prices per 1M tokens. Thinking tokens bill at the output rate,
    # which is why `router._gemini_usage` folds `thoughts_token_count` into
    # `output_tokens` rather than reporting it separately: the number here is the
    # rate that applies to both.
    "google:gemini-2.5-flash": ("0.30", "2.50"),
    "google:gemini-2.5-flash-lite": ("0.10", "0.40"),
    # OpenRouter free routes — genuinely zero, and stored as a known zero.
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free": ("0.00", "0.00"),
    # The scripted provider in the test suite. It has no model and costs nothing,
    # and pricing it keeps CI's cost aggregates on the `priced` path rather than
    # exercising only the unknown-model branch.
    "scripted": ("0.00", "0.00"),
}

#: `provider` (or `provider:model`) -> dollars per generated image.
IMAGE_PRICES: dict[str, str] = {
    # Cloudflare Workers AI flux-1-schnell, within the free neuron allowance.
    "cloudflare": "0.00",
    # The offline placeholder renderer draws locally.
    "local": "0.00",
    # `media.stock` — a pre-vetted file we already hold. Priced, at zero, rather
    # than left unknown: it is genuinely free, and an unpriced span here would put
    # the safety substitution path permanently in the dashboard's "we do not know
    # what this costs" column.
    "stock": "0.00",
}

#: `provider` (or `provider:model`) -> dollars per 1000 characters synthesised.
TTS_PRICES: dict[str, str] = {
    # ElevenLabs free tier: credits, not dollars, and zero out of pocket.
    "elevenlabs": "0.00",
    "google": "0.016",
    "local": "0.00",
}

_TABLES: dict[str, dict[str, Any]] = {
    "llm": LLM_PRICES,
    "image": IMAGE_PRICES,
    "tts": TTS_PRICES,
}


def _overrides(kind: str) -> Mapping[str, Any]:
    """`AI_PRICE_TABLE[kind]`, or nothing if it is absent or malformed.

    Deliberately forgiving: a mistyped environment variable must not stop a beat
    from being generated. It costs the operator an unpriced span, which the
    dashboard says out loud.
    """
    table = getattr(settings, "AI_PRICE_TABLE", None)
    if not isinstance(table, dict):
        return {}
    section = table.get(kind)
    return section if isinstance(section, dict) else {}


def _lookup(kind: str, key: str) -> Any:
    """Environment override first, then the built-in table."""
    if key in (override := _overrides(kind)):
        return override[key]
    return _TABLES[kind].get(key)


def _rate(kind: str, label: str) -> Any:
    """The rate for `provider:model`, falling back to the bare provider name.

    One entry can therefore price a whole vendor when its models are close enough
    for the difference not to matter.

    The `is None` checks are not stylistic. A rate of `0` arriving as an integer
    from `AI_PRICE_TABLE` is falsy, so an `or` chain here would treat "this route
    is free" as "this route is unknown" and mark every free generation `priced=
    False` — the exact confusion the module docstring says must never happen.
    """
    exact = _lookup(kind, label)
    if exact is not None:
        return exact
    return _lookup(kind, label.split(":", 1)[0])


def llm_cost(label: str, *, input_tokens: int, output_tokens: int) -> Decimal | None:
    """Dollars for one LLM call, or `None` when `label` is not priced.

    `label` is `provider:model` as `ai.router.model_label` builds it. A provider
    with no model (the scripted one, an unimplemented seam) reduces to its own
    name, which is why `scripted` is a key above.
    """
    rates = _rate("llm", label)
    if rates is None:
        return None
    try:
        per_in, per_out = (Decimal(str(rates[0])), Decimal(str(rates[1])))
    except (TypeError, IndexError, ValueError):
        return None

    million = Decimal(1_000_000)
    return (Decimal(input_tokens) * per_in + Decimal(output_tokens) * per_out) / million


def image_cost(label: str, *, images: int = 1) -> Decimal | None:
    """Dollars for `images` generated pictures, or `None` when unpriced."""
    rate = _rate("image", label)
    if rate is None:
        return None
    try:
        return Decimal(str(rate)) * Decimal(images)
    except (TypeError, ValueError):
        return None


def tts_cost(label: str, *, characters: int) -> Decimal | None:
    """Dollars for `characters` of synthesised speech, or `None` when unpriced."""
    rate = _rate("tts", label)
    if rate is None:
        return None
    try:
        return Decimal(str(rate)) * Decimal(characters) / Decimal(1000)
    except (TypeError, ValueError):
        return None
