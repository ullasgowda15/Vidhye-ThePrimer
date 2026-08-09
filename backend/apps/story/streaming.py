"""The beat stream, as it goes on the wire (PRODUCT.md §18.5, §16.4).

Server-Sent Events rather than WebSockets: the traffic is one-directional (the
child's choices go back over an ordinary POST), SSE reconnects by itself, and it
survives every proxy and captive-portal a family's wifi will put in front of it.
Channels is available if voice input later needs a duplex channel.

**One open, one beat.** The stream delivers the beat the child is on and ends;
the child taps, the choice goes back over `POST …/choice`, and the client opens
the stream again for the consequence (§18.6). The alternative — holding the
connection open across the child's thinking time and pushing the next beat down
it — was rejected because a four-year-old's think time is unbounded, a held
connection is a held worker, and the reconnect would still have to exist for the
tablet that goes to sleep mid-decision. Reopening also means every beat inherits
the throttle and the silent token refresh that already guard the first one.

Four properties this file exists to guarantee:

**Narration first.** The `beat` event is emitted the moment the text validates,
with `scene.status: "pending"`. The picture, the voice and each choice card's
icon follow as separate `media` events as they resolve. The child starts hearing
their story while the image model is still drawing (§18.5's design note).

**The stream owns nothing it cannot afford to lose.** The beat and its media are
produced elsewhere — the beat under a lock in the orchestrator, the media by a
Celery task keyed on the beat — and this file only relays them. That is what makes
a disconnect free: the child's tablet going to sleep costs the connection, not the
generation that had already been paid for.

**Nothing is buffered.** Every frame is yielded as it is produced, the response
carries the headers that stop a proxy from collecting them, and the whole thing
runs as an async view under ASGI. Under WSGI this file produces one blob at the
end, which is why there is no WSGI fallback (§16.4).

**The stream always ends, and never with an error.** A generation that cannot be
repaired ends the stream with `done` and nothing else. The child sees the
companion still thinking, which is a scene; they never see a failure (§13). Day 7
replaces the silent ending with a safe fallback beat.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from apps.story import orchestrator
from apps.story.ai.router import AIError

if TYPE_CHECKING:
    from apps.story.models import Adventure, Beat

logger = logging.getLogger(__name__)

#: Headers that make the difference between a stream and a slow response. Django
#: sets `Content-Type`; these stop everything between us and the child from
#: helpfully collecting the frames into one delivery.
STREAM_HEADERS = {
    # `no-transform` alongside `no-cache`: some proxies and mobile carriers
    # recompress or reformat text responses, and doing that to a stream means
    # holding it long enough to rewrite it.
    "Cache-Control": "no-cache, no-transform",
    # nginx buffers proxied responses by default, which turns SSE into a single
    # blob at the end. This is the documented opt-out and is ignored elsewhere.
    "X-Accel-Buffering": "no",
}
# Deliberately *not* set here: `Connection: keep-alive`. It is a hop-by-hop
# header, which ASGI makes the server's business rather than the application's,
# and HTTP/1.1 keeps connections alive by default anyway.

#: An SSE comment. Sent first so the response headers reach the client
#: immediately, before the several seconds of generation that follow — a client
#: that has not seen a byte cannot tell "thinking" from "never connected".
OPEN_FRAME = b": open\n\n"

#: How a stream ends (the `done` frame's `status`).
#:
#: `awaiting_choice` — the child's turn. `complete` — this was the warm close, so
#: the client shows the ending rather than asking for anything (§8.8). `ended` —
#: nothing could be generated; the companion keeps thinking and the child is never
#: told why (§13).
AWAITING_CHOICE = "awaiting_choice"
COMPLETE = "complete"
ENDED = "ended"

#: The assets the child's turn waits for: the picture of the scene and the voice
#: that reads it, including the options, aloud. Everything else — the little
#: picture on each choice card — arrives after `done`, because a card without its
#: icon is still a card and a beat without its question is nothing at all.
ESSENTIAL_STAGES = frozenset(orchestrator.MEDIA_STAGE_LABELS)


def frame(event: str, data: dict[str, Any]) -> bytes:
    """One SSE frame. `separators` keeps frames compact and byte-stable."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def choice_payload(beat: Beat, urls: Mapping[str, str | None]) -> list[dict[str, Any]] | None:
    """The beat's options, each with the picture that goes on its card (§14.4).

    `imageUrl` is null until the icon lands and the client is expected to render
    the card anyway — a choice a pre-reader cannot take because its picture is
    still rendering is a worse failure than a plain card. The label is spoken in
    the same clip as the narration (`orchestrator.spoken_line`), so a card with no
    picture yet is still a card the child has heard described.
    """
    options = beat.options
    if not options:
        return None
    return [
        {
            "key": option.get("key"),
            "label": option.get("label"),
            "imageUrl": urls.get(orchestrator.choice_stage(str(option.get("key")))),
        }
        for option in options
    ]


def interaction_payload(beat: Beat) -> dict[str, Any] | None:
    """The interaction blob as the client's type says it is (`sse.ts` `BeatEvent`).

    The stored blob is whatever shape the beat was written in, and beats written
    before options were illustrated hold bare strings in `choices` (§14.4,
    `models._as_option`). Shipping it verbatim put those strings on the wire under
    a field the client declares as `{key,label,icon_prompt}[]` — nothing reads it
    today, so nothing was broken, but a frame that contradicts its own type is a
    frame the first client to trust it will crash on. That is precisely the bug the
    coercion in `Beat.options` was added to close, re-opened one layer out.

    So the options are replaced with the coerced ones. The top-level `choices`
    array still supersedes this — it is the one carrying each card's picture — and
    remains what the client actually renders.
    """
    if not beat.interaction:
        return beat.interaction
    return {**beat.interaction, "choices": beat.options or None}


def beat_payload(
    beat: Beat,
    *,
    urls: Mapping[str, str | None],
    is_final: bool = False,
) -> dict[str, Any]:
    """The `beat` event body (§18.5).

    The media URLs are **passed in** rather than read off `beat.media` /
    `beat.audio`. Those are lazy foreign keys, and following one from inside an
    async generator is a `SynchronousOnlyOperation` waiting to happen the first
    time this is called for a beat that already has its assets. Taking them as
    arguments makes that impossible instead of merely unlikely.

    `scene.status` is `pending` when the illustration has not arrived, which is
    almost always the case at the moment this event is sent. It is a status rather
    than an absent key, so the client renders a known state rather than inferring
    one from a missing field.

    `isFinal` is on the beat rather than left for the client to work out from
    `seq` against `estimatedBeats`. The warm close is a different screen (§14.3),
    and a client that had to derive which beat it was showing would eventually
    derive it wrong — and put "play again" under a story that had not finished.
    """
    image_url = urls.get(orchestrator.ILLUSTRATION_STAGE)
    return {
        "beatId": str(beat.id),
        "seq": beat.seq,
        "narration": beat.narration,
        "scene": {
            "imageUrl": image_url,
            "status": "ready" if image_url else "pending",
        },
        "audioUrl": urls.get(orchestrator.VOICE_STAGE),
        "interaction": interaction_payload(beat),
        "choices": choice_payload(beat, urls),
        "emotion": beat.emotion,
        "isFinal": is_final,
    }


def media_payload(beat_id: str, stage: str, url: str) -> dict[str, Any]:
    """The `media` event body — one asset, keyed by the beat it belongs to.

    A distinct event rather than a second `beat`: the stream carries a different
    beat on every open, and a client that had to tell "a new beat" from "an update
    to a beat" by comparing ids is a client that will get it wrong.
    """
    if stage == orchestrator.ILLUSTRATION_STAGE:
        return {"beatId": beat_id, "scene": {"imageUrl": url, "status": "ready"}}
    if (key := orchestrator.choice_key_for(stage)) is not None:
        return {"beatId": beat_id, "choice": {"key": key, "imageUrl": url}}
    return {"beatId": beat_id, "audioUrl": url}


async def stream_beat(adventure: Adventure) -> AsyncIterator[bytes]:
    """Stream the beat the child is on, media and all — generating it only if it
    does not exist yet.

    Replay matters as much as generation, and by Day 5 it is the *common* case: a
    child who reloads mid-story lands back in the same forest (§14.5), and a child
    who has just chosen usually finds the consequence already written by the worker
    the choice enqueued (`orchestrator.schedule_advance`). Either way nobody pays
    twice.
    """
    yield OPEN_FRAME

    try:
        beat, urls, is_final = await orchestrator.ensure_current_beat(adventure)
    except AIError:
        # Logged with the adventure so an operator can find the prompt; nothing
        # about it reaches the child (§13).
        logger.exception("story.stream_failed adventure=%s stage=beat", adventure.id)
        yield frame("done", {"status": ENDED})
        return

    yield frame("beat", beat_payload(beat, urls=urls, is_final=is_final))

    # The media is a worker's job (`apps.story.tasks`); this only watches for it,
    # and returns at once for a replayed beat that already carries every asset.
    # Producing it here is what used to make a disconnected client an orphaned,
    # paid-for beat.
    #
    # `done` is emitted the moment the beat's *own* two assets are in, and the
    # choice icons keep arriving after it. That ordering is the whole reason this
    # loop is written out rather than awaited: `done` is what tells the client the
    # child may act, and gating it on three more illustrations would mean one slow
    # icon leaving a four-year-old unable to answer — a spinner in everything but
    # name (§14.5). A card renders without its picture; a child cannot act without
    # the question.
    essential = ESSENTIAL_STAGES & {stage.label for stage in orchestrator.media_stages(beat)}
    landed = {label for label, url in urls.items() if url}
    unblocked = False

    async for stage, url in orchestrator.follow_media(beat, known=urls):
        yield frame("media", media_payload(str(beat.id), stage, url))
        landed.add(stage)
        if not unblocked and essential <= landed:
            async for ending in _hand_over(adventure, beat, is_final=is_final):
                yield ending
            unblocked = True

    if not unblocked:
        # Either the beat was replayed complete (nothing to wait for) or a stage
        # never landed and `follow_media` gave up. Both end the same way: the child
        # gets their turn, with whatever art exists.
        async for ending in _hand_over(adventure, beat, is_final=is_final):
            yield ending


async def _hand_over(adventure: Adventure, beat: Beat, *, is_final: bool) -> AsyncIterator[bytes]:
    """The two frames that give the child their turn, and start the next beat.

    Starting the next beat here rather than earlier is deliberate: the child has
    something to look at and listen to by now, so a language call is no longer in
    front of the media they were actually waiting for.

    `nextBeatReady` says what is true right now — the next beat is written and
    waiting — and is false while it is merely being generated. The client uses it
    to decide nothing important, which is the point: a prefetch the child can
    detect is a prefetch that has become a promise.
    """
    advance = await orchestrator.prepare_next_beat(adventure, after=beat)
    yield frame("prefetch", {"nextBeatReady": advance == orchestrator.ADVANCE_READY})
    yield frame("done", {"status": COMPLETE if is_final else AWAITING_CHOICE})
