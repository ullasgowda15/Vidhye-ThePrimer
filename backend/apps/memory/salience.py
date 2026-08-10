"""Salience — deciding what is worth remembering (PRODUCT.md §11.4).

> *"The post-session job scores each candidate memory for salience: emotional
> intensity, novelty, child-initiated (things the child chose/said/named weigh
> heavily), skill milestones, and repetition. High-salience items become LTM; the
> mundane is summarized or dropped."*

This module is that sentence, made arithmetic. It is the load-bearing piece of
§11: without it we would have a transcript store, and a transcript store cannot
produce a companion that "remembers like a friend" no matter how good the
retrieval on top of it is. Three separate goods come out of being selective —
bounded prompt cost, retrieval that is about what matters, and a companion whose
recall feels human rather than forensic — and all three are downstream of the
numbers below.

**Five factors, weighted, and `child_initiated` weighs most.** That ordering is
§11.4's, and it is the difference between a product that feels like it knows the
child and one that summarises its own output back at them. A beat we generated is
*our* content; the name the child gave the dragon is *theirs*, and it is the
thing that makes them feel known when it comes back eight months later (§9.3).

**Novelty is computed against what we already hold, and against what this batch
has already kept.** That second half is what stops one sitting from writing four
near-identical "chose a path" memories: candidates are considered strongest-first,
and each one's novelty is measured against the ones already accepted. It is a
greedy diversity filter, and it is why `select` returns a *set* rather than
scoring rows independently — the salience of a memory genuinely depends on what
else we are keeping.

**Repetition and novelty pull against each other on purpose.** A thing that keeps
coming back matters *because* it keeps coming back, even though it is not new —
the favourite toy, the character asked for every time (§11.5, §11.7). Netting
them out to one "interestingness" number would collapse two different reasons to
remember something into one, and the graphs in §11.7-11.8 need both.

Nothing here calls a model or touches the database. The score is a pure function
of a candidate and its neighbours, which is what makes "why was this remembered
and that forgotten" answerable in a test rather than only in production.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from django.conf import settings

#: The five factors of §11.4 and what each is worth. They sum to 1.0, so a score
#: is directly comparable to `MEMORY_SALIENCE_THRESHOLD` and reads as "how much
#: of everything that could matter about this, did".
#:
#: `child_initiated` leads by a clear margin because it is the factor that makes
#: memory feel like memory. `novelty` is third rather than second: a *new* mundane
#: thing is still mundane, and weighting novelty above feeling would fill the
#: table with first-time trivia.
WEIGHTS: dict[str, float] = {
    "child_initiated": 0.34,
    "emotional_intensity": 0.22,
    "novelty": 0.20,
    "skill_milestone": 0.14,
    "repetition": 0.10,
}

#: Above this similarity to something we already hold, a candidate is not a new
#: memory — it is the same memory again, and writing it would make the third
#: telling of one story look like three things that happened. Separate from the
#: novelty *weighting* because "less interesting" and "already known" call for
#: different actions: one lowers a score, the other skips the row entirely.
DUPLICATE_SIMILARITY = 0.7

#: Words that carry no topic. Small and deliberately not shared with
#: `orchestrator._FUNCTION_WORDS`, which answers a different question — whether
#: two *choice labels* are too alike to offer a child. Coupling the two would mean
#: tuning how options are worded silently changing what gets remembered.
_STOPWORDS = frozenset(
    """a an and the to of in on at for with was were is are be been it its they them
    their he she his her him you your we our i me my that this these those then than
    there here when while as by from into out up down over under again very just so
    but or if not no yes did do does done had has have""".split()
)

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True, slots=True)
class Signals:
    """The five §11.4 factors for one candidate, each 0-1.

    `novelty` is absent from the constructor's meaningful defaults on purpose: it
    is the one factor that cannot be known when a candidate is built, because it
    depends on the rest of the child's history and on the rest of this batch.
    `select` fills it in.
    """

    child_initiated: float = 0.0
    emotional_intensity: float = 0.0
    novelty: float = 0.0
    skill_milestone: float = 0.0
    repetition: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in WEIGHTS}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One thing that might be worth remembering, before we have decided.

    Deliberately not a `Memory` instance. A candidate is a *proposal*, most of
    which are dropped, and building 40 unsaved model instances per session to
    throw 36 away would blur the line this module exists to draw. It also keeps
    scoring free of the ORM, so `test_salience` needs no database at all.
    """

    type: str
    content: str
    entities: list[str] = field(default_factory=list)
    valence: float = 0.0
    signals: Signals = field(default_factory=Signals)
    #: `rules` for something derived from the persisted rows and the STM log,
    #: `model` for something the consolidation prompt proposed. Carried into the
    #: log line (never into the row) so a run that produced too much or too little
    #: can be traced to the half that produced it.
    origin: str = "rules"
    source_adventure_id: Any = None

    @property
    def score(self) -> float:
        return salience(self)


def salience(candidate: Candidate) -> float:
    """How much this moment mattered, 0-1 (§11.4).

    The weighted sum of the five factors, clamped. Clamped rather than trusted:
    the model-backed half of consolidation supplies a `valence` this reads as
    emotional intensity, and a model that answers `valence: 4` must not be able to
    make one memory outrank everything else a child ever did.
    """
    signals = candidate.signals.as_dict()
    total = sum(WEIGHTS[name] * _clamped(signals[name]) for name in WEIGHTS)
    return round(_clamped(total), 4)


def threshold() -> float:
    """The LTM-write cut, from configuration (§11.4's "high-salience items").

    `settings.MEMORY_SALIENCE_THRESHOLD` — the `MEMORY_` prefix so it sorts with
    the rest of §11's configuration. A function rather than a module constant
    because a constant would be bound at import time, which is exactly what makes
    a settings override in a test do nothing.
    """
    return float(settings.MEMORY_SALIENCE_THRESHOLD)


def limit() -> int:
    """The most memories one sitting may produce, whatever it scored.

    A ceiling as well as a threshold, because they fail differently. The threshold
    is a judgement about one moment; this is a statement about what a sitting *is*
    — half an hour with a four-year-old contains a handful of things worth
    remembering, and a run that wanted to write thirty of them has misjudged the
    session rather than found an unusually rich one.
    """
    return int(settings.MEMORY_MAX_PER_SESSION)


@dataclass(frozen=True, slots=True)
class Selection:
    """What consolidation decided, in enough detail to explain itself.

    `dropped` carries the candidates *and* their scores so the job's log line can
    say "12 candidates, 4 kept, top dropped scored 0.41" without holding any
    content. That line is the only visibility anyone has into whether the
    threshold is set right (§11.4 is a tuning problem, not a solved one).
    """

    kept: list[Candidate]
    dropped: list[Candidate]
    duplicates: list[Candidate]

    @property
    def considered(self) -> int:
        return len(self.kept) + len(self.dropped) + len(self.duplicates)


def select(
    candidates: list[Candidate],
    *,
    existing: list[str] | None = None,
    cut: float | None = None,
    cap: int | None = None,
) -> Selection:
    """Choose which candidates become long-term memories.

    `existing` is the content of memories this child already has — the near
    neighbours of what is being proposed, not the whole history. Passing all of it
    would be both slow and wrong: a child's memory from last spring should not
    make today's similar moment unwritable, which is what the caller's recency
    window is for.

    The pass is greedy and strongest-first, and each candidate's novelty is
    measured against everything accepted so far. Two consequences worth being
    explicit about:

    * A run is **order-independent** given the same inputs, because the ordering
      is by base score and ties break on content. The same session consolidated
      twice keeps the same memories.
    * A candidate can be dropped for being *like something better*, not only for
      being unremarkable. That is the intended behaviour — the second-best
      telling of one moment is not a second moment.
    """
    cut = threshold() if cut is None else cut
    cap = limit() if cap is None else cap

    held = [_tokens(text) for text in (existing or [])]
    # Ranked by everything except novelty, which is what "strongest-first" can
    # mean before novelty is known. Content breaks ties so the order is total.
    ranked = sorted(
        candidates,
        key=lambda candidate: (-_base_score(candidate), candidate.content),
    )

    kept: list[Candidate] = []
    dropped: list[Candidate] = []
    duplicates: list[Candidate] = []

    for candidate in ranked:
        tokens = _tokens(candidate.content)
        closest = max((_similarity(tokens, other) for other in held), default=0.0)

        if closest >= DUPLICATE_SIMILARITY:
            duplicates.append(candidate)
            continue

        scored = replace(candidate, signals=replace(candidate.signals, novelty=1.0 - closest))
        if scored.score < cut or len(kept) >= cap:
            dropped.append(scored)
            continue

        kept.append(scored)
        # Accepted content joins the comparison set, so the *next* candidate is
        # measured against what we are actually keeping and not only against
        # history. This is the diversity half of the filter.
        held.append(tokens)

    return Selection(kept=kept, dropped=dropped, duplicates=duplicates)


def _base_score(candidate: Candidate) -> float:
    """The score with novelty treated as fully novel — the ranking key.

    Ranking by the *best case* rather than by the four known factors alone keeps
    the comparison on the same scale as the final score, so a candidate ranked
    first cannot be one that could never have passed the threshold.
    """
    return salience(replace(candidate, signals=replace(candidate.signals, novelty=1.0)))


def novelty_against(content: str, existing: list[str]) -> float:
    """How new `content` is, 0-1. Exposed for the tests and for Day 9's re-scoring."""
    tokens = _tokens(content)
    closest = max((_similarity(tokens, _tokens(other)) for other in existing), default=0.0)
    return 1.0 - closest


def _tokens(text: str) -> frozenset[str]:
    """Topic words, stemmed crudely, as a set.

    Set semantics rather than a bag: we are asking "is this the same moment", and
    a word appearing twice does not make two moments. The six-character truncation
    is the same cheap stemmer the local embedding provider uses, so "counted" and
    "counting" collapse without a stemming dependency.
    """
    return frozenset(
        word[:6] for word in _WORD.findall(text.lower()) if word not in _STOPWORDS and len(word) > 2
    )


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two token sets, 0-1.

    Jaccard rather than cosine over embeddings, deliberately. This runs before
    anything is embedded — an embedding costs a provider call, and paying for one
    to discover a candidate is a duplicate would be paying to find out we do not
    want it. Lexical overlap is a coarse instrument that is exactly right for
    near-duplicate detection on one-sentence summaries generated from the same
    session.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _clamped(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, numeric))
