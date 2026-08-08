"""The beat stream, as it goes on the wire (PRODUCT.md §18.5, §16.4).

Server-Sent Events rather than WebSockets: the traffic is one-directional (the
child's choices go back over an ordinary POST), SSE reconnects by itself, and it
survives every proxy and captive-portal a family's wifi will put in front of it.
Channels is available if voice input later needs a duplex channel.

Three properties this file exists to guarantee:

**Narration first.** The `beat` event is emitted the moment the text validates,
with `scene.status: "pending"`. The picture and the voice follow as separate
`media` events as they resolve. The child starts hearing their story while the
image model is still drawing (§18.5's design note).

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
from collections.abc import AsyncIterator
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


def frame(event: str, data: dict[str, Any]) -> bytes:
    """One SSE frame. `separators` keeps frames compact and byte-stable."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def beat_payload(
    beat: Beat,
    *,
    image_url: str | None = None,
    audio_url: str | None = None,
) -> dict[str, Any]:
    """The `beat` event body (§18.5).

    The two media URLs are **passed in** rather than read off `beat.media` /
    `beat.audio`. Those are lazy foreign keys, and following one from inside an
    async generator is a `SynchronousOnlyOperation` waiting to happen the first
    time this is called for a beat that already has its assets. Taking them as
    arguments makes that impossible instead of merely unlikely.

    `scene.status` is `pending` when the illustration has not arrived, which is
    almost always the case at the moment this event is sent. It is a status rather
    than an absent key, so the client renders a known state rather than inferring
    one from a missing field.
    """
    return {
        "beatId": str(beat.id),
        "seq": beat.seq,
        "narration": beat.narration,
        "scene": {
            "imageUrl": image_url,
            "status": "ready" if image_url else "pending",
        },
        "audioUrl": audio_url,
        "interaction": beat.interaction,
        "emotion": beat.emotion,
    }


def media_payload(beat_id: str, stage: str, url: str) -> dict[str, Any]:
    """The `media` event body — one asset, keyed by the beat it belongs to.

    A distinct event rather than a second `beat`: from Day 5 the stream carries
    several different beats, and a client that had to tell "a new beat" from "an
    update to a beat" by comparing ids is a client that will get it wrong.
    """
    if stage == "illustration":
        return {"beatId": beat_id, "scene": {"imageUrl": url, "status": "ready"}}
    return {"beatId": beat_id, "audioUrl": url}


async def stream_first_beat(adventure: Adventure) -> AsyncIterator[bytes]:
    """Stream beat one, media and all — generating it only if it does not exist.

    Replay matters as much as generation: a child who reloads mid-story lands back
    in the same forest rather than paying for a new one (§14.5).

    Day 5 turns this into a loop over choices. Today it is deliberately one beat
    then `awaiting_choice` — the status the client is left in is already the right
    one, so the loop is an extension rather than a rewrite.
    """
    yield OPEN_FRAME

    try:
        beat, image_url, audio_url = await orchestrator.ensure_first_beat(adventure)
    except AIError:
        # Logged with the adventure so an operator can find the prompt; nothing
        # about it reaches the child (§13).
        logger.exception("story.stream_failed adventure=%s stage=beat", adventure.id)
        yield frame("done", {"status": "ended"})
        return

    yield frame("beat", beat_payload(beat, image_url=image_url, audio_url=audio_url))

    # A replayed beat that already has both assets is complete; re-running the
    # media stages would only re-read the cache and re-emit URLs the client just
    # received in the `beat` frame.
    if not (image_url and audio_url):
        async for stage, media in orchestrator.stream_media(beat):
            if media is None:
                continue
            yield frame("media", media_payload(str(beat.id), stage, media.url))

    yield frame("done", {"status": "awaiting_choice"})
