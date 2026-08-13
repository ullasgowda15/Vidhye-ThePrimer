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

**A second rule joined it after the Day 1-15 audit** (F-3): whether a route's
terms let the provider train on what we send it. That is the same *kind* of
question — a fact readable off the model id, with two callers who must not drift
— and it had no home at all, which is how every child-data model in the shipped
configuration ended up on a ``:free`` prompt-reselling route while
``launch_check`` reported the grader as healthy. ``prod.py`` now refuses to boot
on one and ``launch_check`` blocks on one, both through ``training_error`` below.

**Imported by Django settings**, so this module may import nothing from Django
and must touch neither models nor the app registry: at the moment
``config.settings.base`` runs, neither exists yet.
"""

from __future__ import annotations

#: The suffix a gateway puts on the routes it does not charge for. On OpenRouter
#: a ``:free`` variant is the same weights under different *terms*: the provider
#: may log the prompt and may train on it, and that is the trade for the zero
#: price. Anything else — an unsuffixed slug, a bare Claude id — is a paid route
#: under API terms that exclude inputs from training.
#:
#: A suffix rather than an allow-list of vendors, deliberately. An allow-list
#: would have to be maintained against every gateway's catalogue and would be
#: stale the week after it was written; the suffix is the one thing the route
#: itself tells us, it is what the price is attached to, and it is what
#: ``Docs/PROGRESS.md``'s §13.8 rule was written about.
FREE_ROUTE_SUFFIX = ":free"

#: The settings whose requests carry a child's own words or a child's profile,
#: and which therefore may only run on a route whose terms forbid training
#: (§13.8). Named here so the boot guard, the preflight and the tests all read
#: one list instead of three.
#:
#: **All four, including the beat model.** The audit's F-3 recorded the founder's
#: decision on that: the beat prompt carries the child's first name, their age
#: band, the topics to avoid, the things they are frightened of and the retrieved
#: memory bundle, so "beats are only story text" was never true of this product.
#: The disclosure in ``apps.accounts.privacy`` promises training exclusion without
#: qualification, and a promise with a carve-out the parent is not told about is
#: the thing that makes a disclosure false.
CHILD_DATA_MODEL_SETTINGS = (
    "AI_BEAT_MODEL",
    "AI_SAFETY_MODEL",
    "AI_MEMORY_MODEL",
    "EMBEDDING_MODEL",
)


def looks_like_slug(model: str) -> bool:
    """Is this a gateway-style ``vendor/model`` slug rather than a bare model id?"""
    return "/" in model


def is_free_route(model: str) -> bool:
    """Is this the gateway's un-charged variant of a model?"""
    return model.strip().lower().endswith(FREE_ROUTE_SUFFIX)


def permits_training(model: str) -> bool:
    """May the provider of this route train on what we send it? (§13.8)

    The honest shape of this function: it answers *no* for everything except the
    one case we can actually detect from a model id. Whether a given paid route's
    terms exclude training is a purchasing decision recorded in ``Docs/Hosting.md
    §3``, not something a string can prove — so this is a guard against the
    specific, cheap, easy mistake that has already happened once, and it is not a
    certificate of compliance. That distinction is written into the message
    ``training_error`` produces, so nobody reads a green row as more than it is.
    """
    return is_free_route(model)


def training_error(*, setting: str, model: str) -> str | None:
    """The reason `model` may not carry child data, or ``None`` if it may.

    Called from ``prod.py``'s boot guards and from ``manage.py launch_check``.
    Both, rather than either: the boot guard is what stops a deployment existing
    in this state, and the preflight is what a human reads before a family is let
    in — and the audit found the preflight reporting a ``:free`` safety grader as
    ``OK`` because it only ever asked whether the route was *reachable*.

    **Plain ASCII, on purpose.** This string is printed by a boot failure and by
    ``launch_check``, both of which are read in a terminal, and the Windows
    console renders a section sign or an em dash as a replacement character.
    ``test_launch_check.py::test_its_output_is_plain_ascii`` already holds the
    preflight to this; a boot error nobody can read is worse, because it is the
    one message somebody meets at their least patient moment.
    """
    if not model or not permits_training(model):
        return None

    return (
        f"{setting}='{model}' is a '{FREE_ROUTE_SUFFIX}' route. Those terms permit the "
        f"gateway to log and train on what we send, and this setting carries a child's "
        f"own words or profile (13.8). apps.accounts.privacy tells parents 'we never "
        f"use your child's data to train anybody's AI models - not ours, and not our "
        f"providers'; that sentence has to be true. Point it at the paid variant of the "
        f"same model (drop the '{FREE_ROUTE_SUFFIX}' suffix) or at another route whose "
        f"terms exclude inputs from training - see Docs/Hosting.md section 3."
    )


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

    if provider == "google" and looks_like_slug(model):
        # Same shape mistake as the two below, one vendor over, and it arrives by
        # exactly the same route: `Docs/Hosting.md` recommended Gemini *through a
        # gateway* before there was a native provider, so a half-migrated `.env`
        # keeps a `google/gemini-...` slug while `AI_PROVIDER_ORDER` now says
        # `google`. Vertex answers 404 to a slug, on every call, which for
        # `AI_SAFETY_MODEL` is the strongest guard in the product silently off.
        return (
            f"{setting}='{model}' looks like a gateway model slug, but google is the "
            f"provider that will be selected (AI_PROVIDER_ORDER={list(order)}). Vertex "
            f"takes a bare Gemini model id - e.g. 'gemini-2.5-flash' - or put openrouter "
            f"first in AI_PROVIDER_ORDER to keep routing through a gateway."
        )

    if provider == "anthropic" and looks_like_slug(model):
        return (
            f"{setting}='{model}' looks like an OpenRouter slug, but anthropic is the "
            f"provider that will be selected (AI_PROVIDER_ORDER={list(order)}). Use a "
            f"Claude model id, or put openrouter first in AI_PROVIDER_ORDER."
        )

    return None
