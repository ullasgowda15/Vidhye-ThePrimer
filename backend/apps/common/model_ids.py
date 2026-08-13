"""What a model id is allowed to look like, given the provider (PRODUCT.md §19.3).

One rule, in one place, because it now has two callers that must not drift:

* ``config.settings.base._check_model_pairing`` — the boot check. An Anthropic
  model id sitting in an OpenRouter deployment answers ``400`` to *every* call,
  and when the setting in question is ``AI_SAFETY_MODEL`` that is the strongest
  guard in the product silently off (Day 7's review found exactly that).
* ``apps.common.flags`` — the ``ai.beat_model`` flag, which exists so a bad model
  change can be rolled back **without a deploy** (§16.10). A rollback lever that
  can itself be set to something unreachable is a lever that breaks the product
  at the moment it is being used to fix it, so the flag is validated on write by
  the same rule the boot check applies to the setting.

Deliberately narrow, and unchanged from the check it was extracted out of: it
compares the *shape* of the id against the provider that will actually be
selected — a gateway slug has a vendor prefix, a Claude id does not — and nothing
else. Whether the model exists is the provider's answer to give.

**Imported by Django settings**, so this module may import nothing from Django
and must touch neither models nor the app registry: at the moment
``config.settings.base`` runs, neither exists yet.
"""

from __future__ import annotations


def looks_like_slug(model: str) -> bool:
    """Is this a gateway-style ``vendor/model`` slug rather than a bare model id?"""
    return "/" in model


def pairing_error(*, setting: str, model: str, provider: str, order: list[str]) -> str | None:
    """The reason `model` cannot be used under `provider`, or `None` if it can.

    `provider` is the one that will actually be selected — first in `order` with
    credentials — and an empty string means nothing is configured, in which case
    there is no pairing to be wrong about and nothing will be called.
    """
    if not provider or not model:
        return None

    if provider == "openrouter" and not looks_like_slug(model):
        message = (
            f"{setting}='{model}' is not an OpenRouter model slug, but openrouter is "
            f"the provider that will be selected (AI_PROVIDER_ORDER={list(order)}). "
            f"OpenRouter will answer 400 to every call. Set {setting} to a "
            f"'vendor/model' slug valid for your gateway."
        )
        if setting == "AI_SAFETY_MODEL":
            message += (
                " AI_SAFETY_MODEL is the independent safety grader (§13.3) — give it a "
                "capable model, not the cheapest free one on the list."
            )
        return message

    if provider == "anthropic" and looks_like_slug(model):
        return (
            f"{setting}='{model}' looks like an OpenRouter slug, but anthropic is the "
            f"provider that will be selected (AI_PROVIDER_ORDER={list(order)}). Use a "
            f"Claude model id, or put openrouter first in AI_PROVIDER_ORDER."
        )

    return None
