"""Who is in this picture (PRODUCT.md §8.2, §9.6, §5.8).

The illustration pipeline has always known how to draw *a* character
consistently: `media.prompts.build_illustration_prompt` takes a cast and emits
their canonical descriptors, and `companion.service.illustrate` has passed it the
companion since Day 3. What was missing is everything this module supplies —
**which** characters a given beat contains, and where a descriptor comes from for
one the child has never met before.

Three rules, and each of them is the answer to a way a child's world stops
holding together:

**Everyone present is described.** A beat naming a frog got the companion's
descriptor and nothing else, so the frog was redrawn from the illustration prompt
every time and was a different frog in every picture (§8.2's "inconsistency is
uncanny and breaks attachment instantly"). Now every character in
`scene.characters_present` that we hold a descriptor for is injected.

**A character is written down the first time and never re-invented.** The beat
that introduces one supplies its appearance (`ai.schemas.CastMember`) and it is
persisted here, immediately, as a `Character` row. Its *second* appearance reads
that row back rather than asking a model to remember — which it cannot.

**The companion's face comes from the five we pre-rendered.** Where a beat's
emotion matches a canonical expression (§9.6), the companion carries that
expression's cue and its already-rendered asset as the anchor, instead of leaving
the face to whatever the scene prose implied.

What is deliberately *not* here: any notion of salience, recall or a relationship
graph. That is §11 and Days 8-9. This module only ever answers "who is in this
beat", from the beat itself.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.companion.models import Character, Companion
from apps.companion.scenes import EXPRESSION_CUES, canonical_expression
from apps.media.prompts import CharacterRef

if TYPE_CHECKING:
    from apps.media.models import Media
    from apps.profiles.models import Child

logger = logging.getLogger(__name__)

#: How many *non-companion* characters may reach one illustration prompt.
#:
#: A bound rather than a preference. Every extra cast member is another paragraph
#: of descriptor in the prompt and another term in `character_hash`, so an
#: unbounded cast costs prompt size, dilutes the subject of the picture, and
#: fragments the cache into keys that will never be hit twice. Three is already
#: more characters than a picture-book spread for this age puts in one frame, and
#: the ones dropped are the ones the beat listed last.
MAX_CAST = 3


def character_key(raw: str) -> str:
    """The stable id a name or a generated key is stored and matched under.

    Both sides of the join go through here — what a beat put in
    `characters_present` and what it put in `new_characters[].key` — because they
    are written by a model in the same call and are *not* reliably identical.
    "Bramble", "bramble" and "the bramble" have to land on one row or the second
    appearance of a character silently becomes a first appearance again, which is
    the exact defect this file exists to prevent.
    """
    return slugify(raw).replace("-", "_")[:64]


def companion_ref(companion: Companion, *, emotion: str = "") -> CharacterRef:
    """The companion, wearing the canonical expression this beat calls for (§9.6).

    Falls back to the plain portrait-anchored reference when the emotion matches
    none of the five faces we hold, which is the common case and not a
    degradation: an expression clause we did not pre-render is a face we cannot
    promise is the same one next time.

    The anchor URL follows the expression rather than staying on the portrait, so
    that on the day an image-conditioning provider is wired (`adapters.image`,
    `supports_image_reference`) the beat is anchored to the *proud* Pip we already
    have on disk rather than to the neutral one. Today no implemented provider can
    consume it and consistency rests on the descriptor and the cue, honestly.
    """
    from apps.companion import service as companion_service

    reference = companion_service.character_ref(companion)
    label = canonical_expression(emotion)
    if label is None:
        return reference

    anchor = companion_service.reference_url(companion, label) or reference.reference_url
    return CharacterRef(
        key=reference.key,
        name=reference.name,
        descriptor=reference.descriptor,
        reference_url=anchor,
        expression=EXPRESSION_CUES[label],
    )


def remember(
    child: Child,
    new_characters: Iterable[Mapping[str, Any]],
    *,
    adventure_id: Any = None,
) -> list[Character]:
    """Persist the characters a beat has just introduced. Idempotent.

    Called from inside the beat's own transaction (`orchestrator._persist_beat`)
    so a character can never outlive the beat that invented it, nor the reverse.

    A key we already hold is **left exactly as it was**. That is the whole
    mechanism: the second beat to mention Bramble will describe Bramble again,
    slightly differently, because a language model asked twice answers twice —
    and taking the newer answer would redraw the character every time it
    reappeared, which is the failure the row was created to stop. First
    description wins, forever.
    """
    remembered: list[Character] = []
    for entry in new_characters or []:
        key = character_key(str(entry.get("key") or entry.get("name") or ""))
        descriptor = str(entry.get("visual_descriptor") or "").strip()
        name = str(entry.get("name") or "").strip() or key.replace("_", " ")
        if not key or not descriptor:
            # A character with no id or no appearance is one nothing downstream
            # could draw consistently. Dropped rather than stored half-formed.
            logger.info("cast.character_incomplete child=%s key=%r", child.id, key)
            continue

        try:
            with transaction.atomic():
                character, created = Character.objects.get_or_create(
                    child=child,
                    key=key,
                    defaults={
                        "name": name[:80],
                        "visual_descriptor": descriptor,
                        "first_seen_adventure_id": adventure_id,
                    },
                )
        except IntegrityError:
            # Two beats of the same adventure introducing one character at once.
            # The constraint settles it and the earlier row is the one of record.
            winner = Character.objects.filter(child=child, key=key).first()
            if winner is None:
                # The constraint that fired was not `character_child_key_uniq`.
                # Something else is wrong and a silent nothing would hide it.
                raise
            character, created = winner, False

        if created:
            logger.info(
                "cast.character_remembered child=%s key=%s name=%s adventure=%s",
                child.id,
                key,
                character.name,
                adventure_id,
            )
        remembered.append(character)
    return remembered


def characters_for(child: Child, present: Sequence[str]) -> list[Character]:
    """The persisted rows for the keys a beat named, in the beat's own order.

    Keys we hold no row for are simply absent from the result — a beat may name a
    character it never introduced, and this is a lookup rather than an assertion.
    """
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in present:
        key = character_key(str(raw))
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)
    if not wanted:
        return []

    rows = {row.key: row for row in Character.objects.filter(child=child, key__in=set(wanted))}
    return [row for key in wanted if (row := rows.get(key)) is not None]


def refs_for(
    child: Child,
    *,
    companion: Companion | None,
    present: Sequence[str],
    emotion: str = "",
) -> list[CharacterRef]:
    """Every character in this beat, as the illustration pipeline needs them.

    The companion first and always — they are in every picture of their own
    story, and a beat whose `characters_present` forgot to list them (which
    happens) must not silently produce a picture of an empty forest.

    Then each key the beat named that we hold a row for, in the order the beat
    named them, capped at `MAX_CAST`. A key we have never seen is skipped in
    silence rather than described: it is a character the beat mentioned without
    introducing, and inventing a descriptor for it here would be inventing the
    character.
    """
    refs: list[CharacterRef] = []
    seen: set[str] = set()

    if companion is not None:
        reference = companion_ref(companion, emotion=emotion)
        refs.append(reference)
        seen.add(reference.key)
        # The companion is named in `characters_present` too, under their own
        # name; skip that entry rather than looking for a `Character` row that
        # by construction does not exist.
        seen.add(character_key(companion.name))

    members = 0
    for row in characters_for(child, present):
        # The companion is named in `characters_present` too, under their own
        # name; `seen` already carries that key, so this skips the row rather than
        # describing the companion twice.
        if row.key in seen or not row.visual_descriptor:
            continue
        seen.add(row.key)
        if members >= MAX_CAST:
            logger.info("cast.trimmed child=%s dropped=%s", child.id, row.key)
            continue
        members += 1
        refs.append(
            CharacterRef(
                key=f"character:{row.id}",
                name=row.name,
                descriptor=row.visual_descriptor,
                reference_url=_first_reference_url(row),
            )
        )
    return refs


def record_reference(characters: Iterable[Character], media: Media) -> None:
    """Note the picture that established a character's look (§8.2, §17.2).

    Only for a character that has none yet, and only ever the first: the anchor
    exists to *hold* the look the first render settled on, so replacing it with a
    later picture would let the character drift one beat at a time — the slow
    version of exactly what having an anchor is for.

    A beat illustration rather than a purpose-rendered portrait, because a portrait
    per passing frog is a provider call per passing frog (§22.4) and this picture
    is already paid for.
    """
    for character in characters:
        if character.reference_asset_ids:
            continue
        character.reference_asset_ids = [media.id]
        character.save(update_fields=["reference_asset_ids", "updated_at"])
        logger.info("cast.reference_recorded character=%s media=%s", character.id, media.id)


def _first_reference_url(character: Character) -> str | None:
    from apps.media.models import Media

    if not character.reference_asset_ids:
        return None
    media = Media.objects.filter(id=character.reference_asset_ids[0]).first()
    return media.url if media is not None and media.is_servable else None
