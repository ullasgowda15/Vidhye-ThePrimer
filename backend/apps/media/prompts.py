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
`STYLE:` blocks rather than one run-on sentence. Image models attend to that
structure, and it also lets anything downstream (the offline placeholder
adapter, a debugging tool, a future consistency checker) read back the part it
cares about instead of guessing.

**What is deliberately absent.** Two things this prompt used to carry, and must
not:

* *The negatives.* §15.9's list travels in a provider's `negative_prompt`
  parameter or not at all (`style.py`). Concatenated into a positive prompt it
  is worse than omitted — a diffusion model reads "no text" as *text*.
* *A storage path.* Character consistency in this product is **descriptor-only**
  until an image-conditioning provider is wired. `flux-1-schnell` is text-only:
  it cannot fetch a reference image, so handing it one is inert at best and, as
  a filename in a prompt about a picture, actively harmful. The `REFERENCES:`
  block is emitted only when the selected provider declares
  `supports_image_reference`, and only ever with a URL that provider can reach.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from apps.media.style import style_tokens

logger = logging.getLogger(__name__)

SCENE_LABEL = "SCENE"
CHARACTERS_LABEL = "CHARACTERS"
REFERENCES_LABEL = "REFERENCES"
STYLE_LABEL = "STYLE"

SECTION_ORDER = (SCENE_LABEL, CHARACTERS_LABEL, REFERENCES_LABEL, STYLE_LABEL)


@dataclass(frozen=True, slots=True)
class CharacterRef:
    """One character as the illustration pipeline needs them (§8.2).

    `descriptor` is the canonical visual descriptor — the persisted sentence
    that makes Pip the same Pip in every picture, and today the *only* thing
    holding a companion's look together. `reference_url` is the handle to an
    already-rendered anchor image; it reaches a model only when that model can
    consume one (see `build_illustration_prompt`), which no provider currently
    implemented can.
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


def build_illustration_prompt(
    scene: str,
    character_refs: Sequence[CharacterRef],
    *,
    include_references: bool = False,
) -> str:
    """The full positive prompt for one illustration.

    `include_references` must be the selected provider's
    `supports_image_reference` flag and nothing else — pass it from
    `adapters.image.supports_image_reference()`. It defaults to `False` because
    that is the truth for every provider implemented today: consistency is
    carried by the canonical descriptor alone, and a `REFERENCES:` block that a
    text-only model cannot act on would read as a working mechanism while doing
    nothing.

    Anchors are emitted only as absolute URLs. A storage-relative path is not a
    thing any provider can fetch, so it is dropped rather than written into a
    prompt whose whole job is to describe a picture.
    """
    characters = sorted(character_refs, key=_sort_key)
    if characters:
        cast = "\n".join(f"- {ref.render()}" for ref in characters)
    else:
        cast = "- (no named characters; the scene itself is the subject)"

    sections = {
        SCENE_LABEL: scene.strip(),
        CHARACTERS_LABEL: cast,
        STYLE_LABEL: style_tokens(),
    }
    order = [SCENE_LABEL, CHARACTERS_LABEL, STYLE_LABEL]

    if include_references and (anchors := _reachable_anchors(characters)):
        sections[REFERENCES_LABEL] = "\n".join(f"- {name}: {url}" for name, url in anchors)
        order.insert(2, REFERENCES_LABEL)

    return "\n\n".join(f"{label}:\n{sections[label]}" for label in order)


def _reachable_anchors(characters: Sequence[CharacterRef]) -> list[tuple[str, str]]:
    """Anchor URLs a provider could actually fetch, in cast order."""
    reachable: list[tuple[str, str]] = []
    for ref in characters:
        url = ref.reference_url
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            # In development the media backend is the local disk and this is a
            # `/media/...` path. Nothing off-box can resolve it.
            logger.debug(
                "Dropping anchor for %s: %r is not a provider-reachable URL.", ref.name, url
            )
            continue
        reachable.append((ref.name, url))
    return reachable


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
