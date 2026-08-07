"""The illustration prompt and its cache key (PRODUCT.md §16.6, §15.9).

These are the properties the whole media cache rests on. If prompt assembly is
not deterministic, the cache never hits and every picture is paid for twice; if
the style tokens can go missing, one child's storybook contains two art styles.

Two of them are regression pins from the Day-3 review, and they are inversions
of what this file used to assert: the negatives must be **absent** from the
positive prompt, and an anchor URL must be absent unless the provider can
actually consume one. Both were "present" before, and both showed up painted
into a render.
"""

from apps.media.hashing import hashes_for, sha16
from apps.media.prompts import (
    CHARACTERS_LABEL,
    REFERENCES_LABEL,
    SCENE_LABEL,
    STYLE_LABEL,
    CharacterRef,
    build_illustration_prompt,
    character_signature,
    parse_sections,
)
from apps.media.style import NEGATIVE_TERMS, STYLE_VERSION, style_signature, style_tokens

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


def test_no_negative_term_ever_reaches_the_positive_prompt() -> None:
    """The Day-3 receipt: "no text" fed forward as the token *text*.

    §15.9's list is a safety control, so it has to reach the model *as a
    negative* — through a provider's `negative_prompt` parameter — or not at
    all. Concatenated into the positive prompt it does the opposite of its job:
    the live render that produced this test had the companion's name painted
    into the corner.
    """
    prompt = build_illustration_prompt(SCENE, [PIP], include_references=True).lower()

    for term in NEGATIVE_TERMS:
        assert term.lower() not in prompt
    # The bare nouns are what a diffusion model actually attends to.
    for token in ("watermark", "photorealism", "3d render"):
        assert token not in prompt


def test_no_reference_block_unless_the_provider_can_consume_one() -> None:
    prompt = build_illustration_prompt(SCENE, [PIP])

    assert REFERENCES_LABEL not in prompt
    assert PIP.reference_url and PIP.reference_url not in prompt


def test_a_capable_provider_gets_the_anchor_url() -> None:
    prompt = build_illustration_prompt(SCENE, [PIP], include_references=True)

    assert f"{REFERENCES_LABEL}:" in prompt
    assert PIP.reference_url and PIP.reference_url in prompt


def test_a_storage_path_is_never_put_in_a_prompt() -> None:
    """Even for a capable provider. `/media/...` is not fetchable by anyone.

    This is the literal Day-3 failure: `- Pip: /media/assets/…/ecdb8189….jpg`
    went to a text-only model, which read it as words about a picture.
    """
    local = CharacterRef(PIP.key, PIP.name, PIP.descriptor, "/media/assets/abc123.jpg")
    prompt = build_illustration_prompt(SCENE, [local], include_references=True)

    assert "/media/" not in prompt
    assert REFERENCES_LABEL not in prompt
    # The character is still fully described — consistency does not depend on
    # the anchor, and never did.
    assert local.descriptor in prompt


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


def test_the_image_model_is_in_the_cache_key_but_not_in_the_prompt(settings) -> None:
    """Switching models must not serve the old model's art (§22.4).

    And the model id must not reach the prompt: a text-to-image model would
    happily paint "flux-1-schnell" into the corner, which is the same class of
    bug as the negatives.
    """
    settings.IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
    schnell = style_signature()

    settings.IMAGE_MODEL = "@cf/stabilityai/stable-diffusion-xl-base-1.0"
    sdxl = style_signature()

    assert sha16(schnell) != sha16(sdxl)
    assert "stable-diffusion" not in build_illustration_prompt(SCENE, [PIP])
    assert "stable-diffusion" not in style_tokens()


def test_hashes_are_independent_of_each_other() -> None:
    style_a, character_a, scene_a = hashes_for("style A", "character A", "scene A")
    style_b, character_b, scene_b = hashes_for("style A", "character A", "scene B")

    assert (style_a, character_a) == (style_b, character_b)
    assert scene_a != scene_b


def test_sections_round_trip() -> None:
    sections = parse_sections(build_illustration_prompt(SCENE, [PIP], include_references=True))

    assert sections[SCENE_LABEL] == SCENE
    assert PIP.descriptor in sections[CHARACTERS_LABEL]
    assert PIP.reference_url and PIP.reference_url in sections[REFERENCES_LABEL]
    assert sections[STYLE_LABEL] == style_tokens()


def test_the_anchor_image_stays_out_of_the_character_description() -> None:
    """A character reads the same before and after their portrait exists.

    The first render of a companion has no anchor; every later one does. If the
    handle lived in `CHARACTERS`, that block — the thing a model attends to for
    *who this is*, and the thing `character_hash` is derived from — would change
    the moment a portrait was stored.
    """
    unanchored = CharacterRef(PIP.key, PIP.name, PIP.descriptor)
    before = parse_sections(build_illustration_prompt(SCENE, [unanchored], include_references=True))
    after = parse_sections(build_illustration_prompt(SCENE, [PIP], include_references=True))

    assert before[CHARACTERS_LABEL] == after[CHARACTERS_LABEL]
    assert before.get(REFERENCES_LABEL) != after.get(REFERENCES_LABEL)
    assert character_signature([unanchored]) == character_signature([PIP])
