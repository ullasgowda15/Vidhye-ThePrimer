"""Short-term memory — the current sitting, in Redis (PRODUCT.md §11.1, §11.2).

> *"Short-term memory (STM): the current session — every beat, choice, reaction,
> retry. Held in Redis + session row. Ephemeral working memory."*

This is working memory, and it is genuinely disposable. It holds the ordered log
of what happened in one sitting so that the consolidation job has something
richer to score than the persisted rows alone — the timings, the retries, the
things the child said, the order they happened in — and then it is thrown away.
Only the salient survives (§11.2), and surviving means becoming a `Memory` row.

**Why Redis and not just the database.** The signals here are per-tap and
high-frequency, and most of them are worth nothing an hour later. Writing them to
Postgres would mean a table that grows with every tap, needs a retention job of
its own, and is indexed for a query nobody makes — while what we actually need is
an append-only list with a TTL, which is the one data structure Redis is. §13.8's
"short retention on raw child behavioural events" is a *property of the store*
here rather than a cron job we have to remember to write.

**Nothing in this module may ever raise into a caller.** It is called from the
beat path and from the choice endpoint, both of which are between a child and
their story. A Redis restart, a full instance, a wrong URL: each of those must
cost us a *signal* and never a beat (§4.9). So every operation is wrapped, every
failure is logged once and swallowed, and the return value says whether it
worked for callers that care. The consolidation job is written to work from an
empty STM for exactly this reason — the persisted beats and choices are the
durable floor, and STM is the enrichment on top.

**The session row is the mirror, not a duplicate.** §11.1 says "Redis + session
row", and the split is deliberate: Redis holds the full ordered log, and
`PlaySession.stm_digest` holds a small, bounded summary of the parts we would be
sorry to lose — the counters, and the child-*initiated* moments, which are the
highest-salience candidates there are (§11.4). If Redis has expired, restarted or
never been reachable, consolidation still knows the child named something and
what they named.

**No plaintext beyond what the child themselves typed.** Narration is not stored
here — only its first clause, as an anchor for the consolidation prompt — because
STM would otherwise become a second, unpolicied copy of every story (§13.8).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: One Redis list per sitting. Namespaced because the same instance is the Celery
#: broker (`REDIS_URL`), and an unprefixed key would sit alongside Celery's own.
KEY_PREFIX = "primer:stm:"

#: Event names. Constants rather than literals because the consolidation job
#: dispatches on them and a typo would silently drop a whole class of signal.
BEAT = "beat"
CHOICE = "choice"
SESSION_END = "session_end"

#: The longest one recorded string may be. STM is not a transcript store: an
#: anchor clause and a child's short answer are what the salience scorer reads,
#: and a cap here is what stops a runaway narration from making it one.
MAX_TEXT_CHARS = 240

#: How many child-initiated highlights the session-row mirror keeps. Small on
#: purpose — the mirror is a floor under a Redis outage, not a second log — and
#: bounded because it is a JSON column on a row read by the parent dashboard.
MAX_DIGEST_HIGHLIGHTS = 12


def key_for(session_id: Any) -> str:
    return f"{KEY_PREFIX}{session_id}"


def _client() -> Any:
    """A Redis client, or `None` if one cannot be made.

    Built per call rather than cached at module level. redis-py maintains its own
    connection pool, so this is cheap, and a module-level client would freeze
    `REDIS_URL` at import time — which is exactly what makes a settings override
    in a test do nothing (the same reasoning as `classifier._router`).
    """
    try:
        import redis

        return redis.Redis.from_url(
            settings.REDIS_URL,
            # Short, and short on purpose: this call sits on the beat path. A
            # Redis that is merely slow must not become latency a child feels —
            # losing the signal is strictly better than delaying the story.
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - import/URL errors only
        logger.warning("memory.stm_client_unavailable error=%s", exc)
        return None


def record(session_id: Any, event: str, **fields: Any) -> bool:
    """Append one event to this sitting's log. Returns whether it was stored.

    Never raises. See the module docstring: a signal is not worth a beat.
    """
    if session_id is None:
        return False

    client = _client()
    if client is None:
        return False

    payload = {"event": event, **{k: _bounded(v) for k, v in fields.items() if v is not None}}
    key = key_for(session_id)
    try:
        pipeline = client.pipeline()
        pipeline.rpush(key, json.dumps(payload, separators=(",", ":")))
        # A ceiling on the list, applied on every write. A client stuck in a
        # submit loop would otherwise grow one key without bound, and the oldest
        # events are the right ones to drop: the mirror on the session row keeps
        # the child-initiated ones whatever happens here.
        pipeline.ltrim(key, -settings.MEMORY_STM_MAX_EVENTS, -1)
        # Refreshed on every write, so the TTL measures time since the child last
        # did something rather than since the sitting opened — a long sitting must
        # not have its own beginning expire out from under it.
        pipeline.expire(key, settings.MEMORY_STM_TTL_S)
        pipeline.execute()
        return True
    except Exception as exc:
        # `except Exception`, not `redis.RedisError`, and the breadth is the
        # point: this is on a child's path, and it is not this module's job to be
        # the reason a story stopped.
        logger.warning(
            "memory.stm_write_failed session=%s event=%s error=%s", session_id, event, exc
        )
        return False


def events(session_id: Any) -> list[dict[str, Any]]:
    """The sitting's log, oldest first. Empty when there is nothing (or no Redis).

    An empty list is not an error and callers must not treat it as one:
    consolidation works from the persisted beats and choices and treats STM as
    enrichment (§11.2).
    """
    if session_id is None:
        return []

    client = _client()
    if client is None:
        return []

    try:
        raw = client.lrange(key_for(session_id), 0, -1)
    except Exception as exc:
        logger.warning("memory.stm_read_failed session=%s error=%s", session_id, exc)
        return []

    parsed: list[dict[str, Any]] = []
    for item in raw:
        try:
            decoded = json.loads(item)
        except (TypeError, ValueError):
            # One unreadable entry must not discard the rest of the sitting.
            logger.warning("memory.stm_entry_unreadable session=%s", session_id)
            continue
        if isinstance(decoded, dict):
            parsed.append(decoded)
    return parsed


def discard(session_id: Any) -> bool:
    """Forget the sitting. Called once consolidation has decided what to keep.

    This is the second half of §11.2 and not merely housekeeping: STM is
    "discarded after consolidation", so the working copy of a child's session
    stops existing the moment the durable, salience-filtered version does. The
    TTL is the backstop for a consolidation that never ran, not the mechanism.
    """
    if session_id is None:
        return False

    client = _client()
    if client is None:
        return False

    try:
        client.delete(key_for(session_id))
        return True
    except Exception as exc:
        logger.warning("memory.stm_discard_failed session=%s error=%s", session_id, exc)
        return False


def _bounded(value: Any) -> Any:
    """Strings clipped, everything else passed through as JSON can carry it."""
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    return value


# ---------------------------------------------------------------------------
# The session-row mirror (§11.1's "Redis + session row")
# ---------------------------------------------------------------------------
def mirror(session: Any, event: str, *, highlight: str = "", **counters: Any) -> None:
    """Fold one event into `PlaySession.stm_digest`. Never raises.

    Two kinds of thing go in, and the distinction is §11.4's:

    * **counters** — how many beats, how many retries, how much time the child
      spent deciding. Cheap, bounded, and the inputs to the "repetition" and
      "skill milestone" salience factors.
    * **highlights** — the child-*initiated* moments: what they named, what they
      said, what they chose. These weigh heaviest in salience scoring and are
      therefore the ones a Redis outage must not be able to lose.

    Written with an `UPDATE … SET` on one JSON column rather than through the
    ORM's normal save, so that two workers folding events for one sitting cannot
    clobber each other's counters — the read-modify-write happens under a row
    lock. This is a session row being touched a few times a minute, so the lock
    is uncontended in practice.
    """
    if session is None:
        return

    from django.db import transaction

    from apps.story.models import PlaySession

    try:
        with transaction.atomic():
            locked = PlaySession.objects.select_for_update().filter(pk=session.pk).first()
            if locked is None:
                return

            digest = dict(locked.stm_digest or {})
            counts = dict(digest.get("counts") or {})
            counts[event] = int(counts.get(event, 0)) + 1
            for name, amount in counters.items():
                if amount is None:
                    continue
                counts[name] = int(counts.get(name, 0)) + int(amount)
            digest["counts"] = counts

            if highlight:
                highlights = list(digest.get("highlights") or [])
                clipped = highlight[:MAX_TEXT_CHARS]
                if clipped not in highlights:
                    highlights.append(clipped)
                # Oldest dropped rather than newest refused: a child who names
                # three things in one sitting has done the most interesting thing
                # they will do, and it happens late as often as early.
                digest["highlights"] = highlights[-MAX_DIGEST_HIGHLIGHTS:]

            locked.stm_digest = digest
            locked.save(update_fields=["stm_digest", "updated_at"])
    except Exception as exc:
        # Same rule as the Redis half: a mirror that cannot be written is a lost
        # signal, never a lost beat.
        logger.warning(
            "memory.stm_mirror_failed session=%s error=%s", getattr(session, "pk", None), exc
        )
