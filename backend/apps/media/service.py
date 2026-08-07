"""The media pipeline (PRODUCT.md §5.8, §16.6, §16.7).

    prompt -> cache lookup -> [image adapter] -> safety scan -> object storage -> Media row

**Cache first, always.** The lookup happens before the provider is constructed,
let alone called. This is not an optimisation bolted onto a working pipeline —
it is the pipeline. Identical `(style, character, scene)` triples are the
common case (a companion's portrait appears in every story; the canonical
expressions are reused forever), and paying an image provider twice for the same
picture is both a cost bug (§22.4) and a consistency bug: two renders of "the
same" prompt are never quite the same picture, and a child notices.

**The scan is before the store, not after.** Bytes that fail are never written
and never get a URL, so there is no window in which an unscanned asset is
reachable (§13.8).

`STATS` counts real provider calls in-process. It is what makes "generating the
same tuple twice calls the provider once" an assertion rather than a claim, and
`render_companion` prints it so a human can see the same thing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.media import storage
from apps.media.adapters import image as image_adapters
from apps.media.adapters import tts as tts_adapters
from apps.media.exceptions import MediaBlocked
from apps.media.hashing import hashes_for, sha16
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.safety import scan

if TYPE_CHECKING:
    from apps.media.adapters.image import ImageProvider
    from apps.media.adapters.tts import TTSProvider
    from apps.profiles.models import Child

logger = logging.getLogger(__name__)

#: What "style" and "character" mean for a spoken line, so audio shares the
#: media table's cache columns instead of needing its own: the TTS model is the
#: style, the voice is the character, and the line is the scene.
TTS_STYLE_PREFIX = "tts"


@dataclass
class _Stats:
    """In-process counters for provider spend."""

    generated: int = 0
    cache_hits: int = 0
    reused_across_children: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)

    def record_generation(self, provider: str) -> None:
        self.generated += 1
        self.by_provider[provider] = self.by_provider.get(provider, 0) + 1

    def reset(self) -> None:
        self.generated = 0
        self.cache_hits = 0
        self.reused_across_children = 0
        self.by_provider = {}


STATS = _Stats()


def get_or_generate_image(
    *,
    child: Child,
    style_hash: str,
    character_hash: str,
    scene_hash: str,
    prompt: str,
    seed: int | None = None,
    provider: ImageProvider | None = None,
) -> Media:
    """Return the illustration for this triple, generating it only if new."""
    return _get_or_generate(
        child=child,
        media_type=MediaType.IMAGE,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        prompt_hash=sha16(prompt),
        produce=lambda: _generate_image(prompt, seed, provider),
    )


def get_or_generate_audio(
    *,
    child: Child,
    text: str,
    voice_id: str,
    provider: TTSProvider | None = None,
) -> Media:
    """Return the spoken line for this `(voice, text)`, synthesising only if new.

    The companion's greeting is said once per child and then played on every
    visit, so this cache is the difference between one TTS call and one per
    session.
    """
    style_hash, character_hash, scene_hash = hashes_for(
        TTS_STYLE_PREFIX, f"voice:{voice_id}", text
    )
    return _get_or_generate(
        child=child,
        media_type=MediaType.AUDIO,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        prompt_hash=sha16(f"{voice_id}:{text}"),
        produce=lambda: _generate_audio(text, voice_id, provider),
    )


def _get_or_generate(
    *,
    child: Child,
    media_type: str,
    style_hash: str,
    character_hash: str,
    scene_hash: str,
    prompt_hash: str,
    produce: Callable[[], tuple[bytes, str]],
) -> Media:
    tuple_filter = {
        "type": media_type,
        "style_hash": style_hash,
        "character_hash": character_hash,
        "scene_hash": scene_hash,
        "safety_status": SafetyStatus.PASSED,
    }

    # 1. This child already has it. The cheapest possible answer: no provider,
    #    no storage, no write.
    if hit := Media.objects.filter(child=child, **tuple_filter).first():
        STATS.cache_hits += 1
        logger.info(
            "media.cache_hit scope=child type=%s tuple=%s/%s/%s media=%s",
            media_type,
            style_hash,
            character_hash,
            scene_hash,
            hit.id,
        )
        return hit

    # 2. Another child already paid for exactly these bytes. Reuse the stored
    #    object, but give *this* child their own row rather than handing back
    #    someone else's: rows are what "delete everything about my child" walks
    #    (§17.5), and one family's deletion must not reach into another's
    #    storybook. The saving that matters — the provider call — is still made.
    if shared := Media.objects.filter(**tuple_filter).first():
        STATS.cache_hits += 1
        STATS.reused_across_children += 1
        logger.info(
            "media.cache_hit scope=global type=%s tuple=%s/%s/%s source=%s",
            media_type,
            style_hash,
            character_hash,
            scene_hash,
            shared.id,
        )
        return Media.objects.create(
            child=child,
            type=media_type,
            url=shared.url,
            provider=shared.provider,
            prompt_hash=prompt_hash,
            style_hash=style_hash,
            character_hash=character_hash,
            scene_hash=scene_hash,
            safety_status=shared.safety_status,
            bytes=shared.bytes,
        )

    # 3. Nothing cached: pay for it.
    raw, provider_name = produce()
    STATS.record_generation(provider_name)

    verdict = scan(raw)
    if not verdict.is_safe:
        # Never stored, never given a URL, never referenced. The caller decides
        # what the child sees instead; it is never an error message (§13).
        logger.error(
            "media.blocked provider=%s type=%s status=%s reason=%s",
            provider_name,
            media_type,
            verdict.status,
            verdict.reason,
        )
        raise MediaBlocked(
            f"{provider_name} returned an asset the safety scan refused "
            f"({verdict.status}: {verdict.reason or 'no reason given'})."
        )

    url = storage.put(str(child.id), raw, content_type=verdict.content_type)

    media = Media.objects.create(
        child=child,
        type=media_type,
        url=url,
        provider=provider_name,
        prompt_hash=prompt_hash,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        safety_status=verdict.status,
        bytes=len(raw),
    )
    logger.info(
        "media.generated provider=%s type=%s bytes=%d tuple=%s/%s/%s media=%s",
        provider_name,
        media_type,
        len(raw),
        style_hash,
        character_hash,
        scene_hash,
        media.id,
    )
    return media


def _generate_image(
    prompt: str,
    seed: int | None,
    provider: ImageProvider | None,
) -> tuple[bytes, str]:
    chosen = provider or image_adapters.select_image_provider()
    return chosen.generate(prompt, seed=seed), image_adapters.provider_label(chosen)


def _generate_audio(
    text: str,
    voice_id: str,
    provider: TTSProvider | None,
) -> tuple[bytes, str]:
    chosen = provider or tts_adapters.select_tts_provider()
    return chosen.synthesize(text, voice_id), tts_adapters.provider_label(chosen)


def seed_from(character_hash: str) -> int:
    """A stable image seed for one character (§5.8).

    Derived from the character hash alone, so every picture of a companion asks
    the model to start from the same point — the cheap half of the consistency
    mechanism, under the descriptor and reference set.
    """
    return int(character_hash[:8], 16)
