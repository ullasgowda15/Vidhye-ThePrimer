"""Illustration prompt assembly (PRODUCT.md §16.6, §8.2, §5.8).

Every illustration in the product is built here: scene description + the fixed
global style tokens + each present character's canonical descriptor and
reference handle. Days 4 and 6 call this for story beats; Day 3 calls it for the
companion's own portrait and expression set.

Two properties matter more than prose quality:

**Determinism.** The same inputs must produce byte-identical text, because that
text is what gets hashed into the cache key. So sections are emitted in a fixed
order and characters are sorted by key — a caller that passes the same cast in a
different order must not pay for a second render of the same picture.

**Labelled sections.** The prompt is written as `SCENE:` / `CHARACTERS:` /
`STYLE:` / `AVOID:` blocks rather than one run-on sentence. Image models attend
to that structure, and it also lets anything downstream (the offline placeholder
adapter, a debugging tool, a future consistency checker) read back the part it
cares about instead of guessing.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from apps.media.style import negative_prompt, style_tokens

SCENE_LABEL = "SCENE"
CHARACTERS_LABEL = "CHARACTERS"
REFERENCES_LABEL = "REFERENCES"
STYLE_LABEL = "STYLE"
AVOID_LABEL = "AVOID"

SECTION_ORDER = (SCENE_LABEL, CHARACTERS_LABEL, REFERENCES_LABEL, STYLE_LABEL, AVOID_LABEL)


@dataclass(frozen=True, slots=True)
class CharacterRef:
    """One character as the illustration pipeline needs them (§8.2).

    `descriptor` is the canonical visual descriptor — the persisted sentence
    that makes Pip the same Pip in every picture. `reference_url` is the handle
    to an already-rendered anchor image; adapters that support image references
    use it, and the ones that don't still get the descriptor.
    """

    key: str
    name: str
    descriptor: str
    reference_url: str | None = None

    def render(self) -> str:
        """Who this character is, in words. The anchor image is *not* here.

        The reference handle lives in its own section because it is metadata
        about the render, not part of the description — and because keeping
        `CHARACTERS` free of it means that block is exactly what
        `character_hash` is computed from. Anything that reads a prompt back
        (the offline adapter, a debugging tool) then sees the same character
        whether or not an anchor image exists yet.
        """
        return f"{self.name}: {self.descriptor.strip()}"


def character_signature(characters: Sequence[CharacterRef]) -> str:
    """The string hashed into `character_hash`.

    Deliberately built from `key` + `descriptor` only. The reference URL is
    excluded because re-uploading the same anchor image to a new URL does not
    change who the character *is*, and including it would throw away the entire
    illustration cache for a storage migration.

    The consequence, stated plainly: two prompts that differ only in their
    `REFERENCES` block share a cache key, so the first render of a character
    (which has no anchor yet) is the one that gets reused. That is the intent —
    the anchor exists to *hold* the look that render established, not to change
    it.
    """
    parts = [f"{ref.key}={ref.descriptor.strip()}" for ref in sorted(characters, key=_sort_key)]
    return " | ".join(parts) if parts else "(no characters)"


def build_illustration_prompt(scene: str, character_refs: Sequence[CharacterRef]) -> str:
    """The full prompt for one illustration."""
    characters = sorted(character_refs, key=_sort_key)
    if characters:
        cast = "\n".join(f"- {ref.render()}" for ref in characters)
    else:
        cast = "- (no named characters; the scene itself is the subject)"

    anchors = [ref for ref in characters if ref.reference_url]
    references = (
        "\n".join(f"- {ref.name}: {ref.reference_url}" for ref in anchors)
        if anchors
        else "- (none yet; this render establishes the look)"
    )

    sections = {
        SCENE_LABEL: scene.strip(),
        CHARACTERS_LABEL: cast,
        REFERENCES_LABEL: references,
        STYLE_LABEL: style_tokens(),
        AVOID_LABEL: negative_prompt(),
    }
    return "\n\n".join(f"{label}:\n{sections[label]}" for label in SECTION_ORDER)


def parse_sections(prompt: str) -> dict[str, str]:
    """Split a prompt built above back into its labelled sections.

    Exists so a consumer can read one part of a prompt without re-deriving it:
    the offline placeholder adapter draws its character from `CHARACTERS` and
    its background from `SCENE`, which is how it demonstrates the same character
    across different scenes. Unknown text before the first label is ignored.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    for line in prompt.splitlines():
        label = line[:-1] if line.endswith(":") else None
        if label in SECTION_ORDER:
            if current:
                sections[current] = "\n".join(buffer).strip()
            current, buffer = label, []
            continue
        if current:
            buffer.append(line)

    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _sort_key(ref: CharacterRef) -> str:
    return ref.key
