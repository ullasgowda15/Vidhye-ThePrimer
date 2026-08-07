"""The companion creation catalogue (PRODUCT.md §9.1, §9.2, §14.5).

Archetypes, palettes and personality presets, in one place, because they are
the same data in three surfaces: the picker renders them, the serializer
validates against them, and the descriptor prompt is built from them. Duplicating
them in the frontend is how "gentle fox" becomes two slightly different foxes.

**Few options, excellent defaults.** §9.1 is explicit: a sixty-second setup must
yield a lovable companion. Five archetypes, five palettes and three personality
presets is a choice a four-year-old can make by pointing, and every combination
is one we are happy to ship.

The `silhouette` and `signature` lines are the seeds of the canonical visual
descriptor (§15.10). They are written here rather than invented per companion so
that even the fallback descriptor — the one used when the language model is
unreachable — is art-directed rather than generic.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Archetype:
    key: str
    label: str
    #: One warm line, read aloud to the child in the picker.
    blurb: str
    silhouette: str
    signature: str
    default_palette: str
    default_preset: str
    #: A *semantic* voice key, not a vendor id. Stored on the companion at
    #: creation so the voice is fixed for the life of the friendship (§9.1); the
    #: TTS adapter maps it to whatever the current provider calls that voice, so
    #: changing vendors does not change who a child's friend sounds like.
    voice_id: str


@dataclass(frozen=True, slots=True)
class Palette:
    key: str
    label: str
    primary: str
    secondary: str
    accent: str
    #: How the colours are described *to the image model*, in words, since a hex
    #: triple means nothing to it.
    description: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "primary": self.primary,
            "secondary": self.secondary,
            "accent": self.accent,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class PersonalityPreset:
    key: str
    label: str
    traits: tuple[str, ...]
    speech: str
    values: tuple[str, ...]
    catchphrases: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """The stored `companion.personality` JSON (§17.2).

        Shape matches §9.2 — traits + speech style + catchphrases + values — so
        the prompt assembler can inject it without knowing about presets at all.
        """
        return {
            "preset": self.key,
            "traits": list(self.traits),
            "speech": self.speech,
            "values": list(self.values),
            "catchphrases": list(self.catchphrases),
        }


ARCHETYPES: dict[str, Archetype] = {
    archetype.key: archetype
    for archetype in (
        Archetype(
            key="dragon",
            label="a small dragon",
            blurb="A little dragon who is braver than they look.",
            silhouette=(
                "a small round-bellied baby dragon the size of a house cat, stubby wings, "
                "a soft curled tail, tiny blunt horns, no teeth showing"
            ),
            signature="a single crooked horn and a tail tip shaped like a leaf",
            default_palette="ember",
            default_preset="brave_spark",
            voice_id="bright_young",
        ),
        Archetype(
            key="fox",
            label="a gentle fox",
            blurb="A gentle fox who notices everything.",
            silhouette=(
                "a small sitting fox cub with an oversized fluffy tail, rounded ears, "
                "soft cheek fur, gentle half-closed eyes"
            ),
            signature="one white-tipped ear and a tail tip like a paintbrush",
            default_palette="ember",
            default_preset="warm_guide",
            voice_id="warm_low",
        ),
        Archetype(
            key="robot",
            label="a curious robot",
            blurb="A curious little robot who loves questions.",
            silhouette=(
                "a small friendly rounded robot the height of a toddler, dome head, "
                "big soft-glowing oval eyes, stubby arms, no sharp edges, no screens"
            ),
            signature="a little antenna with a soft glowing bead on the end",
            default_palette="tide",
            default_preset="curious_helper",
            voice_id="clear_bright",
        ),
        Archetype(
            key="star",
            label="a little star",
            blurb="A little star who came down to play.",
            silhouette=(
                "a small rounded five-pointed star creature with soft blunt points, "
                "little arms, a gentle glow, cheeks like a child's drawing"
            ),
            signature="a soft halo of glow and one point that always droops",
            default_palette="dusk",
            default_preset="warm_guide",
            voice_id="airy_soft",
        ),
        Archetype(
            key="owl",
            label="a sleepy owl",
            blurb="A sleepy owl who knows the way in the dark.",
            silhouette=(
                "a small round owlet, mostly fluff, large calm eyes, tiny beak, "
                "feathered feet, wings held close"
            ),
            signature="a tuft of feathers above each eye like eyebrows",
            default_palette="moss",
            default_preset="calm_anchor",
            voice_id="calm_deep",
        ),
    )
}

PALETTES: dict[str, Palette] = {
    palette.key: palette
    for palette in (
        Palette("ember", "Warm ember", "#e2803c", "#f4c67a", "#8c4a2f", "warm amber and rust"),
        Palette("moss", "Forest moss", "#5c9455", "#bcd9a0", "#33603a", "soft mossy greens"),
        Palette("tide", "Sea tide", "#3f8fa8", "#a8dbe4", "#1f5a6e", "cool teal and sea glass"),
        Palette("dusk", "Evening dusk", "#7a5fb0", "#c9b6ea", "#3f2f66", "gentle violet and dusk"),
        Palette("blossom", "Blossom", "#d9738f", "#f6c9d6", "#8d3f5a", "soft rose and blossom"),
    )
}

PERSONALITY_PRESETS: dict[str, PersonalityPreset] = {
    preset.key: preset
    for preset in (
        PersonalityPreset(
            key="warm_guide",
            label="Warm and steady",
            traits=("warm", "steady", "encouraging", "endlessly patient"),
            speech="short, gentle sentences; often repeats the child's name; never rushes",
            values=("kindness", "trying is the win", "everyone belongs"),
            catchphrases=("Oh, I'm so glad you're here.", "Shall we look together?"),
        ),
        PersonalityPreset(
            key="brave_spark",
            label="Playful and brave",
            traits=("playful", "brave-but-gentle", "delighted", "a little silly"),
            speech="bouncy and bright; small exclamations; invites the child to lead",
            values=("courage is going anyway", "effort over outcome", "share the fun"),
            catchphrases=("Ready? I'll go first!", "That was YOUR idea. It was brilliant."),
        ),
        PersonalityPreset(
            key="calm_anchor",
            label="Calm and cosy",
            traits=("calm", "soft-spoken", "reassuring", "observant"),
            speech="slow, quiet sentences with lots of pauses; good for winding down",
            values=("it is safe here", "feelings are allowed", "rest is part of an adventure"),
            catchphrases=("We can take our time.", "I'm right here."),
        ),
        PersonalityPreset(
            key="curious_helper",
            label="Curious and clever",
            traits=("curious", "inventive", "wondering aloud", "never a know-it-all"),
            speech="asks more than it tells; wonders aloud; celebrates the child's answers",
            values=("questions are treasure", "not knowing is exciting", "figure it out together"),
            catchphrases=("Ooh — what do YOU think?", "Let's find out!"),
        ),
    )
}

DEFAULT_ARCHETYPE = "fox"
DEFAULT_PALETTE = "ember"
DEFAULT_PRESET = "warm_guide"

ARCHETYPE_KEYS = tuple(ARCHETYPES)
PALETTE_KEYS = tuple(PALETTES)
PRESET_KEYS = tuple(PERSONALITY_PRESETS)


def archetype(key: str) -> Archetype:
    return ARCHETYPES.get(key) or ARCHETYPES[DEFAULT_ARCHETYPE]


def palette(key: str) -> Palette:
    return PALETTES.get(key) or PALETTES[DEFAULT_PALETTE]


def preset(key: str) -> PersonalityPreset:
    return PERSONALITY_PRESETS.get(key) or PERSONALITY_PRESETS[DEFAULT_PRESET]


def catalog() -> dict[str, Any]:
    """The whole catalogue, as the picker consumes it.

    Served over the API rather than duplicated in the frontend so there is one
    list of archetypes in the product, not two that drift.
    """
    return {
        "archetypes": [
            {
                "key": item.key,
                "label": item.label,
                "blurb": item.blurb,
                "default_palette": item.default_palette,
                "default_personality_preset": item.default_preset,
            }
            for item in ARCHETYPES.values()
        ],
        "palettes": [item.as_dict() for item in PALETTES.values()],
        "personality_presets": [
            {"key": item.key, "label": item.label, "traits": list(item.traits)}
            for item in PERSONALITY_PRESETS.values()
        ],
    }
