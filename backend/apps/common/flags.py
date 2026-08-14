"""Feature flags (PRODUCT.md §16.10, §19.9).

**What this is for: turning something off at 7am on a Sunday without a deploy.**
§16.10 asks for graceful degradation, and every lever in this file is one that a
person on call can pull when a provider is misbehaving or a bill is running hot —
each one trades some richness for spend or latency, and none of them takes the
product down.

Three layers, first answer wins:

1. a ``FeatureFlag`` row — set from the admin or ``manage.py flags``, effective
   within ``CACHE_TTL_S`` seconds and with no restart;
2. ``settings.FEATURE_FLAGS`` — the deployment's own default, from the environment;
3. the declared default in ``REGISTRY`` below.

The DB is on top deliberately: the whole point is a change that outruns a deploy
pipeline. The settings layer is under it so a deployment can ship a different
*baseline* (a staging box with illustrations off costs nothing to run) without a
row.

**Nothing in here can weaken the safety layer.** There is no flag that turns off
the input guard, the output classifier, the image scan, the fear ceiling or the
fallback. That is a rule, not an accident — a runtime switch that makes a
children's product less safe is a switch that will eventually be flipped by
someone with a deadline, and ``test_flags.py`` asserts the registry stays free of
one. Safety configuration lives in settings, where changing it is a deploy and a
code review, and where ``prod.py`` refuses to boot on the dangerous values.

**Reading a flag can never raise.** These are read on a child's story path, so a
database that is down or a cache that is misbehaving degrades to the declared
default — which is always the *shipping* behaviour — and never to an exception.
Same rule as ``events.track`` and ``cost.record``, for the same reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: How long a resolved value is held. Short, because the number that matters here
#: is "how long after I flip it does it take effect" and the answer has to be
#: small enough to say out loud during an incident. The cost of the read it saves
#: is one indexed lookup, so this is about write-to-effect latency, not load.
#:
#: The cache is per-process (LocMemCache unless a shared backend is configured),
#: so this is also the window in which two web workers can disagree. Ten seconds
#: of disagreement about whether to draw a picture is acceptable; that is part of
#: why nothing safety-bearing is allowed in here.
CACHE_TTL_S = 10

_CACHE_PREFIX = "primer:flag:"


@dataclass(frozen=True)
class Flag:
    """One declared lever.

    `default` is always the shipping behaviour, so every failure path in this
    module — no row, no settings entry, no database — lands on the product
    working normally rather than in a degraded mode nobody chose.
    """

    name: str
    default: str
    description: str
    #: Returns a human reason the value is unusable, or `None`. Applied on
    #: **write** (the admin form and `manage.py flags set`), never on read: a
    #: value already in the table has to resolve to something, and the moment to
    #: refuse a bad one is before it is stored.
    validate: Callable[[str], str | None] | None = None


#: Accepted spellings. Generous on input because these get typed into an admin
#: form at speed; `enabled()` is strict about anything outside the two sets.
TRUE = frozenset({"1", "true", "on", "yes"})
FALSE = frozenset({"0", "false", "off", "no"})


def _validate_beat_model(value: str) -> str | None:
    """The `ai.beat_model` override, checked exactly as the boot check checks the
    setting it overrides (`apps.common.model_ids`).

    Empty is always valid and means "use `AI_BEAT_MODEL`" — that is how the
    rollback is undone.

    **Three rules, and the last two were added by the Day 1-15 audit** (F-3, F-4).
    Reachability was never the only thing that can be wrong with a value typed
    into this box:

    * *Pairing.* An id the selected provider will answer `400` to breaks the
      product at the moment the lever is being used to fix it.
    * *Independence (§13.3).* The one value that must never be accepted here is
      the safety grader's own model id — it collapses the independent guard into
      self-grading, silently, with nothing logged and no dashboard row changed.
      The lever's own moment is a bad model in production at 3am, and the safety
      model's slug is the nearest known-good id to hand. §13.3's guarantee is a
      fact "about the weights and not only of the request"; after that flip it is
      a fact about neither.
    * *Provider terms (§13.8).* A beat prompt carries the child's first name,
      fears, avoid list and memory bundle, so the rollback lever must not be able
      to route them down a `:free` prompt-reselling route that `prod.py` would
      refuse at boot. A runtime lever that can reach a state the boot guard
      forbids is a hole in the boot guard.
    """
    if not value:
        return None

    from apps.common.model_ids import pairing_error, training_error

    if value.strip() == str(settings.AI_SAFETY_MODEL).strip():
        return (
            # ASCII, like `model_ids.training_error` and for the same reason: this
            # is read in an admin form and in a terminal during an incident.
            f"ai.beat_model='{value}' is the safety grader's own model "
            f"(AI_SAFETY_MODEL). 13.3 requires the output classifier to be an "
            f"independent model - a model grading its own output is not a guard. "
            f"Roll back to a different writer, or change AI_SAFETY_MODEL first (a "
            f"deploy, on purpose, with somebody watching)."
        )

    if problem := training_error(setting="ai.beat_model", model=value):
        return problem

    order = list(settings.AI_PROVIDER_ORDER)
    credentials = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }
    provider = next((name for name in order if credentials.get(name)), "")
    return pairing_error(setting="ai.beat_model", model=value, provider=provider, order=order)


#: Every flag the product has. A flag not declared here does not exist: `value()`
#: raises on an unknown name rather than returning a default, so a typo in a call
#: site fails in a test instead of silently reading as "off" in production.
REGISTRY: dict[str, Flag] = {
    flag.name: flag
    for flag in (
        Flag(
            name="ai.beat_model",
            default="",
            description=(
                "Overrides AI_BEAT_MODEL at runtime. Empty means use the setting. "
                "This is the no-deploy rollback for a model change (§19.3)."
            ),
            validate=_validate_beat_model,
        ),
        Flag(
            name="media.illustrations",
            default="on",
            description=(
                "Draw new illustrations. Off serves whatever is already cached and "
                "skips the rest — the story is narrated and fully playable without a "
                "picture (§4.9), so this is the biggest spend lever that costs a "
                "child nothing they cannot do without."
            ),
        ),
        Flag(
            name="story.prefetch_next_beat",
            default="on",
            description=(
                "Generate the consequence beat while the child is still looking at "
                "the current one (§4.9's latency hiding). Off makes the next beat "
                "slower and stops paying for beats a child never reaches — the "
                "§19.9 prefetch-discipline lever."
            ),
        ),
    )
}


def value(name: str) -> str:
    """The flag's current value as a string. Never raises for a declared flag."""
    flag = REGISTRY.get(name)
    if flag is None:
        raise KeyError(
            f"Unknown feature flag '{name}'. Declare it in apps.common.flags.REGISTRY. "
            f"Known flags: {', '.join(sorted(REGISTRY))}."
        )

    key = f"{_CACHE_PREFIX}{name}"
    try:
        cached = cache.get(key)
        if cached is not None:
            return str(cached)
        resolved = _resolve(flag)
        cache.set(key, resolved, CACHE_TTL_S)
        return resolved
    except Exception:
        # A child is on the other end of most of these reads. See the module
        # docstring: the declared default is the shipping behaviour, so the
        # degraded path here is "the product works normally".
        logger.exception("flags.read_failed name=%s falling_back_to_default", name)
        return flag.default


def enabled(name: str) -> bool:
    """A boolean flag's value.

    Anything unrecognised reads as the declared default rather than as `False`:
    a typo in the admin form must not be able to silently switch a feature off.

    `value` is called first so an *undeclared* name raises that function's
    message — which names the registry and lists the known flags — rather than a
    bare `KeyError` from the lookup below.
    """
    raw = value(name).strip().lower()
    flag = REGISTRY[name]
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    logger.warning("flags.unparseable name=%s value=%r using_default", name, raw)
    return flag.default.strip().lower() in TRUE


def _resolve(flag: Flag) -> str:
    """DB row, then settings, then the declared default."""
    from apps.common.models import FeatureFlag

    row = FeatureFlag.objects.filter(name=flag.name).values_list("value", flat=True).first()
    if row is not None:
        return str(row)

    override = getattr(settings, "FEATURE_FLAGS", {}) or {}
    if flag.name in override:
        return str(override[flag.name])

    return flag.default


def invalidate(name: str | None = None) -> None:
    """Drop cached values so the next read hits the database.

    Called by `FeatureFlag.save`/`delete` and by `manage.py flags`, so a change
    made in this process is visible to it immediately rather than in
    `CACHE_TTL_S` seconds. Other processes still wait out the TTL — which is the
    honest bound on how fast a flag change propagates, and is what the runbook
    quotes.
    """
    names = [name] if name else list(REGISTRY)
    try:
        cache.delete_many([f"{_CACHE_PREFIX}{each}" for each in names])
    except Exception:  # pragma: no cover - a cache backend that cannot delete
        logger.exception("flags.invalidate_failed name=%s", name)


def snapshot() -> list[dict[str, str]]:
    """Every flag, its value and where that value came from — for `manage.py
    flags` and the launch preflight.

    The *source* is the column that matters during an incident: "off" and "off
    because somebody set a row last Tuesday" are different situations, and a list
    of values alone cannot tell them apart.
    """
    from apps.common.models import FeatureFlag

    rows = dict(FeatureFlag.objects.values_list("name", "value"))
    override = getattr(settings, "FEATURE_FLAGS", {}) or {}

    out: list[dict[str, str]] = []
    for name, flag in sorted(REGISTRY.items()):
        if name in rows:
            source, current = "database", str(rows[name])
        elif name in override:
            source, current = "settings", str(override[name])
        else:
            source, current = "default", flag.default
        out.append(
            {
                "name": name,
                "value": current,
                "source": source,
                "default": flag.default,
                "description": flag.description,
            }
        )
    return out
