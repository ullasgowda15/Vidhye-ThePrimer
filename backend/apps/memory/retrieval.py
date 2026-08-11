"""Hybrid memory retrieval (PRODUCT.md §11.9, §19.4).

    context ──embed──> vector neighbours ─┐
                                          ├─> rank ─> dedupe ─> truncate ─> bundle
    child ────rules──> must_include ──────┘

Day 8 filled the table; this is the half that makes filling it worth anything.
Four decisions shape the module and each is a line in §11.9.

**Hybrid, because neither half is sufficient.** Vector similarity answers "what is
relevant to *this* moment" and is the only thing that can surface the shell from
the beach adventure when today's story reaches a beach. It is also blind to
everything the story is not currently about — a child's active fear is exactly the
memory that must arrive when the story has *not* mentioned it, which is why
`must_include` is a rule and not a query. So: rules pinned, vectors ranked, one
bundle.

**Few, not many.** §19.4's phrase is "few, high-value memories", and it is a
statement about the prompt as much as the bill. A bundle of thirty memories costs
tokens on every beat of every adventure forever, and buys a model that has been
handed a child's whole history and asked to notice one thing in it. The budget is
enforced here rather than asked for, because a prompt that grows with a child's
history is a prompt that gets worse the longer they stay.

**Never another child's memories.** Every query in this module filters on `child`,
and the bundle carries `child_id` so the property is assertable on the value rather
than only on the code that produced it (§13.8, §17.5). There is no call signature
here that can express "everyone's memories", which is deliberate: the cheapest way
to guarantee a leak cannot happen is to make it unspellable.

**Degrading is normal, and it is always toward the rules.** An embedding provider
having a bad day costs us the *similarity* half and nothing else: `must_include`
needs no provider, no vector and no network, so a child whose provider is down is
greeted with their fears, their loves and their companion intact. The bundle says
so (`degraded`) rather than quietly looking thin.

What this module does **not** do: decide what a memory means, or pick a world from
it. What to teach belongs to `apps.learning` and interest-graph world selection is
Day 11; this returns the memories, and the prompt decides what to do with them.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F, QuerySet
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.media.adapters import embedding as embedding_adapter
from apps.media.exceptions import MediaError
from apps.memory import salience as salience_scoring
from apps.memory.models import Memory, MemoryType
from apps.profiles.models import Child

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryBundle",
    "Recalled",
    "estimate_tokens",
    "mark_recalled",
    "must_include",
    "rank",
    "retrieve",
]

# ---------------------------------------------------------------------------
# Where an item came from
# ---------------------------------------------------------------------------
#: Retrieved because a rule says this child's prompt always carries it (§11.9).
RULE = "rule"
#: Retrieved because it is semantically near the current moment.
VECTOR = "vector"

#: The `type` the two synthesised items carry into the prompt. They are not
#: `MemoryType` values and must not be: nothing in the `memory` table produced
#: them, and a consolidation run that started writing rows typed `companion_state`
#: would make "always include active fears" quietly ambiguous.
COMPANION_STATE = "companion_state"
INTEREST = "interest"

# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
#: §11.9's three signals, weighted. Similarity leads because the vector half's
#: whole job is relevance to *this* moment; salience is the durable judgement made
#: at consolidation; recency is the tiebreak that keeps a companion talking about
#: last week rather than about last spring.
#:
#: They are weights on a bounded sum rather than a sort key tuple, because a strict
#: lexicographic sort by salience would rank an old, very salient memory above a
#: perfectly relevant recent one every single time — which is how a companion ends
#: up with one favourite anecdote it tells forever.
WEIGHTS: dict[str, float] = {
    "similarity": 0.5,
    "salience": 0.3,
    "recency": 0.2,
}

#: How long it takes an untouched memory to lose half its recency contribution.
#: A month, because that is roughly the span over which "the other day" stops being
#: true for a five-year-old. It is a *ranking* half-life and not `Memory.decay`,
#: which is recall strength and is written by the decay pass — the two are
#: multiplied rather than merged so "this is old" and "this is fading" stay
#: separable (see `Memory.decay`).
RECENCY_HALF_LIFE_DAYS = 30.0

#: How many of each rule class ride in every bundle. Small, fixed numbers rather
#: than "all of them": the point of a rule-include is that the prompt always
#: carries the *most* active fear, not that it carries a list of every worry a
#: child has ever had. Bounded here is what makes the token budget reachable
#: without the budget ever having to drop a rule (see `_truncate`).
RULE_FEARS = 3
RULE_INTERESTS = 3
RULE_RECENT = 3

#: How salient a memory must be to count as one of §11.9's "recent salient events".
#: Above `MEMORY_SALIENCE_THRESHOLD` by construction — everything in the table
#: cleared that — so this is the second, stricter cut that separates "worth keeping"
#: from "worth mentioning unprompted".
RECENT_SALIENCE_FLOOR = 0.55

# ---------------------------------------------------------------------------
# The token budget
# ---------------------------------------------------------------------------
#: Characters per token, near enough. English averages a shade under four, and the
#: estimate is deliberately on the *low* side of nothing: this bounds a prompt we
#: are paying for, so an estimate that ran under the truth would silently spend the
#: budget it exists to enforce. See `estimate_tokens`.
CHARS_PER_TOKEN = 4

#: What one item costs beyond its own text — the `- (skill_moment) ` prefix and the
#: newline that `assembler._memory_block` renders around it. Counted because a
#: bundle of twelve short memories is a third framing, and a budget that ignored it
#: would be wrong by exactly the amount that matters when the items are small.
ITEM_OVERHEAD_TOKENS = 6

#: How much of one memory sentence may reach the prompt. A memory is one sentence
#: by construction (`memory.prompts.MemoryProposal`), so anything past this is a
#: model or an importer having misunderstood the contract, and truncating it is
#: better than letting one row eat the whole bundle.
CONTENT_CHARS = 240


def token_budget() -> int:
    """The bundle's ceiling, from configuration (§19.4's "truncate to a budget").

    A function rather than a module constant so a settings override in a test does
    something — the same reason `salience.threshold` is one.
    """
    return max(0, int(settings.MEMORY_RETRIEVAL_TOKEN_BUDGET))


def candidate_limit() -> int:
    """How many vector neighbours are fetched before ranking."""
    return max(0, int(settings.MEMORY_RETRIEVAL_CANDIDATES))


def estimate_tokens(text: str) -> int:
    """About how many tokens `text` will cost, framing included.

    An estimate, and named one. Counting exactly would mean shipping a tokenizer
    per provider and re-counting when a provider changes theirs, to enforce a
    number (`MEMORY_RETRIEVAL_TOKEN_BUDGET`) that is itself a judgement call. What
    the budget has to guarantee is that the bundle cannot grow with a child's
    history, and a four-characters-per-token approximation guarantees that exactly
    as well as a real tokenizer would.
    """
    return math.ceil(len(text) / CHARS_PER_TOKEN) + ITEM_OVERHEAD_TOKENS


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Recalled:
    """One thing the companion is about to be reminded of.

    Deliberately not a `Memory` instance, and for a sharper reason than
    `salience.Candidate`'s: two of the items in every bundle are not rows at all.
    The companion's own state and the child's stated interests are facts we hold
    about a family rather than moments we scored, and modelling them as unsaved
    `Memory` objects would put two things in the bundle that a `bulk_create` typo
    could persist into a child's history.

    `memory_id` is what tells the two apart, and `mark_recalled` reads it: recall
    bookkeeping is meaningful for a memory that was *chosen* and meaningless for a
    fact that is in every prompt anyway.
    """

    type: str
    content: str
    source: str = RULE
    memory_id: Any = None
    salience: float = 0.0
    similarity: float = 0.0
    recency: float = 0.0
    score: float = 0.0
    #: True for the rule-includes, which are ordered ahead of the ranked half and
    #: are therefore the last things the budget would ever drop (§11.9's "always").
    pinned: bool = False

    @property
    def stored(self) -> bool:
        """Is this a real `memory` row, rather than a profile or companion fact?"""
        return self.memory_id is not None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def as_prompt_item(self) -> dict[str, str]:
        """The shape `prompting.assemble(memory=…)` reads.

        Two keys and no id. The model is being told what this child loves and what
        happened to them; a row id in the prompt is a token we pay for, a thing a
        beat could repeat back to a child, and a piece of our schema in a third
        party's logs (§13.8).
        """
        return {"type": self.type, "content": self.content}


@dataclass(frozen=True, slots=True)
class MemoryBundle:
    """What retrieval decided this moment should be told, and what it cost.

    Carries the ids of the memories actually used, which is the Day-9 stub §5 asks
    for: the caller bumps `recall_count` on exactly what reached the prompt, so
    recall strength records what the companion *said* rather than what a query
    happened to touch.

    `child_id` is on the value rather than only in the code that built it. A test —
    and a reviewer — can then ask the bundle itself whether it belongs to the child
    it was retrieved for, which is the one property in this module that must never
    silently stop holding (§17.5).
    """

    child_id: Any
    items: tuple[Recalled, ...] = ()
    #: How many candidates were ranked, before dedupe and the budget. The
    #: numerator of "we retrieved few from many", and the number a log line needs
    #: to show the budget is doing something.
    considered: int = 0
    tokens: int = 0
    #: True when the vector half could not run — no provider, or one that did not
    #: answer. The bundle is still useful; it is just rules-only.
    degraded: bool = False

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Any:
        return iter(self.items)

    @property
    def remembered(self) -> tuple[Recalled, ...]:
        """The items that are real memories of things that happened.

        What makes a child *returning* rather than new: interests and a companion
        name exist from onboarding, so a bundle carrying only those is a bundle for
        a child who has not played yet (`companion.greeting`).
        """
        return tuple(item for item in self.items if item.stored)

    @property
    def memory_ids(self) -> tuple[Any, ...]:
        return tuple(item.memory_id for item in self.remembered)

    def as_prompt(self) -> list[dict[str, str]]:
        return [item.as_prompt_item() for item in self.items]


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def retrieve(
    child: Child,
    context: str = "",
    *,
    budget: int | None = None,
    candidates: int | None = None,
) -> MemoryBundle:
    """The bundle for one moment: rules, plus what is near `context` (§11.9).

    `context` is what the story is about right now — the plan's title, this beat's
    stage, what the child just chose. Empty is legitimate and means "no moment yet"
    (the returning greeting, a plan being built before there is a story), and the
    vector half is simply skipped: embedding the empty string would cost a provider
    call to rank by nothing.

    Never raises. Every caller is between a child and their story, so a retrieval
    that cannot run returns a thinner bundle and says so, and the beat is told from
    the rules and the profile — which is precisely what Day 8 was building toward
    when it made `Memory.embedding` nullable.
    """
    budget = token_budget() if budget is None else budget
    limit = candidate_limit() if candidates is None else candidates

    rules = must_include(child)
    vectors, degraded = _nearest(child, context, limit=limit)

    # Ranked, merged, then ranked again. The second pass is not redundant: merging
    # a rule-include with its own vector hit gives it a similarity it did not have
    # when it was first ordered, and a bundle sorted before that number existed
    # would put the most relevant memory in the middle of the pinned block.
    kept, tokens = _truncate(rank(_dedupe(rank([*rules, *vectors]))), budget)

    bundle = MemoryBundle(
        child_id=child.pk,
        items=tuple(kept),
        considered=len(rules) + len(vectors),
        tokens=tokens,
        degraded=degraded,
    )
    logger.info(
        "memory.retrieve child=%s considered=%d kept=%d stored=%d tokens=%d/%d degraded=%d",
        child.pk,
        bundle.considered,
        len(bundle),
        len(bundle.remembered),
        bundle.tokens,
        budget,
        int(degraded),
    )
    return bundle


def must_include(child: Child) -> list[Recalled]:
    """The items §11.9 says are in every bundle, whatever the story is about.

    Active fears, the companion's state, recent salient events — plus the child's
    stated loves, which §19.1's `memory_bundle` layer names alongside them and
    which are the reason a brand-new child's bundle is not empty.

    Ordered rather than merely collected, and the order is the priority the budget
    truncates against: who the companion is, what frightens the child, what they
    love, what happened lately. A prompt that had to drop one of these should drop
    the last one.

    Needs no provider, no vector and no network. That is the property that makes it
    the floor under everything else here.
    """
    items: list[Recalled] = []

    if state := _companion_state(child):
        items.append(state)

    items.extend(_profile_facts(child.fears, MemoryType.FEAR, f"{child.first_name} is afraid of"))
    items.extend(_stored_fears(child))
    items.extend(_profile_facts(child.interests, INTEREST, f"{child.first_name} loves"))
    items.extend(_recent_salient(child))
    return items


def rank(items: Iterable[Recalled]) -> list[Recalled]:
    """Order a bundle's candidates: rule-includes first, then by score (§11.9).

    Two groups rather than one score with a large pinning bonus, because a bonus is
    a number someone will later tune until a fear falls off the bundle without
    anyone deciding that it should. "Always include active fears" is a rule; rules
    are not weights.

    Within each group the order is the weighted sum of similarity, salience and
    recency, and ties break on content so the ordering is total — the same child in
    the same state produces the same prompt, which is what makes any of this
    testable.
    """
    return sorted(items, key=lambda item: (not item.pinned, -item.score, item.content))


def mark_recalled(bundle: MemoryBundle) -> int:
    """Record that these memories were used (§11.9, §19.4). Returns the row count.

    One UPDATE for the whole bundle, scoped to the bundle's own child — belt and
    braces on a set of ids that already came from that child's rows, because this
    is a write and the cost of the guard is a WHERE clause.

    `recall_count` is `F("recall_count") + 1` rather than a read-modify-write: two
    streams on one adventure can be generating beats at once, and a Python-side
    increment would lose one of them. Never raises: bookkeeping that failed must
    not cost a child their beat, and the honest consequence of a lost update here
    is that one memory ranks very slightly differently next week.
    """
    ids = bundle.memory_ids
    if not ids:
        return 0
    try:
        return Memory.objects.filter(child_id=bundle.child_id, id__in=ids).update(
            last_recalled_at=timezone.now(),
            recall_count=F("recall_count") + 1,
            updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("memory.recall_bookkeeping_failed child=%s", bundle.child_id)
        return 0


# ---------------------------------------------------------------------------
# The two halves
# ---------------------------------------------------------------------------
def _memories(child: Child) -> QuerySet[Memory]:
    """This child's live memories, and **only** this child's.

    Every read in this module starts here. One function rather than the same two
    filters written five times, so "no cross-child retrieval" is a property of one
    line of code that a reviewer can check once (§17.5).
    """
    return Memory.objects.filter(child=child, deleted_at__isnull=True)


def _nearest(child: Child, context: str, *, limit: int) -> tuple[list[Recalled], bool]:
    """The vector half: this child's memories nearest to `context`.

    Returns `(items, degraded)`. Degraded means the similarity half was *meant* to
    run and could not — a provider with no credentials, or one that did not answer.
    It is not the same as having nothing to search, which is the ordinary state of a
    child on their first day and is not a degradation of anything.

    The `exists()` check ahead of the embedding call is worth its round trip: a
    child with no embedded memories is every child on day one, and paying a provider
    to embed a context we have nothing to compare against would be a call per beat
    for nothing.

    `CosineDistance` because that is the operator class `Memory`'s HNSW index was
    built with — a mismatch here builds the index and silently never uses it.
    """
    if not context.strip() or limit <= 0:
        return [], False

    embeddable = _memories(child).filter(embedding__isnull=False)
    if not embeddable.exists():
        return [], False

    try:
        vector = embedding_adapter.embed(context)
    except MediaError as exc:
        # Warning rather than exception: this is an expected degradation with a
        # known cost (relevance), and the bundle still carries the rules.
        logger.warning("memory.retrieve_embed_failed child=%s reason=%s", child.pk, exc)
        return [], True

    rows = embeddable.annotate(distance=CosineDistance("embedding", vector)).order_by("distance")[
        :limit
    ]
    return [_from_row(row, source=VECTOR, distance=row.distance) for row in rows], False


def _companion_state(child: Child) -> Recalled | None:
    """Who the companion is, as a fact in the bundle (§11.9's "companion state").

    Also in the `world_and_companion_state` **instruction** layer, and the overlap
    is intentional rather than sloppy. That layer tells the model how to *be* the
    companion — the canonical appearance it must never contradict. This one is a
    fact about the child's history that the greeting is written from: the reason
    "You named me Sparkle" is a thing the companion can say is that its own name
    arrived as something it was given (§9.3's reciprocity), and a bundle that
    carried the child's whole world except the friend in it would be a strange
    bundle to write a welcome from.
    """
    try:
        companion = child.companion
    except ObjectDoesNotExist:
        # Onboarding is not finished. There is nothing to say about a friend the
        # child has not chosen yet, and inventing one here would put a name in the
        # prompt that the safety layer has explicitly told the model not to assert.
        return None

    content = (
        f"{child.first_name}'s companion is {companion.name}, a {companion.archetype}, "
        f"chosen and named when they first met."
    )
    if stage := str((companion.evolution_state or {}).get("stage") or ""):
        content += f" {companion.name} is at the {stage} stage of growing up alongside them."
    return Recalled(
        type=COMPANION_STATE,
        content=_capped(content),
        source=RULE,
        pinned=True,
        # Scored at the top of its class so `rank` keeps it first among the pinned
        # items without a special case: the companion is the one fact every other
        # item in the bundle is addressed to.
        score=1.0,
    )


def _profile_facts(values: Sequence[str] | None, kind: str, opener: str) -> list[Recalled]:
    """The onboarding seeds, as bundle items (§19.1's "interests, active fears").

    These were the *whole* bundle before today (`orchestrator.seed_memories`, now
    folded in here), and they stay in it for the reason they were there: a child on
    their first day has no memories, and a bundle with nothing in it makes beat one
    generic in exactly the moment §4.4 has to prove the product works.

    They are facts a parent typed, so they enter as data and are scrubbed on the way
    into the prompt like everything else — by `guard_input` at the call site and
    again by the assembler (§13.5). Nothing here trusts them.
    """
    limit = RULE_FEARS if kind == MemoryType.FEAR else RULE_INTERESTS
    items: list[Recalled] = []
    for value in list(values or [])[:limit]:
        text = str(value).strip()
        if not text:
            continue
        items.append(
            Recalled(
                type=str(kind),
                content=_capped(f"{opener} {text}."),
                source=RULE,
                pinned=True,
                # Below the companion, above the memory rows in the same class: a
                # fear a parent stated is the one we are surest of.
                score=0.9,
            )
        )
    return items


def _stored_fears(child: Child) -> list[Recalled]:
    """Fears the *sessions* revealed — §11.9's "always include active fears".

    Separate from the parent's list because they are different evidence. A parent
    knows their child is scared of the dark; consolidation notices the beat where
    they went quiet at the bridge. Both belong in every prompt, and only one of them
    can be got by asking.
    """
    rows = (
        _memories(child)
        .filter(type=MemoryType.FEAR)
        .order_by("-salience", "-created_at")[:RULE_FEARS]
    )
    return [_from_row(row, source=RULE, pinned=True) for row in rows]


def _recent_salient(child: Child) -> list[Recalled]:
    """§11.9's "recent salient events" — what happened lately that mattered.

    The half of retrieval that makes day two work at all. A vector search against
    today's opening finds what is *like* today; this finds what actually happened
    last time, which is the thing a returning child is waiting to hear about.

    Ordered by recency and cut by salience rather than the other way round: "the
    most salient thing ever" is an anecdote a companion would repeat forever, and
    "the most recent thing" is whatever the last beat happened to be about.
    """
    rows = (
        _memories(child)
        .filter(salience__gte=RECENT_SALIENCE_FLOOR)
        .order_by("-created_at")[:RULE_RECENT]
    )
    return [_from_row(row, source=RULE, pinned=True) for row in rows]


# ---------------------------------------------------------------------------
# Scoring, dedupe, budget
# ---------------------------------------------------------------------------
def _from_row(
    row: Memory, *, source: str, distance: float | None = None, pinned: bool = False
) -> Recalled:
    similarity = _similarity(distance)
    salience = _clamped(row.salience * row.decay)
    recency = _recency(row)
    return Recalled(
        type=row.type,
        content=_capped(row.content),
        source=source,
        memory_id=row.pk,
        salience=salience,
        similarity=similarity,
        recency=recency,
        score=round(
            WEIGHTS["similarity"] * similarity
            + WEIGHTS["salience"] * salience
            + WEIGHTS["recency"] * recency,
            4,
        ),
        pinned=pinned,
    )


def _similarity(distance: float | None) -> float:
    """Cosine *distance* as a 0-1 similarity.

    pgvector's cosine distance runs 0 (identical) to 2 (opposite), so this is
    `1 - d` clamped. Clamped rather than trusted: a provider that returns
    un-normalised vectors would push the distance outside its nominal range, and a
    negative similarity multiplied by a weight would rank a memory *below* having
    no vector at all — which is a silent inversion rather than a visible failure.
    """
    if distance is None:
        return 0.0
    return _clamped(1.0 - float(distance))


def _recency(row: Memory) -> float:
    """1.0 for a memory made today, halving every `RECENCY_HALF_LIFE_DAYS`.

    Measured from the last time the memory was *used* when it has been used, and
    from when it was made when it has not. A memory the companion mentioned last
    week is live in the child's mind whatever its birthday says, which is the whole
    reason `last_recalled_at` is written (§11.9) rather than merely counted.
    """
    seen = row.last_recalled_at or row.created_at
    if seen is None:
        return 0.0
    days = max(0.0, (timezone.now() - seen).total_seconds() / 86_400.0)
    return _clamped(0.5 ** (days / RECENCY_HALF_LIFE_DAYS))


def _dedupe(items: Iterable[Recalled]) -> list[Recalled]:
    """Fold repeats — the same row twice, and the same *moment* said twice.

    Two questions, because there are two ways a bundle repeats itself.

    **The same row, from both halves.** A memory that a rule requires *and* that is
    a near neighbour of today's context arrives twice with one id. Those two copies
    are **merged rather than deduplicated**, and the difference is not pedantry: the
    rule copy knows the prompt must carry it and the vector copy knows how relevant
    it is, and keeping only the first one seen throws away whichever fact the other
    one held. Dropping the vector copy — which is what "keep the first" means once
    the rules are ordered ahead — made every recent memory rank by recency alone,
    so the *most relevant* thing the child did last week landed in the middle of the
    bundle instead of at the top of it.

    **Two rows about one moment.** Different ids, near-identical sentences, which
    only a content comparison finds — the same question `salience.select` asks at
    write time, asked again here because a bundle is drawn from the whole history
    rather than from one sitting. That one really is a drop, and the survivor is the
    higher-ranked of the pair.
    """
    by_id: dict[Any, int] = {}
    kept: list[Recalled] = []
    kept_content: list[str] = []

    for item in items:
        if item.stored and (index := by_id.get(item.memory_id)) is not None:
            kept[index] = _merged(kept[index], item)
            continue
        if salience_scoring.is_the_same_memory(item.content, kept_content):
            continue
        if item.stored:
            by_id[item.memory_id] = len(kept)
        kept.append(item)
        kept_content.append(item.content)
    return kept


def _merged(kept: Recalled, other: Recalled) -> Recalled:
    """One memory that both halves found, holding what each of them knew.

    `pinned` is a logical OR — a rule required it, and nothing the vector half says
    can un-require it. Everything else takes the better of the two, because the two
    copies differ only in what was *measured*, never in what is true: the rule copy
    was built with no query to compare against and therefore a similarity of zero.
    """
    return replace(
        kept,
        source=RULE if RULE in (kept.source, other.source) else VECTOR,
        pinned=kept.pinned or other.pinned,
        similarity=max(kept.similarity, other.similarity),
        salience=max(kept.salience, other.salience),
        recency=max(kept.recency, other.recency),
        score=max(kept.score, other.score),
    )


def _truncate(items: Sequence[Recalled], budget: int) -> tuple[list[Recalled], int]:
    """Cut the ranked list to the token budget (§19.4).

    Greedy from the head, which is what makes `rank`'s pinning meaningful: the
    rule-includes are the first things offered the budget and therefore the last
    things it can refuse. It **can** refuse them — a budget small enough to drop a
    fear is a misconfiguration and is logged as one, rather than silently exempted,
    because a guarantee the code quietly works around is not a guarantee.

    A single over-long item does not stop the walk. The list is ranked, so the item
    after it is the next best thing that might still fit, and stopping at the first
    miss would let one verbose memory cost a child three good ones.
    """
    kept: list[Recalled] = []
    spent = 0
    for item in items:
        cost = item.tokens
        if spent + cost > budget:
            if item.pinned:
                logger.warning(
                    "memory.retrieve_budget_dropped_rule type=%s budget=%d spent=%d",
                    item.type,
                    budget,
                    spent,
                )
            continue
        kept.append(item)
        spent += cost
    return kept, spent


def _capped(text: str) -> str:
    return str(text).strip()[:CONTENT_CHARS]


def _clamped(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
