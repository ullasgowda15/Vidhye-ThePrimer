"""The Learning Engine (PRODUCT.md §10, §5.5).

The loop, in four functions and in the order §5.5 runs them:

    select_objective(child)   what to practise, and how hard        §10.3
    adapt(adventure)          how hard *now*, mid-story             §10.4
    evidence_from(adventure)  what the child's play actually said   §10.5
    apply_evidence(...)       the estimate and the schedule move    §10.5

Three commitments shape everything below.

**It is interpretable, deliberately.** A parent sees the output of this module on
Day 12, and "your child is counting to five reliably and to seven with help" has
to be a true sentence about `mastery`. So the estimate is a running average with a
decaying learning rate, the observation that feeds it is the difficulty the child
was actually asked for, and the review interval is a straight line through
mastery. Every number in this file can be explained in one sentence, and that is a
requirement rather than a happy accident (§10.2).

**It never sees a right answer as a verdict.** `correct` appears here because
§10.5 says behaviour is the assessment, and a child who counted four frogs when
there were five has told us something real about where their edge is. It is
evidence about *us* — about whether we picked the right difficulty — and it
travels no further than this module. Nothing here is written to a beat, spoken by
a companion, or shown to a child (§8.5, Principle #1).

**It is honest about being small.** The catalog is seven objectives across two
domains, the "interest fit" term is word overlap, and the mastery update is four
lines of arithmetic. Each of those is the right size for the evidence available —
a handful of noisy signals from a four-year-old per sitting — and each has an
obvious place to grow into when the interest graph (Day 11) and the parent
dashboard (Day 12) give it something to grow for.
"""

from __future__ import annotations

import datetime
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.learning.demands import NO_OBJECTIVE, Demand, demand_for
from apps.learning.models import PREREQUISITE_MASTERY, Domain, LearningProgress, Objective
from apps.profiles.models import Child

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selection (§10.3)
# ---------------------------------------------------------------------------
#: The zone of proximal development, as §10.3 states it: an objective the child
#: half-knows is the one worth a story. Inside the band an objective scores full
#: marks; outside it the score falls off, and it falls off **faster above than
#: below** — an objective they have already mastered is a worse use of a sitting
#: than one they have not started, unless spacing says it is time to revisit
#: (which is the `due` term's job, not this one).
ZPD_FLOOR = 0.4
ZPD_CEILING = 0.7
#: How far the score falls per unit of mastery outside the band. Chosen so an
#: untouched objective (mastery 0) scores 0.6 and a mastered one (1.0) scores 0.4.
BELOW_BAND_DECAY = 1.0
ABOVE_BAND_DECAY = 2.0

#: What each term is worth. The ZPD is the anchor; spacing can outweigh it once
#: something is properly overdue; interest is a tie-break and is written as one.
ZPD_WEIGHT = 1.0
DUE_WEIGHT = 0.6
INTEREST_WEIGHT = 0.25
#: Subtracted, not added: these are the "avoid over-repeating" and "interleave
#: domains across sessions" halves of §10.3.
REPEAT_PENALTY = 0.5
INTERLEAVE_PENALTY = 0.3

#: How long an objective stays "just practised" for the repetition penalty. Two
#: days, so a child who plays every evening does not get the same objective three
#: nights running — while a child who plays on Saturdays is not penalised at all.
REPEAT_WINDOW_HOURS = 48.0

#: How overdue an objective has to be for spacing to shout rather than mention.
#: A week past its date is a fact about the schedule, not about the child.
OVERDUE_SATURATION_DAYS = 7.0

#: Preference order when two objectives score identically. Numeracy first is
#: §10.1's shape written down — *one* domain done well, with vocabulary
#: opportunistic alongside it — and it is what makes a brand-new child's first
#: adventure deterministic rather than a coin toss between two zeroes.
DOMAIN_ORDER: tuple[str, ...] = (Domain.NUMERACY, Domain.VOCABULARY)

#: How far past their own estimate a child is asked to reach. Small: the point of
#: the ZPD is that the next step is a step, and a difficulty far above mastery is
#: a beat the companion ends up doing for them.
ZPD_STRETCH = 0.1

#: The difficulty a story is ever asked for. Never 0 (a beat that asks nothing is
#: not an ask) and never 1 (there is always somewhere further to go).
MIN_DIFFICULTY = 0.05
MAX_DIFFICULTY = 0.95

# ---------------------------------------------------------------------------
# Within-session adaptation (§10.4)
# ---------------------------------------------------------------------------
#: How far one beat's evidence moves the difficulty of the next one, inside the
#: same sitting. A full rung of `demands.COUNT_LADDER` is ~0.2, so a step of 0.15
#: means a single struggle usually softens the *shape* of the ask (the companion
#: counts along, two cards instead of three) and two struggles in a row drop the
#: number itself. That ordering is deliberate: company first, then a smaller ask.
ADAPT_STEP = 0.15

#: An answer this fast, unaided and right, is one the child found easy (§10.4).
QUICK_MS = 6_000
#: An answer this slow was got to rather than known, however right it was. It does
#: not count against the child anywhere — it only stops a hesitant success from
#: being read as fluency.
SLOW_MS = 15_000

# ---------------------------------------------------------------------------
# Mastery update (§10.5)
# ---------------------------------------------------------------------------
#: What one answer says about the child, relative to the difficulty they were
#: asked for. A clean success says "this, and a bit more"; a success they needed
#: help with says "this, exactly"; no success says "this was above them today".
#:
#: Anchoring the observation to *difficulty* rather than to a flat 1/0 is what
#: closes §5.5's loop honestly: counting two frogs correctly cannot push a child's
#: estimate to mastery, and missing ten of them cannot push it to zero.
EVIDENCE_MARGIN = 0.2

#: How much one observation may move the estimate. The rate is the child's own
#: `uncertainty`, clamped — so the first thing we ever see moves it halfway, and
#: the fiftieth nudges it. `MIN_RATE` is a floor rather than zero because children
#: change, and an estimate that has stopped being able to move is an estimate that
#: will be wrong for a year.
MIN_RATE = 0.15
MAX_RATE = 0.5
#: How fast confidence accumulates, and the floor it stops at.
UNCERTAINTY_DECAY = 0.75
MIN_UNCERTAINTY = 0.15

#: Spaced retrieval (§10.3), as a straight line through mastery: something barely
#: known comes back tomorrow, something solid comes back in three weeks. A curve
#: would be defensible and would be one more thing nobody could explain to a
#: parent; this one reads as "the better they know it, the longer we leave it".
DUE_MIN_DAYS = 1.0
DUE_MAX_DAYS = 21.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# What the child did (§10.5)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Evidence:
    """One thing a child did in a story, read as evidence about one objective.

    Built from a `Beat` and its `Choice` — never from a test, because there is no
    test (§10.5). The beat says what was asked and how hard it was (its `learning`
    tag, stamped by the orchestrator from this module's own selection); the choice
    says what happened.

    `correct` is the one field here that could be mistaken for a grade, so: it is
    computed from `interaction.expected`, which exists so the *client* can offer
    the companion's hand on a miss (§8.5), and it is consumed only by
    `observation` below. No caller outside this module reads it, and nothing
    downstream of it reaches the child.
    """

    objective_code: str
    difficulty: float
    evidence_key: str
    correct: bool
    assisted: bool
    retries: int
    latency_ms: int | None
    at: datetime.datetime

    @property
    def struggled(self) -> bool:
        """Did this cost the child more than it should have? (§10.4)"""
        return not self.correct or self.assisted or self.retries > 0

    @property
    def easy(self) -> bool:
        """Was this well within them? — right, unaided, first go, and quick."""
        if self.struggled:
            return False
        return self.latency_ms is None or self.latency_ms <= QUICK_MS

    @property
    def hesitant(self) -> bool:
        return self.latency_ms is not None and self.latency_ms >= SLOW_MS


def observation(evidence: Evidence) -> float:
    """What one piece of evidence says the child's mastery is, on the 0-1 scale.

    Three rungs and no more, because with this much noise a fourth would be
    invented precision:

    * **clean success** → a little above the difficulty they handled;
    * **success with help, with a retry, or after a long pause** → the difficulty
      itself, which is what "right at their edge" means;
    * **no success** → a little below it.

    Note what is *not* in here: how many tries, how many milliseconds, how close
    the answer was. All of them are real signals and all of them are noisier than
    the three-way split, and §10.2's whole argument is that an over-fitted
    estimate of a four-year-old is worse than a coarse one.
    """
    if not evidence.correct:
        return _clamp(evidence.difficulty - EVIDENCE_MARGIN)
    if evidence.assisted or evidence.retries or evidence.hesitant:
        return _clamp(evidence.difficulty)
    return _clamp(evidence.difficulty + EVIDENCE_MARGIN)


def evidence_from(adventure: Any) -> list[Evidence]:
    """Every answerable thing the child did in one adventure, in order.

    A beat produces evidence when three things are true: it carries a learning tag
    naming an objective, it asked something with a single expected answer
    (`interaction.expected`), and the child answered it. Everything else in an
    adventure — the choices between two good paths, the taps that only mean "carry
    on" — is story, and reading it as evidence would be reading noise.

    The safe fallback beat is excluded by the same rule and not by a special case:
    it carries `objective_id: "none"` and asks for nothing (`safety.fallbacks`), so
    a sitting that degraded is a sitting with less evidence rather than a sitting
    that taught us something false.
    """
    from apps.story.models import Beat

    found: list[Evidence] = []
    beats = Beat.objects.filter(adventure=adventure).prefetch_related("choices").order_by("seq")
    for beat in beats:
        learning = beat.learning or {}
        code = str(learning.get("objective_id") or "").strip()
        if not code or code == NO_OBJECTIVE:
            continue

        expected = (beat.interaction or {}).get("expected")
        if expected is None:
            continue

        choice = next(iter(beat.choices.all()), None)
        if choice is None:
            continue

        # What the child answered is the `value` when the beat asked for one — a
        # number on the count pad — and the key they tapped when it asked them to
        # point at something. Both are the same act from the child's side; only the
        # control differs (§14.4).
        answered = choice.value if choice.value is not None else choice.chosen_key
        found.append(
            Evidence(
                objective_code=code,
                difficulty=_clamp(float(learning.get("difficulty") or 0.0)),
                evidence_key=str(learning.get("skill_evidence_key") or ""),
                correct=_matches(answered, expected),
                assisted=bool(choice.assisted),
                retries=int(choice.retries or 0),
                latency_ms=choice.latency_ms,
                at=choice.created_at,
            )
        )
    return found


def _matches(answer: Any, expected: Any) -> bool:
    """Did the child's answer meet the one the beat named?

    Tolerant in both directions on purpose. The count pad sends an int and a model
    may have written `"5"`; an option key arrives as a string and is compared as
    one. A comparison that cannot be made is `False` — which reads as "this was
    above them today" and costs the child a fraction of an estimate, never a word
    of narration.
    """
    if answer is None or expected is None:
        return False
    if isinstance(answer, bool) or isinstance(expected, bool):
        return bool(answer) is bool(expected)
    try:
        return float(answer) == float(expected)
    except (TypeError, ValueError):
        pass
    return str(answer).strip().casefold() == str(expected).strip().casefold()


# ---------------------------------------------------------------------------
# Within-session adaptation (§10.4)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Adaptation:
    """How hard the *next* learning beat of this adventure should be.

    `scaffold` and `stretch` are mutually exclusive and are about the beat's
    shape, not its number: scaffolding is the companion counting along and one
    fewer card, a stretch is reaching a rung further. Both are §10.4's "no child
    is ever stuck" — one of them stops a struggle becoming a wall, the other stops
    ease becoming boredom.
    """

    difficulty: float
    scaffold: bool
    stretch: bool


def adapt(adventure: Any) -> Adaptation:
    """Difficulty for the next ask, from what has happened in this sitting so far.

    Starts at the adventure's own difficulty — the estimate the selector set at
    plan time — and walks the evidence in order, stepping down on a struggle and
    up on ease. Walking rather than averaging, because a child who struggled twice
    should be met twice as gently, and because the *last* thing that happened is
    what the next beat has to answer to (§10.4).

    A success that was neither easy nor a struggle holds the difficulty and clears
    both flags: the child is exactly where we put them, which is the state this
    whole engine is trying to reach.
    """
    difficulty = float(adventure.difficulty)
    scaffold = stretch = False

    for item in evidence_from(adventure):
        if item.struggled:
            difficulty -= ADAPT_STEP
            scaffold, stretch = True, False
        elif item.easy:
            difficulty += ADAPT_STEP
            scaffold, stretch = False, True
        else:
            scaffold = stretch = False

    return Adaptation(
        difficulty=_clamp(difficulty, MIN_DIFFICULTY, MAX_DIFFICULTY),
        scaffold=scaffold,
        stretch=stretch,
    )


def demand_for_adventure(adventure: Any) -> Demand | None:
    """The concrete ask this adventure is currently making, or None.

    None when the adventure carries no objective — a story started before the
    catalog existed, or one whose objective code has since left it. Callers treat
    that as "no learning directive", which the assembler already renders as *keep
    the beat playful and let the story lead*; a missing objective must never be a
    missing beat.
    """
    objective = objective_by_code(adventure.primary_objective_id)
    if objective is None:
        return None

    adaptation = adapt(adventure)
    return demand_for(
        objective.code,
        objective.domain,
        adaptation.difficulty,
        scaffold=adaptation.scaffold,
        stretch=adaptation.stretch,
    )


# ---------------------------------------------------------------------------
# Objective selection (§10.3)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Selection:
    """What this sitting will quietly practise, and how hard."""

    objective: Objective
    difficulty: float

    @property
    def code(self) -> str:
        return self.objective.code

    @property
    def demand(self) -> Demand:
        """The concrete ask this selection implies at its starting difficulty.

        Never scaffolded and never a stretch: those are §10.4's *within*-session
        adaptation and there is no evidence yet — a sitting begins where the
        estimate says, and moves from there (`adapt`).
        """
        return demand_for(self.objective.code, self.objective.domain, self.difficulty)


def objective_by_code(code: str | None) -> Objective | None:
    if not code:
        return None
    return Objective.objects.filter(code=code).first()


def progress_for(child: Child, objective: Objective) -> LearningProgress:
    """This child's row for this objective, created on first use."""
    progress, _ = LearningProgress.objects.get_or_create(child=child, objective=objective)
    return progress


def starting_difficulty(progress: LearningProgress) -> float:
    """Where a sitting on this objective begins (§10.4).

    "Difficulty starts at the child's estimate" — plus a small reach, because the
    estimate is what they can already do and the point is the *next* step. A child
    who has never met the objective starts near the floor, which is what makes a
    first adventure gentle without anything having to special-case it.
    """
    return _clamp(progress.mastery + ZPD_STRETCH, MIN_DIFFICULTY, MAX_DIFFICULTY)


def select_objective(child: Child) -> Selection | None:
    """What this child should practise next, and how hard (§10.3).

    Balances the three things §10.3 names — due for review, edge of ability,
    interest fit — against the two it warns about: over-repetition and staying in
    one domain. The arithmetic is in `score_objective`; what happens here is the
    gating, which is the part with consequences:

    * **Age.** Outside the band the objective is not offered at all.
    * **Prerequisites.** `count_to_10` is not a candidate for a child who is still
      finding five hard, whatever the other terms say. This is the one gate that
      is absolute, because the alternative is a story that asks a three-year-old
      for something they cannot do and a companion who has to rescue them from it.

    Returns None only when the catalog has nothing for this child's age at all —
    a two-year-old, or an empty catalog. Callers degrade to a story with no
    learning directive rather than to no story (`demand_for_adventure`).
    """
    now = timezone.now()
    age = child.age

    catalog = list(Objective.objects.filter(min_age__lte=age, max_age__gte=age))
    if not catalog:
        logger.warning("learning.no_objectives_for_age child=%s age=%d", child.pk, age)
        return None

    rows = LearningProgress.objects.filter(child=child).select_related("objective")
    progress: dict[str, LearningProgress] = {row.objective.code: row for row in rows}

    unlocked = [item for item in catalog if _unlocked(item, progress)]
    if not unlocked:
        # Every age-appropriate objective is gated behind something this child has
        # not done yet, which can only happen if the catalog's chains are longer
        # than its entry points. Falling back to the whole set keeps a story
        # possible; the log line is what makes the seeding mistake findable.
        logger.warning("learning.all_objectives_locked child=%s age=%d", child.pk, age)
        unlocked = catalog

    interests = _interest_tokens(child)
    last_domain = _last_domain(child)

    def rank(objective: Objective) -> tuple[float, int, int, str]:
        row = progress.get(objective.code) or LearningProgress(child=child, objective=objective)
        score = score_objective(
            objective, row, interests=interests, last_domain=last_domain, now=now
        )
        # Deterministic all the way down: score, then the domain order §10.1 sets,
        # then the gentlest objective, then the code. Two children with identical
        # histories get identical stories, which is what makes any of this testable.
        order = DOMAIN_ORDER.index(objective.domain) if objective.domain in DOMAIN_ORDER else 99
        return (-score, order, objective.min_age, objective.code)

    chosen = min(unlocked, key=rank)
    row = progress.get(chosen.code) or LearningProgress(child=child, objective=chosen)
    difficulty = starting_difficulty(row)

    logger.info(
        "learning.objective_selected child=%s objective=%s difficulty=%.2f mastery=%.2f "
        "due=%d last_domain=%s candidates=%d",
        child.pk,
        chosen.code,
        difficulty,
        row.mastery,
        int(row.is_due(now=now)),
        last_domain or "-",
        len(unlocked),
    )
    return Selection(objective=chosen, difficulty=difficulty)


def score_objective(
    objective: Objective,
    progress: LearningProgress,
    *,
    interests: frozenset[str],
    last_domain: str = "",
    now: datetime.datetime | None = None,
) -> float:
    """How much this child should practise this objective right now (§10.3).

    Pure and unweighted by anything stored, so a reviewer can read the four terms
    and predict the pick. Higher is better; the terms are documented at their
    constants.
    """
    now = now or timezone.now()
    return (
        ZPD_WEIGHT * zpd_score(progress.mastery)
        + DUE_WEIGHT * due_score(progress, now=now)
        + INTEREST_WEIGHT * interest_fit(objective, interests)
        - REPEAT_PENALTY * recency_penalty(progress, now=now)
        - INTERLEAVE_PENALTY * (1.0 if objective.domain == last_domain else 0.0)
    )


def zpd_score(mastery: float) -> float:
    """1.0 inside the 0.4-0.7 band, falling off outside it (§10.3)."""
    if mastery < ZPD_FLOOR:
        return _clamp(1.0 - BELOW_BAND_DECAY * (ZPD_FLOOR - mastery))
    if mastery > ZPD_CEILING:
        return _clamp(1.0 - ABOVE_BAND_DECAY * (mastery - ZPD_CEILING))
    return 1.0


def due_score(progress: LearningProgress, *, now: datetime.datetime) -> float:
    """0 until spaced retrieval comes round, then 0.5 rising to 1.0 over a week."""
    if not progress.is_due(now=now):
        return 0.0
    assert progress.due_at is not None  # `is_due` is False without one
    overdue_days = (now - progress.due_at).total_seconds() / 86_400.0
    return 0.5 + 0.5 * _clamp(overdue_days / OVERDUE_SATURATION_DAYS)


def recency_penalty(progress: LearningProgress, *, now: datetime.datetime) -> float:
    """1.0 for something practised moments ago, decaying to 0 over two days.

    §10.3's "avoids repeating the same objective too often", and the reason it is
    a decay rather than a hard block: a child who plays twice in one evening should
    get two different objectives, and a child who plays every Saturday should not
    be pushed off the thing they are in the middle of learning.
    """
    if progress.last_practiced_at is None:
        return 0.0
    hours = (now - progress.last_practiced_at).total_seconds() / 3600.0
    return _clamp(1.0 - hours / REPEAT_WINDOW_HOURS)


def interest_fit(objective: Objective, interests: frozenset[str]) -> float:
    """Whether this objective is expressible in what the child currently loves.

    Word overlap between the objective's description and the child's live interest
    vocabulary (`_interest_tokens`), and it is weighted as the tie-break it is
    (`INTEREST_WEIGHT`).

    Deliberately weak, and worth saying why rather than dressing it up: at MVP both
    domains are expressible in *any* world — you can count dinosaurs as easily as
    you can count frogs — so a strong interest term would be a strong signal about
    nothing.

    Day 11 changed what is in the set and deliberately did not change that. The
    tokens now come from the interest graph rather than from an onboarding text box,
    so this term moves with the child; what §11.7's graph is actually load-bearing
    for is which *world* the objective is dressed in (`story.worlds`), because that
    is where personalisation belongs. Curriculum belongs at the child's edge (§10.3),
    and a child who loves frogs still needs the number after four.
    """
    if not interests:
        return 0.0
    words = _tokens(objective.description)
    return 1.0 if words & interests else 0.0


def _unlocked(objective: Objective, progress: Mapping[str, LearningProgress]) -> bool:
    """Are this objective's prerequisites roughly in hand? (`PREREQUISITE_MASTERY`)"""
    for code in objective.prerequisites or []:
        row = progress.get(str(code))
        if row is None or row.mastery < PREREQUISITE_MASTERY:
            return False
    return True


def _last_domain(child: Child) -> str:
    """The domain of this child's most recent adventure, for interleaving (§10.3).

    Read off `Adventure.primary_objective_id` rather than off `LearningProgress`,
    because what we are interleaving is *sittings*: an objective selected for a
    story the child abandoned after one beat still made that story a numeracy
    story, and following it with another one is exactly the monotony this term
    exists to break.
    """
    from apps.story.models import Adventure

    code = (
        Adventure.objects.filter(child=child)
        .exclude(primary_objective_id__isnull=True)
        .exclude(primary_objective_id="")
        .order_by("-started_at")
        .values_list("primary_objective_id", flat=True)
        .first()
    )
    if not code:
        return ""
    return Objective.objects.filter(code=code).values_list("domain", flat=True).first() or ""


def _interest_tokens(child: Child) -> frozenset[str]:
    """What this child loves, as words the `interest_fit` term can match (§11.7).

    Day 11's landing site. This used to tokenise `Child.interests` — the onboarding
    text box — so the term could only ever say what was true on the day a parent
    filled it in. It now reads the live interest graph, which merges those seeds
    with what the child has actually reached for and ages both (`memory.interest`),
    so the same child scores differently a month later.

    Imported inside the function, like `_last_domain`'s `Adventure`: this module is
    the pure arithmetic of §10 and must not gain an import-time dependency on the
    memory app to compute a tie-break.
    """
    from apps.memory import interest

    return interest.selector_tokens(child)


#: Words that identify nothing on their own, so an overlap on one of them is not
#: an overlap at all. Short and closed rather than a stopword corpus, because the
#: two things being compared are a parent's three-word interest ("the sea") and a
#: one-sentence objective description — and both are almost entirely content.
#:
#: This list is not decoration. Without it, a child whose interests included "the
#: sea" matched every objective whose description contained the word "the", which
#: is all of them, and the interest term decided the selection instead of breaking
#: its ties.
_STOPWORDS = frozenset(
    """and are its the them they thing things this that with what when out
    about back over into from for""".split()
)


def _tokens(text: str) -> frozenset[str]:
    """Lowercase content words of three letters or more, singularised crudely.

    Crude on purpose — "frogs" has to match "frog" and nothing here is worth a
    stemming dependency. The cost of a miss is one tie broken the other way.
    """
    letters = "".join(char if char.isalpha() else " " for char in text.casefold())
    words = (
        word[:-1] if word.endswith("s") and len(word) > 3 else word for word in letters.split()
    )
    return frozenset(word for word in words if len(word) >= 3 and word not in _STOPWORDS)


# ---------------------------------------------------------------------------
# The estimate moves (§10.5)
# ---------------------------------------------------------------------------
def next_due(mastery: float, *, at: datetime.datetime) -> datetime.datetime:
    """When to come back to something known this well (§10.3's spaced retrieval)."""
    days = DUE_MIN_DAYS + (DUE_MAX_DAYS - DUE_MIN_DAYS) * _clamp(mastery)
    return at + datetime.timedelta(days=days)


@transaction.atomic
def apply_evidence(child: Child, evidence: Iterable[Evidence]) -> dict[str, LearningProgress]:
    """Fold a sitting's evidence into the skill model. Returns what moved, by code.

    Grouped by objective and applied in time order, because the update is a
    running estimate and running estimates are not commutative — two answers at
    different difficulties say different things depending on which came second.

    The row is locked for the read-modify-write. Two sittings finishing at once is
    not hypothetical (a tablet and a phone, one child), and without the lock the
    second would compute its new mastery from a value the first had already
    replaced — silently losing an afternoon of evidence.

    An objective code that is no longer in the catalog is logged and skipped
    rather than created: a beat is a durable record of what a child practised, and
    a catalog we have since changed is our problem, not a reason to invent a row.
    """
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.objective_code].append(item)

    moved: dict[str, LearningProgress] = {}
    for code in sorted(grouped):
        objective = objective_by_code(code)
        if objective is None:
            logger.warning("learning.unknown_objective child=%s code=%s", child.pk, code)
            continue

        progress, _ = LearningProgress.objects.select_for_update().get_or_create(
            child=child, objective=objective
        )
        items = sorted(grouped[code], key=lambda item: item.at)
        before = progress.mastery

        for item in items:
            _observe(progress, item)

        progress.evidence_count += len(items)
        progress.last_practiced_at = items[-1].at
        progress.due_at = next_due(progress.mastery, at=items[-1].at)
        progress.save(
            update_fields=[
                "mastery",
                "uncertainty",
                "evidence_count",
                "last_practiced_at",
                "due_at",
                "updated_at",
            ]
        )
        logger.info(
            "learning.mastery child=%s objective=%s evidence=%d mastery=%.2f->%.2f "
            "uncertainty=%.2f due_in_days=%.1f",
            child.pk,
            code,
            len(items),
            before,
            progress.mastery,
            progress.uncertainty,
            (progress.due_at - items[-1].at).total_seconds() / 86_400.0,
        )
        moved[code] = progress
    return moved


def _observe(progress: LearningProgress, evidence: Evidence) -> None:
    """One observation, folded into the estimate in place (§10.2, §10.5).

    Four lines, and each one is a sentence:

        the estimate moves part-way toward what we just saw,
        by an amount that is how unsure we were,
        and we are a little less unsure afterwards,
        down to a floor, because children change.
    """
    rate = _clamp(progress.uncertainty, MIN_RATE, MAX_RATE)
    target = observation(evidence)
    progress.mastery = _clamp(progress.mastery + rate * (target - progress.mastery))
    progress.uncertainty = max(MIN_UNCERTAINTY, progress.uncertainty * UNCERTAINTY_DECAY)


def update_from_adventures(child: Child, adventures: Sequence[Any]) -> dict[str, LearningProgress]:
    """Every adventure in one sitting, folded into the skill model together.

    One call rather than one per adventure so the ordering above holds across the
    whole sitting, and so a child who played two stories in an evening gets one
    coherent update rather than two that race.
    """
    collected: list[Evidence] = []
    for adventure in adventures:
        collected.extend(evidence_from(adventure))
    if not collected:
        return {}
    return apply_evidence(child, collected)
