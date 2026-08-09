"""Companion identity (PRODUCT.md §9.1, §9.6, §8.2).

Creating a companion is two things that must not be confused:

1. **The identity**, which is instant, synchronous, and never fails: archetype,
   name, palette, personality, voice. This is what the parent typed, and it is
   saved before anything slow happens.
2. **The reference set**, which is slow and fallible: a canonical descriptor
   from a language model, a portrait and five expressions from an image model,
   and a spoken greeting. This runs on a worker (§5.8, §16.9) and is idempotent,
   so a failed or half-finished run can simply be run again.

Keeping them apart is what lets the greeting scene open on "your friend is
waking up" instead of a spinner or an error (§13, §14.7), and what makes a
provider outage a delay rather than a lost companion.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.db import transaction

from apps.companion.catalog import archetype as get_archetype
from apps.companion.catalog import palette as get_palette
from apps.companion.catalog import preset as get_preset
from apps.companion.descriptor import generate_visual_descriptor
from apps.companion.models import (
    GREETING_AUDIO,
    PORTRAIT,
    AssetStatus,
    Companion,
)
from apps.companion.scenes import REFERENCE_ORDER, REFERENCE_SCENES, greeting_text
from apps.media import service as media_service
from apps.media.adapters import image as image_adapters
from apps.media.exceptions import MediaError
from apps.media.hashing import hashes_for
from apps.media.models import Media
from apps.media.prompts import CharacterRef, build_illustration_prompt, character_signature
from apps.media.style import style_signature

if TYPE_CHECKING:
    from apps.profiles.models import Child

logger = logging.getLogger(__name__)

#: The stage every companion starts at. Growth is §9.4 and lands much later —
#: today the field exists so evolution has somewhere to be written.
INITIAL_EVOLUTION_STATE = {"stage": 0}


def create_companion(
    *,
    child: Child,
    archetype: str,
    name: str,
    palette: str,
    personality_preset: str,
) -> Companion:
    """Create the child's one companion. Fast, local, and always succeeds."""
    spec = get_archetype(archetype)
    colours = get_palette(palette or spec.default_palette)
    personality = get_preset(personality_preset or spec.default_preset)

    return Companion.objects.create(
        child=child,
        archetype=spec.key,
        name=name.strip(),
        palette=colours.as_dict(),
        personality=personality.as_dict(),
        # Fixed at creation and never re-rolled: the voice is part of who this
        # friend is, and a companion that sounds different next week is a
        # different companion (§9.1).
        voice_id=spec.voice_id,
        evolution_state=dict(INITIAL_EVOLUTION_STATE),
        assets_status=AssetStatus.PENDING,
    )


def character_ref(companion: Companion) -> CharacterRef:
    """This companion as the illustration pipeline sees them (§8.2)."""
    return CharacterRef(
        key=f"companion:{companion.id}",
        name=companion.name,
        descriptor=companion.visual_descriptor,
        reference_url=portrait_url(companion),
    )


def portrait_url(companion: Companion) -> str | None:
    return reference_url(companion, PORTRAIT)


def reference_url(companion: Companion, label: str) -> str | None:
    """The URL of one pre-rendered reference asset — a portrait or an expression.

    None when that reference has not been generated yet, which is a real state
    rather than an error: `build_assets` is idempotent and a companion may be
    running on a partial set (`AssetStatus.PARTIAL`). Callers anchor to whatever
    exists (`cast.companion_ref`) rather than waiting for a face.
    """
    media = _media_for(companion, label)
    return media.url if media else None


def ensure_visual_descriptor(companion: Companion, *, force: bool = False) -> str:
    """Generate and persist the canonical descriptor, once."""
    if companion.visual_descriptor and not force:
        return companion.visual_descriptor

    companion.visual_descriptor = generate_visual_descriptor(companion)
    companion.save(update_fields=["visual_descriptor", "updated_at"])
    logger.info("companion.descriptor companion=%s", companion.id)
    return companion.visual_descriptor


def illustration_prompt(companion: Companion, scene: str) -> str:
    """The exact positive prompt `illustrate` sends for this scene.

    Its own function so that a smoke check or a debugging session can inspect
    what really goes to the model instead of reconstructing it and being wrong
    about the reconstruction — which is how a `REFERENCES:` block full of local
    file paths survived a whole build day.
    """
    return _prompt_for(character_ref(companion), scene)


def _prompt_for(reference: CharacterRef, scene: str) -> str:
    """Assemble the prompt for an already-resolved character.

    `include_references` is the selected provider's own capability flag and
    never a constant: an anchor URL reaches a model only if that model can
    consume one. No implemented provider can, so today this is descriptor-only
    consistency and the prompt says nothing about a reference at all.
    """
    return build_illustration_prompt(
        scene,
        [reference],
        include_references=image_adapters.configured_supports_image_reference(),
    )


def render_scene(child: Child, scene: str, references: Sequence[CharacterRef]) -> Media:
    """Render one illustration of `scene` with this cast, cache-first (§16.6).

    **The one place a cast-bearing illustration is produced.** The reference set,
    the dev commands and every story beat come through here, so the style tokens,
    the character signature and the cache key are computed identically every time
    — and that identity is the entire cache. A beat that happens to want the same
    characters in the same scene as an earlier one costs nothing.

    Takes a cast rather than a companion because from Day 6 a beat has one: the
    companion plus whichever recurring characters are present (§8.2). Ordering is
    the caller's convenience only — `build_illustration_prompt` and
    `character_signature` both sort, so passing the same cast in a different order
    is the same picture and the same key rather than a second render of it.

    The seed is derived from the *combined* character hash, so a scene with Pip
    and Bramble in it asks the model for a stable starting point of its own rather
    than borrowing Pip's. It reaches only providers that declare `supports_seed`,
    which today is none of them (§5.8).
    """
    prompt = build_illustration_prompt(
        scene,
        references,
        include_references=image_adapters.configured_supports_image_reference(),
    )
    style, character, scene_hash = hashes_for(
        style_signature(),
        character_signature(references),
        scene,
    )
    return media_service.get_or_generate_image(
        child=child,
        style_hash=style,
        character_hash=character,
        scene_hash=scene_hash,
        prompt=prompt,
        seed=media_service.seed_from(character),
    )


def illustrate(companion: Companion, scene: str) -> Media:
    """Render one illustration of this companion alone in `scene`, cache-first.

    The companion-only case of `render_scene`, which is what the reference set and
    `render_companion` want. A story beat calls `render_scene` directly with its
    whole cast.
    """
    return render_scene(companion.child, scene, [character_ref(companion)])


def ensure_reference_set(companion: Companion, *, labels: tuple[str, ...] = ()) -> dict[str, str]:
    """Generate the portrait and canonical expressions, once (§9.6).

    Idempotent: anything already in the manifest is skipped, and the cache
    catches anything the manifest lost. One failed expression does not abandon
    the rest — a companion with five of six references is still a companion.
    """
    ensure_visual_descriptor(companion)

    for label in labels or REFERENCE_ORDER:
        if _media_for(companion, label):
            continue
        try:
            media = illustrate(companion, REFERENCE_SCENES[label])
        except MediaError:
            logger.exception(
                "companion.reference_failed companion=%s label=%s", companion.id, label
            )
            continue
        _attach(companion, label, media)

    return dict(companion.reference_manifest)


def ensure_greeting_audio(companion: Companion) -> Media | None:
    """Synthesise the companion's spoken hello, once (§4.3)."""
    if existing := _media_for(companion, GREETING_AUDIO):
        return existing

    text = greeting_text(companion.name, companion.child.first_name)
    try:
        media = media_service.get_or_generate_audio(
            child=companion.child,
            text=text,
            voice_id=companion.voice_id or "",
        )
    except MediaError:
        logger.exception("companion.greeting_failed companion=%s", companion.id)
        return None

    _attach(companion, GREETING_AUDIO, media)
    return media


def build_assets(companion: Companion) -> Companion:
    """The whole one-time build: descriptor, reference set, voice.

    Safe to run again at any time — every step checks first. The status it
    leaves behind is what the greeting scene reads.
    """
    ensure_reference_set(companion)
    ensure_greeting_audio(companion)

    expected = len(REFERENCE_ORDER) + 1
    have = len(companion.reference_manifest)
    if have >= expected:
        status = AssetStatus.READY
    elif companion.assets_ready:
        # Portrait and voice exist: the child can meet their friend today, and
        # the missing expressions are worth another attempt later.
        status = AssetStatus.PARTIAL
    else:
        status = AssetStatus.FAILED

    companion.assets_status = status
    companion.save(update_fields=["assets_status", "updated_at"])
    logger.info(
        "companion.assets companion=%s status=%s have=%d/%d",
        companion.id,
        status,
        have,
        expected,
    )
    return companion


def greeting_line(companion: Companion) -> str:
    """The text of the hello, for captions (§14.6) and for tests."""
    return greeting_text(companion.name, companion.child.first_name)


def _media_for(companion: Companion, label: str) -> Media | None:
    media_id = companion.reference_manifest.get(label)
    if not media_id:
        return None
    return Media.objects.filter(id=media_id).first()


@transaction.atomic
def _attach(companion: Companion, label: str, media: Media) -> None:
    """Record one asset under its label, keeping both reference fields in step.

    `reference_asset_ids` is §17.2's flat array and `reference_manifest` is the
    labelled view; they are written together here, and nowhere else, so they
    cannot drift.
    """
    locked = Companion.objects.select_for_update().get(pk=companion.pk)
    manifest = dict(locked.reference_manifest)
    manifest[label] = str(media.id)

    locked.reference_manifest = manifest
    locked.reference_asset_ids = list(manifest.values())
    locked.save(update_fields=["reference_manifest", "reference_asset_ids", "updated_at"])

    companion.reference_manifest = manifest
    companion.reference_asset_ids = list(locked.reference_asset_ids)
