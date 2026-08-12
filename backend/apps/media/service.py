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

**Rows are per-child; objects are shared.** Two children who need the same
picture get a row each and one object between them, so a provider call is paid
once and `delete_child_media` can walk one family's rows without touching
another's pictures (§17.5). The object goes when the last row pointing at it
does.

`STATS` counts real provider calls in-process. It is what makes "generating the
same tuple twice calls the provider once" an assertion rather than a claim, and
`render_companion` prints it so a human can see the same thing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction

from apps.media import storage
from apps.media.adapters import image as image_adapters
from apps.media.adapters import tts as tts_adapters
from apps.media.exceptions import MediaBlocked
from apps.media.hashing import hashes_for, sha16
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.safety import scan
from apps.media.style import negative_prompt

if TYPE_CHECKING:
    from django.db.models import QuerySet

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
    """In-process counters for provider spend.

    Counts only what is actually *paid for*: a generation is a provider call, and
    a hit is a call not made. The split by media type exists because the two cost
    wildly different amounts and move independently — an illustration cache that
    has quietly stopped working is invisible in a total that voice hits dominate.

    In-process and therefore per-worker, which is the honest scope: this is what
    makes "the same tuple twice calls the provider once" an assertion a test can
    make and `render_consistency` can print, not a fleet-wide cost metric. Real
    per-adventure cost instrumentation is §22.4 and a later day.
    """

    generated: int = 0
    cache_hits: int = 0
    reused_across_children: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    generated_by_type: dict[str, int] = field(default_factory=dict)
    hits_by_type: dict[str, int] = field(default_factory=dict)

    def record_generation(self, provider: str, media_type: str = "") -> None:
        self.generated += 1
        self.by_provider[provider] = self.by_provider.get(provider, 0) + 1
        if media_type:
            self.generated_by_type[media_type] = self.generated_by_type.get(media_type, 0) + 1

    def record_hit(self, media_type: str = "", *, shared: bool = False) -> None:
        self.cache_hits += 1
        if shared:
            self.reused_across_children += 1
        if media_type:
            self.hits_by_type[media_type] = self.hits_by_type.get(media_type, 0) + 1

    def hit_rate(self, media_type: str = "") -> float:
        """Fraction of requests answered without calling a provider, 0.0-1.0."""
        if media_type:
            hits = self.hits_by_type.get(media_type, 0)
            asked = hits + self.generated_by_type.get(media_type, 0)
        else:
            hits, asked = self.cache_hits, self.cache_hits + self.generated
        return hits / asked if asked else 0.0

    def reset(self) -> None:
        self.generated = 0
        self.cache_hits = 0
        self.reused_across_children = 0
        self.by_provider = {}
        self.generated_by_type = {}
        self.hits_by_type = {}


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
        # Image models bill per picture, not per prompt token (§22.4).
        cost_units={"images": 1},
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

    The key carries the *resolved vendor* voice as well as the semantic one.
    Without it, every unmapped key hashes the same whatever it actually speaks
    as, so changing `ELEVENLABS_VOICE_ID` or editing `ELEVENLABS_VOICE_MAP`
    would leave every companion talking in the old voice forever.

    That resolution is always the **primary** provider's, even when a fallback
    ends up doing the speaking (`tts.synthesize_with_failover`), because the key
    is computed before the lookup and the lookup must not depend on which vendor
    happens to be healthy — a cache whose keys move during an outage is a cache
    that re-pays for every line the moment the outage ends. The consequence is
    that a line first spoken by the fallback stays the fallback's recording: the
    row records who really said it, and a replay of that beat sounds the same as
    the first time the child heard it, which is the property that matters more
    (§9.1). Re-recording it later is a cache-invalidation question, not this
    function's.
    """
    vendor_voice = tts_adapters.configured_voice(voice_id, provider)
    style_hash, character_hash, scene_hash = hashes_for(
        TTS_STYLE_PREFIX, f"voice:{voice_id}@{vendor_voice}", text
    )
    return _get_or_generate(
        child=child,
        media_type=MediaType.AUDIO,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        prompt_hash=sha16(f"{vendor_voice}:{text}"),
        produce=lambda: _generate_audio(text, voice_id, provider),
        # TTS vendors bill per character of the text handed to them. The *length*
        # only — this is a unit count, and the line itself is a child's story
        # (§13.8), which is why nothing about the text reaches the cost row.
        cost_units={"characters": len(text)},
    )


def get_or_generate_stock(
    *,
    child: Child,
    style_hash: str,
    character_hash: str,
    scene_hash: str,
    data: bytes,
    provider_name: str,
) -> Media:
    """Store a pre-vetted asset we already hold the bytes for (§13.2's `IMGFB`).

    The substitution path (`apps.media.stock`). It goes through the same cache,
    the same scan and the same row-creation race handling as a generated asset,
    for two reasons: a stock file that somehow failed the structural scan must be
    withheld exactly like a bad render, and the second time a child needs a
    substitute it should cost a `SELECT` rather than a write.

    `produce` is a closure over bytes we already have, so `STATS` counts it as a
    "generation" — which is correct: it is one asset entering the pipeline, and
    the provider label on the row (`stock`) is what keeps it out of any spend
    figure that matters.
    """
    return _get_or_generate(
        child=child,
        media_type=MediaType.IMAGE,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        prompt_hash=sha16(f"{provider_name}:{scene_hash}"),
        produce=lambda: (data, provider_name),
        # No `cost_units`, and no image count: a stock file was not generated and
        # billing it as a picture would put spend on the safety fallback path,
        # which is the one place a rising cost number must never discourage use.
        # The span is still written — provider `stock`, zero units — so the
        # dashboard can see how often the substitution fired.
    )


def _trace_media_cost(
    *,
    media_type: str,
    provider_name: str,
    units: dict[str, Any],
    started: float,
) -> None:
    """One `GenerationCost` span for a media provider call (§19.9).

    `provider_label` returns `provider` or `provider:model`, which is exactly the
    key `analytics.pricing` looks rates up by, so it is split back into the two
    columns the cost table keeps apart rather than re-derived.

    The operation name is the media type's product word — `illustration`,
    `narration` — because that is what the internal dashboard's per-operation
    table is read in. `media_type` itself (`image`/`audio`) is the *kind*.

    Import-local, like the router's: `apps.analytics` must stay importable without
    the media app and vice versa.
    """
    from apps.analytics import cost
    from apps.analytics.models import CostKind

    provider, _, model = provider_name.partition(":")
    kind = CostKind.IMAGE if media_type == MediaType.IMAGE else CostKind.TTS

    cost.record(
        kind=kind,
        operation=OPERATION_FOR_TYPE.get(media_type, media_type),
        provider=provider,
        model=model,
        units=units,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


#: Media type -> the span name in the cost table. See `_trace_media_cost`.
#: Annotated `str` keys rather than `MediaType`, because `media_type` travels
#: through this module as a plain string.
OPERATION_FOR_TYPE: dict[str, str] = {
    MediaType.IMAGE: "illustration",
    MediaType.AUDIO: "narration",
}


def _get_or_generate(
    *,
    child: Child,
    media_type: str,
    style_hash: str,
    character_hash: str,
    scene_hash: str,
    prompt_hash: str,
    produce: Callable[[], tuple[bytes, str]],
    cost_units: dict[str, Any] | None = None,
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
        STATS.record_hit(media_type)
        logger.info(
            "media.cache_hit scope=child type=%s tuple=%s/%s/%s media=%s",
            media_type,
            style_hash,
            character_hash,
            scene_hash,
            hit.id,
        )
        return hit

    # 2. Another child already paid for exactly these bytes. Point at the same
    #    stored object, but give *this* child their own row rather than handing
    #    back someone else's: rows are what "delete everything about my child"
    #    walks (§17.5). The object is content-addressed and carries no child in
    #    its path, so it belongs to neither family exclusively and outlives
    #    whichever of them leaves first. The saving that matters — the provider
    #    call — is still made.
    if shared := Media.objects.filter(**tuple_filter).first():
        STATS.record_hit(media_type, shared=True)
        logger.info(
            "media.cache_hit scope=global type=%s tuple=%s/%s/%s source=%s",
            media_type,
            style_hash,
            character_hash,
            scene_hash,
            shared.id,
        )
        return _create_row(
            child=child,
            media_type=media_type,
            url=shared.url,
            provider_name=shared.provider,
            prompt_hash=prompt_hash,
            style_hash=style_hash,
            character_hash=character_hash,
            scene_hash=scene_hash,
            safety_status=shared.safety_status,
            size=shared.bytes,
        )

    # 3. Nothing cached: pay for it — and say so (§19.9, §22.4).
    #
    # This is the only line in the media pipeline that spends money, which is why
    # the cost span is written here and not in the two `_generate_*` helpers: a
    # future third media type gets traced by existing here, and a cache hit above
    # writes nothing because a hit costs nothing. `units` is what the vendor bills
    # for — a picture is per image, a spoken line is per character — and
    # `cost_units` is passed down by the caller that knows which.
    started = time.monotonic()
    raw, provider_name = produce()
    STATS.record_generation(provider_name, media_type)
    _trace_media_cost(
        media_type=media_type,
        provider_name=provider_name,
        units=dict(cost_units or {}),
        started=started,
    )

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

    url = storage.put(raw, content_type=verdict.content_type)

    media = _create_row(
        child=child,
        media_type=media_type,
        url=url,
        provider_name=provider_name,
        prompt_hash=prompt_hash,
        style_hash=style_hash,
        character_hash=character_hash,
        scene_hash=scene_hash,
        safety_status=verdict.status,
        size=len(raw),
    )
    if media.url != url:
        # We lost the race described in `_create_row`: another worker's render
        # is the one of record. Our object is not referenced by any row, so it
        # would otherwise sit in the bucket forever.
        purge_objects({url})
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


def _create_row(
    *,
    child: Child,
    media_type: str,
    url: str,
    provider_name: str,
    prompt_hash: str,
    style_hash: str,
    character_hash: str,
    scene_hash: str,
    safety_status: str,
    size: int,
) -> Media:
    """Write the row, or return the row that beat us to it.

    Nothing locks the window between the cache read and this write, so two
    workers can arrive here with the same tuple for the same child. Rather than
    hold a lock across a two-minute provider call, the unique constraint on
    `(child, type, style, character, scene)` settles it and the loser adopts the
    winner's row. One row, one URL, whoever got there first — which is what the
    child sees either way, since both renders answered the same prompt.
    """
    fields = {
        "url": url,
        "provider": provider_name,
        "prompt_hash": prompt_hash,
        "safety_status": safety_status,
        "bytes": size,
    }
    try:
        # `atomic`, because an IntegrityError otherwise poisons an enclosing
        # transaction and every later query in it fails.
        with transaction.atomic():
            return Media.objects.create(
                child=child,
                type=media_type,
                style_hash=style_hash,
                character_hash=character_hash,
                scene_hash=scene_hash,
                **fields,
            )
    except IntegrityError:
        winner = Media.objects.filter(
            child=child,
            type=media_type,
            style_hash=style_hash,
            character_hash=character_hash,
            scene_hash=scene_hash,
        ).first()
        if winner is None:
            # The constraint that fired was not the cache tuple. Do not swallow
            # it — something else is wrong and a silent nothing would hide it.
            raise
        logger.info(
            "media.race_lost type=%s tuple=%s/%s/%s kept=%s",
            media_type,
            style_hash,
            character_hash,
            scene_hash,
            winner.id,
        )
        return winner


def _generate_image(
    prompt: str,
    seed: int | None,
    provider: ImageProvider | None,
) -> tuple[bytes, str]:
    """Call the image model with exactly what it can consume, and no more.

    The §15.9 negatives are applied here rather than passed in, because they are
    global art direction and a safety control (§15.9): no caller gets to forget
    them, and no caller gets to substitute their own. A provider with no
    `negative_prompt` field receives nothing — folding negations into the
    positive prompt is what painted the companion's name into a picture.
    """
    chosen = provider or image_adapters.select_image_provider()
    return (
        chosen.generate(
            prompt,
            negative_prompt=(
                negative_prompt()
                if image_adapters.supports(chosen, "supports_negative_prompt")
                else ""
            ),
            seed=seed if image_adapters.supports(chosen, "supports_seed") else None,
        ),
        image_adapters.provider_label(chosen),
    )


def _generate_audio(
    text: str,
    voice_id: str,
    provider: TTSProvider | None,
) -> tuple[bytes, str]:
    """Speak one line, failing over between vendors rather than going silent.

    The image side has no equivalent and should not: a beat with no picture is
    playable, a beat with no voice is a screen a pre-reader cannot read. An
    injected `provider` is used alone — a test double asking for a specific
    failure must get it, not a fallback.
    """
    if provider is not None:
        return provider.synthesize(text, voice_id), tts_adapters.provider_label(provider)
    return tts_adapters.synthesize_with_failover(text, voice_id)


def seed_from(character_hash: str) -> int:
    """A stable image seed for one character (§5.8).

    Derived from the character hash alone, so every picture of a companion asks
    the model to start from the same point. Reaches only providers that declare
    `supports_seed`, which today is none of the implemented ones — so it is a
    seam, and the descriptor is doing all the work (§8.2).
    """
    return int(character_hash[:8], 16)


def purge_objects(urls: set[str]) -> int:
    """Delete the objects behind `urls` that no surviving `Media` row wants.

    The reference check is the whole function. Objects are content-addressed and
    child-agnostic, so two families can legitimately point at one file; deleting
    it because *a* row went away would blank the other family's storybook.
    """
    if not urls:
        return 0
    still_referenced = set(Media.objects.filter(url__in=urls).values_list("url", flat=True))
    return storage.delete_orphans(urls - still_referenced)


def purge_media(queryset: QuerySet[Media]) -> tuple[int, int]:
    """Delete these rows and the objects they were the last owner of.

    Returns `(rows, objects)`. Row deletion and object deletion are one call
    because the previous shape — rows only — quietly grew a bucket that nothing
    ever emptied.
    """
    urls = set(queryset.values_list("url", flat=True))
    rows, _ = queryset.delete()
    return rows, purge_objects(urls)


def delete_child_media(child: Child) -> tuple[int, int]:
    """Erase every asset held for one child (§13.8, §17.5).

    Rows first, then the objects nothing else points at. A sibling — or another
    family who happened to need the same picture — keeps their own row, and
    their URL keeps working, because the object survives as long as one row
    wants it. That is the property a per-child *prefix* delete could not give.
    """
    rows, objects = purge_media(Media.objects.filter(child=child))
    logger.info("media.child_deleted child=%s rows=%d objects=%d", child.id, rows, objects)
    return rows, objects
