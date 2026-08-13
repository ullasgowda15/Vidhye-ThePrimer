"""The event pipeline's write end (PRODUCT.md §5.6, §17.4, §13.8).

One function. Every service in the product calls `track`, nothing calls
`Event.objects.create`, and the reason the indirection exists is that three of
this module's four rules cannot be enforced at a model:

**1. Telemetry never reaches the caller.** `track` catches everything. A child is
mid-story when most of these fire, and §13 forbids a child seeing an error —
"the analytics table was locked" must not become a blank screen where a beat was.
An event that could not be written is logged and lost, which is the correct trade
for a counter. This is also why it is not wrapped in the caller's transaction:
see `_write`.

**2. The name is from a closed set.** A typo in an event name is a metric that
reads zero forever and looks like a product problem. `Event.NAMES` is the set,
`metrics.py` reads the same constants, and an unknown name raises in DEBUG and is
dropped-with-a-log in production — loud where a developer will see it, harmless
where a child is.

**3. `props` carries shape, never content (§13.8).** The rule is data
minimisation on raw child behavioural events, and the failure mode is banal: a
caller passes `narration=beat.narration` because it was handy, and the events
table becomes an unretained archive of what a four-year-old was told. `_clean`
therefore accepts scalars only, truncates strings to `MAX_STRING`, drops
containers, and caps the number of keys. It is deliberately not configurable.

**4. The child is derived from the parent, and both cascade.** An earlier draft
kept a bare `child_key` UUID alongside the FK so a metric could survive the child
being deleted. Two existing invariant tests refused it, and they were right: the
privacy disclosure says the data goes at once, and analytics does not get an
exception to that sentence. See `Event`'s docstring for the full reasoning and
for why `session` is the one FK that does *not* cascade.

The read end is `apps.analytics.metrics`; the drain is
`manage.py drain_events`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.analytics.models import Event

if TYPE_CHECKING:
    import datetime

    from apps.accounts.models import User
    from apps.profiles.models import Child
    from apps.story.models import PlaySession

logger = logging.getLogger(__name__)

#: How much of a string prop survives. Long enough for an identifier, a status, a
#: world key or a reason code; far too short to hold a sentence a child heard.
MAX_STRING = 120

#: How many props one event may carry. An event is a fact with a handful of
#: dimensions; anything wider is a record, and records belong in the tables that
#: own them.
MAX_PROPS = 20


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    """Reduce a caller's kwargs to scalars, truncated (§13.8).

    Returns a new dict. Keys are kept in the order given so the first twenty are
    the ones the caller thought of first, and dropped keys are logged by name —
    a prop that silently vanished is worse than one that was never sent, because
    a dashboard built on it reads empty rather than absent.
    """
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []

    for key, value in props.items():
        if len(cleaned) >= MAX_PROPS:
            dropped.append(key)
            continue
        if value is None or isinstance(value, bool | int | float):
            cleaned[str(key)] = value
        elif isinstance(value, str):
            cleaned[str(key)] = value[:MAX_STRING]
        else:
            # Lists, dicts, model instances, UUIDs, datetimes. A UUID or a date is
            # a perfectly reasonable thing to want on an event — pass `str(...)`
            # and it survives. Being explicit here is what keeps a `Beat` object
            # from being JSON-serialised into this column by accident.
            dropped.append(key)

    if dropped:
        logger.warning("analytics.event_props_dropped keys=%s", ",".join(dropped))
    return cleaned


def track(
    name: str,
    *,
    child: Child | None = None,
    parent: User | None = None,
    session: PlaySession | None = None,
    ts: datetime.datetime | None = None,
    **props: Any,
) -> Event | None:
    """Append one event. Returns the row, or `None` if it could not be written.

    The return value exists for tests and for the handful of callers that want to
    assert they emitted something. **No caller may branch on it** — a service that
    behaves differently because telemetry failed has made telemetry load-bearing,
    which is exactly what rule 1 above forbids.

    `parent` is derived from `child` when it is not passed, because almost every
    child-scoped event is also a fact about an account and no caller should have
    to remember to say so twice. That derivation happens **inside** the `try`: on
    a `Child` that was not loaded with `select_related`, `child.parent` is a
    deferred database read, and it is the one line of this function other than the
    insert that can touch a database at all. `getattr`'s default absorbs the
    `AttributeError` a null FK raises and nothing else, so with that line outside
    the guard an `OperationalError` from the fetch reached the caller — which on
    this path is `record_choice` or `_persist_beat`, with a child mid-story
    (Day-14 review, P1-1). Rule 1 has to cover every line that can fail, not the
    obvious one.
    """
    if name not in Event.NAMES:
        # Loud in development, quiet in production. An unknown name can only come
        # from a literal in our own source, so it is a programming error — and the
        # place to find one is a developer's terminal, never a family's evening.
        # Rule 1 wins in production: a mistyped counter must not cost a child a
        # beat.
        if settings.DEBUG:
            raise ValueError(f"Unknown event name {name!r}. Add it to Event.NAMES.")
        logger.error("analytics.event_unknown name=%s", name)
        return None

    try:
        if parent is None and child is not None:
            parent = getattr(child, "parent", None)
        return _write(
            name=name,
            child=child,
            parent=parent,
            session=session,
            ts=ts or timezone.now(),
            props=_clean(props),
        )
    except Exception:
        # Rule 1. `exception` rather than `error` because the traceback is the only
        # thing that will ever explain a gap in the funnel.
        logger.exception("analytics.event_failed name=%s", name)
        return None


def _write(
    *,
    name: str,
    child: Child | None,
    parent: User | None,
    session: PlaySession | None,
    ts: datetime.datetime,
    props: dict[str, Any],
) -> Event:
    """The insert, isolated so a failure cannot poison the caller's transaction.

    Most `track` calls happen inside an `atomic` block that is doing the real work
    — recording a choice, creating a child. Postgres marks an *entire* transaction
    as failed after any error inside it, so an `IntegrityError` here would take
    the child's row down with it and `track`'s `except` would be a lie: the caller
    would still fail, several queries later, for a reason nothing points at.

    `atomic` around the insert gives it a savepoint of its own, so the failure
    rolls back to just before this line and the surrounding work survives.
    """
    with transaction.atomic():
        return Event.objects.create(
            name=name,
            child=child,
            parent=parent,
            session=session,
            session_key=getattr(session, "pk", None),
            ts=ts,
            props=props,
        )
