"""Remember which media providers have run out, so we stop asking (§16.6).

A vendor whose quota is spent answers in milliseconds, and it answers *no* — but
that round trip is on the child's critical path, and it is paid again on the very
next call because nothing remembers the last one. ElevenLabs answering
`quota_exceeded` for every line of a sitting was measured at **21 seconds** of
dead time in the Day-6 review, on live keys, before the failover chain got the
child a voice. The image side is worse per beat: an illustration plus one icon per
choice card is three or four refusals where one would have done.

So the first refusal is recorded, and every later call inside the cooldown skips
that provider without opening a socket. This is not a cost control — a 429 is
free. It is latency, and latency here is a pre-reader looking at a screen.

**Three things this gets right that a naive "burned today" flag does not.**

*Reset windows differ per vendor.* Cloudflare Workers AI is a **daily**
allocation; ElevenLabs is a **monthly** credit balance; Google TTS is a monthly
free tier. One global "resets at midnight" would retry ElevenLabs every morning
for three weeks after it actually ran out, which is the same wasted round trip
this module exists to remove.

*Exhausted and rate-limited are both HTTP 429.* A burst limit means "try again in
ten seconds"; a daily allocation means "try again tomorrow". Tripping a day-long
cooldown on a burst limit would take a **healthy** provider offline for hours —
strictly worse than the problem being solved — so the two are told apart by what
the vendor actually said, and anything unrecognised gets the short cooldown.

*Nothing is ever blocked outright.* The cooldown expires on its own, and Redis
being unreachable fails **open**: an unknown quota state means we try the
provider, because the cost of being wrong that way is one wasted call, while the
other way is a provider silently switched off for as long as the cache is down.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: Namespaced like `analytics.budget`'s counter, and for the same reason: two
#: unrelated features sharing one Redis must not be able to collide.
KEY_PREFIX = "primer:quota:"

#: How long a provider stays skipped after saying it is out, per vendor, in
#: seconds. Sized from what each one actually meters, and deliberately a little
#: **short** of the true reset in every case: waking early costs one wasted call,
#: while waking late is a provider we owned and did not use.
#:
#: The default is one hour rather than one day, because an unrecognised provider
#: is one whose reset window we have not checked.
COOLDOWN_S: dict[str, int] = {
    # Daily neuron allocation, resets 00:00 UTC. Six hours means a run that
    # exhausts it in the morning is retried the same evening rather than tomorrow.
    "cloudflare": 6 * 3600,
    # Monthly credit balance. A day is already generous: the balance will not come
    # back on its own before the billing date, and this is the interval at which we
    # are willing to spend one call finding out whether it was topped up.
    "elevenlabs": 24 * 3600,
    # Monthly free character tier.
    "google": 24 * 3600,
}
DEFAULT_COOLDOWN_S = 3600

#: What a 429 means when we cannot tell. Long enough to clear a burst limit,
#: short enough that a misread quota message costs minutes rather than a day.
UNKNOWN_LIMIT_COOLDOWN_S = 300

#: The phrases each vendor uses when the allowance itself is gone, as opposed to
#: the requests-per-minute limit. Matched case-insensitively against the error
#: text the adapter raised, which is where the response body ends up.
#:
#: Kept literal and short on purpose. A clever matcher that guessed would trip the
#: long cooldown on an ordinary burst limit, and taking a working provider offline
#: for six hours is a worse outcome than the wasted call this module removes.
_EXHAUSTED_PATTERNS = re.compile(
    "|".join(
        (
            # Cloudflare Workers AI. `\w+\s+` because the real string is "your
            # daily **free** allocation of 10,000 neurons" — this was written from
            # a paraphrase in a build note rather than from the vendor, matched
            # nothing, and quietly gave an exhausted account the five-minute
            # cooldown meant for a burst limit. Written from the wire now.
            r"daily\s+(?:\w+\s+)?allocation",
            r"quota[_ ]exceeded",  # ElevenLabs
            r"quota exceeded",
            r"out of credits",
            r"insufficient[_ ]quota",  # OpenAI-compatible gateways
            r"billing hard limit",
            # `RESOURCE_EXHAUSTED` is deliberately absent. It reads like the
            # strongest possible signal and is in fact Vertex's *generic* 429: the
            # per-minute request limit on an image model returns it, and so does a
            # spent daily allowance. Matching it cost a working provider a
            # 24-hour cooldown the first time a burst of choice icons went out
            # together — three images in ten seconds, one rate-limited, Gemini
            # benched until tomorrow. Ambiguous means the short cooldown; that is
            # the whole point of having two.
        )
    ),
    re.I,
)


def _client() -> Any:
    """A Redis client, or `None`.

    Built per call, and with the same short timeouts as `analytics.budget._client`
    for the same reason: this sits in front of every generation, and a slow Redis
    must not become latency a child feels.
    """
    try:
        import redis

        return redis.Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - import/URL errors only
        logger.warning("quota.client_unavailable error=%s", exc)
        return None


def _key(provider: str) -> str:
    return f"{KEY_PREFIX}{provider}"


def looks_exhausted(error: BaseException | str) -> bool:
    """True when this failure says the *allowance* is gone, not the rate.

    Read from the message because that is where every adapter puts the response
    body (`MediaProviderError` carries the vendor's JSON verbatim), and because
    the status code alone cannot answer it — 429 is both.
    """
    return bool(_EXHAUSTED_PATTERNS.search(str(error)))


def cooldown_for(provider: str, error: BaseException | str) -> int:
    """How long to skip `provider`, given what it just said."""
    if looks_exhausted(error):
        return COOLDOWN_S.get(provider, DEFAULT_COOLDOWN_S)
    return UNKNOWN_LIMIT_COOLDOWN_S


def should_record(error: BaseException | str) -> bool:
    """Whether this failure is the vendor's to answer for.

    A `MediaConfigurationError` is **ours** — an unset key, a voice that does not
    resolve — and resting a provider for it would be the wrong lesson twice over:
    the cooldown would not fix anything, and a deployment that briefly lost a key
    would go on skipping a healthy vendor for a day after the key came back.

    Caught in practice before it ever shipped: the suite blanks every credential,
    so the first run after this module existed wrote a 24-hour ElevenLabs cooldown
    into the developer's own Redis on the strength of "no API key".
    """
    from apps.media.exceptions import MediaConfigurationError

    return not isinstance(error, MediaConfigurationError)


def mark_exhausted(provider: str, error: BaseException | str) -> int:
    """Record that `provider` is out. Returns the cooldown in seconds (0 if not
    recorded).

    Never raises: this is called from an exception handler on the child's path,
    and a cache write failing there must not replace the vendor's error with one
    of ours.
    """
    if not should_record(error):
        logger.info("quota.not_recorded provider=%s reason=configuration", provider)
        return 0

    seconds = cooldown_for(provider, error)
    client = _client()
    if client is None:
        return 0

    try:
        client.setex(_key(provider), seconds, str(int(time.time())))
    except Exception as exc:
        logger.warning("quota.mark_failed provider=%s error=%s", provider, exc)
        return 0

    logger.warning(
        "quota.exhausted provider=%s cooldown_s=%d exhausted=%d reason=%r",
        provider,
        seconds,
        int(looks_exhausted(error)),
        str(error)[:200],
    )
    return seconds


def is_exhausted(provider: str) -> bool:
    """True when `provider` is inside a cooldown and should be skipped.

    **Fails open.** No Redis, or a read that errors, means we do not know — and
    not knowing has to mean "try it". The cost of being wrong in that direction is
    one wasted call; the cost of the other direction is a provider switched off
    for as long as the cache is unreachable, which is exactly the outage this
    module was written to prevent, arriving by a different road.
    """
    client = _client()
    if client is None:
        return False
    try:
        return bool(client.exists(_key(provider)))
    except Exception as exc:
        logger.warning("quota.check_failed provider=%s error=%s", provider, exc)
        return False


def clear(provider: str) -> None:
    """Forget a provider's cooldown — a topped-up account, or an operator who
    knows better than the guess above. Used by `manage.py quota`."""
    client = _client()
    if client is None:
        return
    try:
        client.delete(_key(provider))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("quota.clear_failed provider=%s error=%s", provider, exc)


def remaining_s(provider: str) -> int:
    """Seconds left on `provider`'s cooldown; 0 when it is not skipped."""
    client = _client()
    if client is None:
        return 0
    try:
        ttl = int(client.ttl(_key(provider)))
    except Exception:  # pragma: no cover - defensive
        return 0
    return max(0, ttl)
