"""Entity keys — one node per thing (PRODUCT.md §11.7).

`Memory.entities` is not decoration. §11.7's interest graph and relationship graph
are built by *querying this column*, and Day 9's retrieval reads it. So the column
has one job: the same thing must arrive as the same key, every time, from either
half of consolidation.

It did not. The Day 8 review found the founder's own data already forked — four
rows saying `companion:ruby` alongside one saying `companion:Ruby` — which is one
companion stored as two nodes, each carrying part of the recurrence that feeds the
`repetition` salience factor (§11.4). The favourite thing is precisely what gets quietly
demoted, which is the opposite of what §11.5 is for.

**Why it forked, and why normalising at the source could not fix it.** Both key
sources are model output. The rules half reads `Beat.continuity_refs`, which the
*story* model writes against one worked example (`story/ai/schemas.py:186` —
"e.g. ['companion:pip', 'world:whispering_woods']"), and the model half reads the
consolidation prompt's own example list. Neither is a constrained field, and a
model shown `companion:pip` will happily answer `companion:Ruby` when the
companion is called Ruby. Prompting harder is not a fix — it is the same
unenforced request, asked twice. The enforcement has to be here, on the way in.

**The namespace vocabulary is closed, and `interest` is an alias.** A closed set is
what makes the graph queryable at all: a rule like "everything this child cares
about" cannot be written against an open vocabulary the model extends whenever it
likes. `interest:` is aliased to `character:` rather than kept, because nothing in
this codebase emits `interest:` — it is purely a model coinage, and in the
observed data it named story creatures (`interest:Elephant` beside
`character:elephant`, the same elephant twice). Aliasing merges them; keeping the
namespace would have preserved the fork the review asked us to close.

**Unknown namespaces are dropped rather than coerced.** A ref we cannot place is a
ref we cannot query, and storing it under a guessed namespace would put a wrong
edge in the graph — worse than no edge, because it is invisible. It is logged
once per consolidation so a model that invents a genuinely useful namespace shows
up as a decision to make rather than as silence.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: The namespaces the memory graph is queryable against. Chosen to cover what the
#: story model actually emits into `continuity_refs` rather than an ideal list:
#: `world` and `object` are in the live data and are meaningful, so excluding them
#: would drop real edges to satisfy a tidier vocabulary.
NAMESPACES = frozenset(
    {
        "companion",  # the child's own companion
        "character",  # someone in the story
        "child",  # the child themselves
        "world",  # where it happened
        "place",  # a location within it
        "object",  # a thing that matters (§11.5's treasure shell)
        "topic",  # a subject, not a participant
        "objective",  # a learning objective (§10) — the one machine-made namespace
    }
)

#: Model coinages that mean something we already have a namespace for. Mapped
#: rather than dropped: the edge is real, only the word for it is invented.
#:
#: `interest` → `character` is the review's "reconcile character vs interest",
#: decided in the direction the evidence pointed — see the module docstring.
ALIASES = {
    "interest": "character",
    "creature": "character",
    "animal": "character",
    "person": "character",
    "friend": "character",
    "setting": "world",
    "scene": "world",
    "location": "place",
    "item": "object",
    "thing": "object",
    "subject": "topic",
}

#: How long a key may be. `content` is a sentence and these are its index terms;
#: anything longer is a model returning prose into the wrong field.
MAX_KEY_LENGTH = 120

_SEPARATORS = re.compile(r"[\s\-]+")
_UNSAFE = re.compile(r"[^a-z0-9_]+")


def normalise(refs: list[str] | None, *, context: str = "") -> list[str]:
    """Canonical entity keys, deduplicated, order preserved.

    Order is preserved because it is weakly meaningful — the story model tends to
    put the beat's subject first — and because a stable order makes the column
    diffable when someone is trying to understand a memory.

    `context` names the caller in the log line (the session or adventure id), so a
    dropped ref can be traced back to the sitting that produced it without the log
    carrying any of the child's content.
    """
    seen: dict[str, None] = {}
    dropped: list[str] = []

    for raw in refs or []:
        key = _one(str(raw), dropped)
        if key is not None:
            seen.setdefault(key, None)

    if dropped:
        # One line per consolidation, not one per ref: a model having a bad day
        # should be one log entry to notice, not fifty to scroll past.
        logger.info(
            "memory.entities_dropped context=%s count=%d namespaces=%s",
            context or "-",
            len(dropped),
            sorted({item.partition(":")[0][:24] for item in dropped}),
        )

    return list(seen)


def _one(raw: str, dropped: list[str]) -> str | None:
    """One ref, canonicalised — or `None` if it has no place in the graph."""
    namespace, separator, value = raw.strip().partition(":")
    if not separator:
        # A bare token names a thing without saying what kind of thing it is,
        # which is exactly the edge we cannot query. Dropped, not guessed at.
        dropped.append(raw)
        return None

    namespace = _slug(namespace)
    namespace = ALIASES.get(namespace, namespace)
    if namespace not in NAMESPACES:
        dropped.append(raw)
        return None

    value = _slug(value)
    if not value:
        dropped.append(raw)
        return None

    return f"{namespace}:{value}"[:MAX_KEY_LENGTH]


def _slug(text: str) -> str:
    """Casefolded, underscore-joined, punctuation-free.

    Casefold rather than `lower()` because these are names a model wrote and may
    carry non-ASCII case (a Turkish dotted İ, a German ß); `lower()` leaves pairs
    of those unequal, which is the same fork one layer down.

    Underscores rather than spaces so `objective:count_to_3` — the one namespace
    written by our own code and already in this shape — survives untouched.
    """
    slug = _SEPARATORS.sub("_", text.strip().casefold())
    slug = _UNSAFE.sub("", slug)
    return slug.strip("_")
