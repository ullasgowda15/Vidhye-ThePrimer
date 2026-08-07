"""The canonical visual descriptor (PRODUCT.md §8.2, §15.10, §9.6).

One sentence-and-a-half of text that says exactly what this companion looks
like, written once and then appended to every illustration prompt for the rest
of the friendship. It is the single highest-leverage string in the product: get
it wrong and Pip is a slightly different dragon every day, which reads as
uncanny to a four-year-old and breaks attachment (§23.1).

Two properties make it work, and both are enforced here rather than hoped for:

**It is generated once and persisted.** Regenerating it per story would produce
a new character each time, however good the prompt.

**There is always one.** If the language model is unreachable — no key, an
outage, a refusal — a deterministic descriptor is composed from the catalogue
instead. A companion with a template-written appearance is a companion; a
companion with no appearance is a broken product, and the child would be the one
to find out.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from apps.companion.catalog import archetype as get_archetype
from apps.companion.catalog import palette as get_palette
from apps.companion.catalog import preset as get_preset
from apps.story.ai.router import AIError, generate_structured

if TYPE_CHECKING:
    from apps.companion.models import Companion

logger = logging.getLogger(__name__)

SYSTEM = (
    "You are the art director for The Primer, an illustrated storybook for children "
    "aged three to seven. You write canonical character descriptions for an "
    "illustration model.\n\n"
    "A canonical description is used for EVERY picture of this character, forever, so "
    "it must describe only what is permanently true: body shape, proportions, colours, "
    "markings, and one or two distinctive features. Never describe a pose, an "
    "expression, an action, a background, a light source, or an art style — those "
    "change per picture and are supplied separately.\n\n"
    "House rules, without exception:\n"
    "- The character is a small, soft, non-threatening creature. Never a human child.\n"
    "- Nothing frightening: no fangs, claws, glowing red eyes, armour, or weapons.\n"
    "- Nothing photoreal. Rounded forms only.\n"
    "- Concrete and visual. No personality words, no adjectives an illustrator "
    "cannot draw.\n"
    "- British or American spelling is fine; plain words only, no poetry."
)


class VisualDescriptor(BaseModel):
    """The typed contract for a generated descriptor.

    Structured rather than free text so the pieces can be validated separately
    and so a model cannot answer with a paragraph of prose about how lovely the
    companion is.
    """

    model_config = ConfigDict(extra="forbid")

    appearance: str = Field(
        description=(
            "One or two sentences describing permanent physical appearance only: "
            "body shape, size, proportions, colours, markings."
        )
    )
    signature_features: list[str] = Field(
        description=(
            "Two or three short, permanent, distinctive details an illustrator would "
            "reproduce every time, e.g. 'one crooked horn'."
        )
    )


def build_context(companion: Companion) -> dict[str, str]:
    """The prompt for one descriptor generation. Pure — no model call."""
    spec = get_archetype(companion.archetype)
    colours = get_palette(companion.palette.get("key", ""))
    personality = get_preset(companion.personality.get("preset", ""))

    user = (
        f"Write the canonical description for this companion.\n\n"
        f"Name: {companion.name}\n"
        f"Kind: {spec.label}\n"
        f"Starting silhouette: {spec.silhouette}\n"
        f"Suggested signature detail: {spec.signature}\n"
        f"Colour palette: {colours.label} — {colours.description} "
        f"(primary {colours.primary}, secondary {colours.secondary}, "
        f"accent {colours.accent})\n"
        f"Temperament (for shape and posture only, do not describe it in words): "
        f"{', '.join(personality.traits)}\n"
    )
    return {"system": SYSTEM, "user": user}


def fallback_descriptor(companion: Companion) -> str:
    """A deterministic descriptor composed from the catalogue.

    Not a degraded placeholder to be replaced later: it is art-directed text
    built from the same silhouette and palette the model would have been given,
    so a companion created during a provider outage still looks like it belongs
    in this product.
    """
    spec = get_archetype(companion.archetype)
    colours = get_palette(companion.palette.get("key", ""))
    return (
        f"{companion.name}, {spec.silhouette}, in {colours.description} "
        f"(main {colours.primary}, lighter {colours.secondary}, accents {colours.accent}); "
        f"{spec.signature}"
    )


def render(descriptor: VisualDescriptor, companion: Companion) -> str:
    """Flatten a generated descriptor into the stored string."""
    features = "; ".join(feature.strip().rstrip(".") for feature in descriptor.signature_features)
    appearance = descriptor.appearance.strip()
    if not appearance.startswith(companion.name):
        appearance = f"{companion.name}, {appearance[0].lower()}{appearance[1:]}"
    return f"{appearance} Signature details: {features}." if features else appearance


def generate_visual_descriptor(companion: Companion) -> str:
    """The canonical descriptor for this companion, generated or composed.

    Never raises. A companion always ends up with a descriptor; the log records
    which route produced it.
    """
    try:
        descriptor = generate_structured(build_context(companion), model=VisualDescriptor)
    except AIError as exc:
        logger.warning(
            "Falling back to a catalogue descriptor for companion %s: %s",
            companion.id,
            exc,
        )
        return fallback_descriptor(companion)
    # Deliberately total: whatever goes wrong, the child still gets a friend.
    except Exception:
        logger.exception("Unexpected failure generating a descriptor for %s.", companion.id)
        return fallback_descriptor(companion)

    return render(descriptor, companion)
