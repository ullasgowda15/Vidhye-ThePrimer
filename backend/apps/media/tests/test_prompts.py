"""The illustration prompt and its cache key (PRODUCT.md §16.6, §15.9).

These are the properties the whole media cache rests on. If prompt assembly is
not deterministic, the cache never hits and every picture is paid for twice; if
the style tokens can go missing, one child's storybook contains two art styles.
"""

from apps.media.hashing import hashes_for, sha16
from apps.media.prompts import (
    AVOID_LABEL,
    CHARACTERS_LABEL,
    REFERENCES_LABEL,
    SCENE_LABEL,
    STYLE_LABEL,
    CharacterRef,
    build_illustration_prompt,
    character_signature,
    parse_sections,
)
from apps.media.style import NEGATIVE_TERMS, STYLE_VERSION, style_tokens

PIP = CharacterRef(
    key="companion:pip",
    name="Pip",
    descriptor="a small round-bellied baby dragon in warm amber, one crooked horn",
    reference_url="https://cdn.example/pip.png",
)
MOTH = CharacterRef(key="moth", name="Moth", descriptor="a palm-sized silver moth")
SCENE = "a mossy riverbank at dawn, three frogs on a log"


def test_prompt_carries_scene_characters_and_the_global_style() -> None:
    prompt = build_illustration_prompt(SCENE, [PIP])

    assert SCENE in prompt
    assert PIP.descriptor in prompt
    assert style_tokens() in prompt
    assert PIP.reference_url and PIP.reference_url in prompt


def test_every_negative_term_is_present() -> None:
    # §15.9's negative prompts are a safety control, not a style preference: no
    # photorealism and no realistic human children are the two that matter most.
    prompt = build_illustration_prompt(SCENE, [PIP])
    for term in NEGATIVE_TERMS:
        assert term in prompt


def test_prompt_is_byte_identical_for_identical_inputs() -> None:
    assert build_illustration_prompt(SCENE, [PIP, MOTH]) == build_illustration_prompt(
        SCENE, [PIP, MOTH]
    )


def test_cast_order_does_not_change_the_prompt_or_the_key() -> None:
    # A caller that lists the same two characters in the other order must not
    # buy a second render of the same picture.
    forwards = build_illustration_prompt(SCENE, [PIP, MOTH])
    backwards = build_illustration_prompt(SCENE, [MOTH, PIP])

    assert forwards == backwards
    assert character_signature([PIP, MOTH]) == character_signature([MOTH, PIP])


def test_reference_url_is_outside_the_character_hash() -> None:
    # Re-uploading the same anchor image to a new URL must not invalidate every
    # cached illustration in the product.
    moved = CharacterRef(PIP.key, PIP.name, PIP.descriptor, "https://cdn.example/moved.png")
    assert character_signature([PIP]) == character_signature([moved])


def test_a_changed_descriptor_changes_the_character_hash() -> None:
    other = CharacterRef(PIP.key, PIP.name, "a small blue dragon with two straight horns")
    assert character_signature([PIP]) != character_signature([other])


def test_style_version_is_part_of_the_style_hash() -> None:
    # Editing the art direction must invalidate the cache rather than leave two
    # styles interleaved in one child's storybook.
    assert STYLE_VERSION in style_tokens()
    assert sha16(style_tokens()) != sha16(style_tokens().replace(STYLE_VERSION, "9.9.9"))


def test_hashes_are_independent_of_each_other() -> None:
    style_a, character_a, scene_a = hashes_for("style A", "character A", "scene A")
    style_b, character_b, scene_b = hashes_for("style A", "character A", "scene B")

    assert (style_a, character_a) == (style_b, character_b)
    assert scene_a != scene_b


def test_sections_round_trip() -> None:
    sections = parse_sections(build_illustration_prompt(SCENE, [PIP]))

    assert sections[SCENE_LABEL] == SCENE
    assert PIP.descriptor in sections[CHARACTERS_LABEL]
    assert PIP.reference_url and PIP.reference_url in sections[REFERENCES_LABEL]
    assert sections[STYLE_LABEL] == style_tokens()
    assert "no photorealism" in sections[AVOID_LABEL]


def test_the_anchor_image_stays_out_of_the_character_description() -> None:
    """A character reads the same before and after their portrait exists.

    The first render of a companion has no anchor; every later one does. If the
    handle lived in `CHARACTERS`, that block — the thing a model attends to for
    *who this is*, and the thing `character_hash` is derived from — would change
    the moment a portrait was stored.
    """
    unanchored = CharacterRef(PIP.key, PIP.name, PIP.descriptor)
    before = parse_sections(build_illustration_prompt(SCENE, [unanchored]))
    after = parse_sections(build_illustration_prompt(SCENE, [PIP]))

    assert before[CHARACTERS_LABEL] == after[CHARACTERS_LABEL]
    assert before[REFERENCES_LABEL] != after[REFERENCES_LABEL]
    assert character_signature([unanchored]) == character_signature([PIP])
