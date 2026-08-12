"""The skill model, read the way a parent should read it (PRODUCT.md §12.3, §10.6).

`learning.LearningProgress` holds four numbers per child per objective. This
module turns them into the only things §12.3 permits a parent to be shown: a word
("emerging", "practising", "mastered"), a plain-language sentence, and the
milestone the objective rolls up to. **No grades, no percentiles, no comparison to
other children** — none of those exist anywhere in this file, because §12.3 says a
parent surface that induces anxiety or competition is a worse product, not a
richer one.

It reads the skill model and never writes it, and it never recomputes it. §10.2 is
the source of truth for what a child can do; this is a translation layer over it.

---

**The question this module exists to answer**, logged at the end of Day 10 and
deferred to today: *a child who counts to three perfectly asymptotes at ~0.64
mastery, so does the dashboard show mastery per objective or per ladder?*

Neither, and the reason the question was hard is that it assumed the number would
be shown at all. It is not. What is shown is a **status relative to the objective's
own reach**, and `reach_for` is what makes that honest.

Mastery is on the same 0-1 scale as *difficulty*, and difficulty means "how hard an
ask", not "how well are they doing". `count_to_3` cannot ask for more than three
things (`demands.COUNT_CEILING`), three things is a 0.44-difficulty ask, and a
clean success is worth `EVIDENCE_MARGIN` above the ask — so 0.64 is not a
three-quarters-of-the-way-there child, it is a child who has topped out this
objective. Showing 0.64, or "64%", or a bar filled to 64%, would tell a parent
their child is middling at counting to three when they have finished counting to
three. Dividing by the reach and thresholding gives the true answer, and gives it
in words.

`reach_for` asks `demands.demand_for` rather than reading `COUNT_CEILING` itself,
so a new objective, a new ladder rung or a changed ceiling moves this file's
answers with it. A translation layer that had its own copy of the curriculum's
arithmetic would be wrong the first time somebody edited the curriculum.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.learning.demands import demand_for
from apps.learning.engine import EVIDENCE_MARGIN, MAX_DIFFICULTY
from apps.learning.models import LearningProgress, Objective
from apps.profiles.models import Child

#: The three words §12.3 allows, plus the honest fourth. `NOT_STARTED` is not a
#: judgement and is not hidden: "we have not covered this yet" is information a
#: parent is entitled to, and a dashboard that silently omitted untouched
#: objectives would imply a curriculum smaller than the one we have.
NOT_STARTED = "not_started"
EMERGING = "emerging"
PRACTISING = "practising"
MASTERED = "mastered"

#: Where the bands sit, as a fraction of what the objective can reach.
#:
#: Fractions rather than absolute mastery values, because the denominator differs
#: per objective — see the module docstring. 0.45 is roughly "has met this and is
#: getting somewhere"; 0.85 is "does this reliably at the hardest version we ask".
#: Neither is a precise claim and neither is shown; they are where one word gives
#: way to the next.
EMERGING_FROM = 0.45
MASTERED_FROM = 0.85

#: How much evidence before a status means anything. One lucky tap is not a skill,
#: and a parent told "mastered" after a single beat would be right to stop
#: believing the dashboard. Below this the status is capped at `EMERGING`.
MIN_EVIDENCE_FOR_MASTERY = 3


@dataclass(frozen=True, slots=True)
class SkillView:
    """One objective as a parent reads it (§12.3)."""

    code: str
    domain: str
    #: §10.6's plain language. The milestone name, never the objective code.
    milestone: str
    description: str
    status: str
    #: How far along its prerequisite chain this objective sits (`chain_depth`).
    #: Not on the wire — it exists so "the furthest thing they can do" is decided
    #: by the curriculum rather than by alphabetical order, which would rank
    #: `count_to_10` before `count_to_3`.
    depth: int
    #: The raw estimate and the highest this objective can reach, both carried on
    #: the wire because the brief's `/progress` shape names `mastery` and because a
    #: number without its own ceiling is the exact thing this module argues is
    #: unreadable. **Nothing renders either of them**; the parent surface shows
    #: `status` and `summary` (see `frontend/src/routes/parent/Insights.tsx`).
    mastery: float
    reach: float
    evidence_count: int
    summary: str


def reach_for(code: str, domain: str) -> float:
    """The highest mastery this objective's own asks can ever produce.

    The hardest ask the engine will make (`MAX_DIFFICULTY`), put through the demand
    translator so the objective's ceiling is applied, plus what a clean success at
    that ask is worth (`EVIDENCE_MARGIN`). For `count_to_3` that is 0.64; for
    `count_to_10` it is 1.0.

    Asked of `demands` rather than derived here on purpose — see the module
    docstring.
    """
    hardest = demand_for(code, domain, MAX_DIFFICULTY)
    return min(1.0, max(0.0, hardest.difficulty + EVIDENCE_MARGIN))


def chain_depth(
    code: str, prerequisites: dict[str, list[str]], _seen: frozenset[str] = frozenset()
) -> int:
    """How many links of `Objective.prerequisites` lead to this one.

    `count_to_3` is 0, `count_to_5` is 1, `count_to_10` is 2. This is the order a
    curriculum climbs in, and it is the only ordering under which "the furthest
    thing they can do" is a true phrase.

    `_seen` guards a cycle in the seeded chains. A cycle is a seeding mistake
    rather than a runtime condition, and the parent's dashboard is not the place
    to discover it by recursing until Python gives up.
    """
    if code in _seen:
        return 0
    parents = prerequisites.get(code) or []
    if not parents:
        return 0
    return 1 + max(chain_depth(str(parent), prerequisites, _seen | {code}) for parent in parents)


def status_for(progress: LearningProgress, *, reach: float | None = None) -> str:
    """One of the four words, from one child's row for one objective.

    Two guards, and both are about not overclaiming. An objective with no evidence
    is `NOT_STARTED` however the estimate was seeded, and an objective with less
    than `MIN_EVIDENCE_FOR_MASTERY` observations cannot read higher than
    `EMERGING` — a child who happened to get the first one right has told us
    something, but not that they can do it.
    """
    if progress.evidence_count <= 0:
        return NOT_STARTED

    ceiling = reach
    if ceiling is None:
        ceiling = reach_for(progress.objective.code, progress.objective.domain)
    fraction = progress.mastery / ceiling if ceiling > 0 else 0.0

    if progress.evidence_count < MIN_EVIDENCE_FOR_MASTERY:
        return EMERGING
    if fraction >= MASTERED_FROM:
        return MASTERED
    if fraction >= EMERGING_FROM:
        return PRACTISING
    return EMERGING


def summarise(objective: Objective, status: str, evidence_count: int) -> str:
    """One sentence a parent could repeat to a grandparent (§12.3).

    Developmental and positive, per §12.3, and specific enough to be worth reading:
    it names the skill in the objective's own words rather than saying "making
    progress". Never comparative, never a number, and never a word for falling
    behind — the vocabulary here has no failure state, because at three to seven
    there is no such thing as behind schedule for a skill nobody has taught yet.
    """
    skill = objective.description.rstrip(".").lower()
    if status == NOT_STARTED:
        return f"Not covered yet — this one is about learning to {skill}."
    if status == EMERGING:
        seen = "once so far" if evidence_count == 1 else f"{evidence_count} times so far"
        return f"Just starting to {skill}. We have seen this {seen}."
    if status == PRACTISING:
        return f"Getting there — can often {skill}, and still likes a hand with the harder ones."
    return f"Does this reliably now: can {skill}."


def skill_views(child: Child) -> list[SkillView]:
    """Every objective this child could be offered, with where they are on it.

    Everything at or below their age, so the list is the curriculum they are
    actually inside rather than the whole catalog — and objectives they have aged
    *past* stay in, because a skill mastered at four is still a skill at six and
    dropping it would make the dashboard look like it forgot.

    Objectives with no progress row are included at `NOT_STARTED` rather than
    omitted; see `NOT_STARTED`.
    """
    catalog = list(Objective.objects.filter(min_age__lte=child.age).select_related("milestone"))
    # Depth is read off the *whole* catalog, not the age-filtered slice: an
    # objective's position in its chain is a property of the curriculum, and
    # slicing the prerequisite map by age would flatten a chain whose first link
    # the child has aged into but whose root is below them.
    prerequisites = {
        code: list(prereqs)
        for code, prereqs in Objective.objects.values_list("code", "prerequisites")
    }
    rows = {
        row.objective_id: row
        for row in LearningProgress.objects.filter(child=child).select_related("objective")
    }

    views: list[SkillView] = []
    for objective in catalog:
        progress = rows.get(objective.pk) or LearningProgress(child=child, objective=objective)
        reach = reach_for(objective.code, objective.domain)
        status = status_for(progress, reach=reach)
        views.append(
            SkillView(
                code=objective.code,
                domain=objective.domain,
                milestone=objective.milestone.name,
                description=objective.description,
                status=status,
                depth=chain_depth(objective.code, prerequisites),
                mastery=round(progress.mastery, 3),
                reach=round(reach, 3),
                evidence_count=progress.evidence_count,
                summary=summarise(objective, status, progress.evidence_count),
            )
        )
    # Domain, then the order the curriculum climbs in — which is the order a
    # parent reads a chain: three, then five, then ten.
    views.sort(key=lambda view: (view.domain, view.depth, view.code))
    return views


@dataclass(frozen=True, slots=True)
class MilestoneView:
    """A developmental step, with the objectives under it (§10.6, §12.5)."""

    name: str
    domain: str
    description: str
    status: str
    #: The furthest thing in this milestone the child does reliably, in plain
    #: language — "can count up to five visible things" — or empty.
    furthest: str
    skills: list[SkillView]


def milestone_rollup(child: Child) -> list[MilestoneView]:
    """§10.6's rollup: objectives grouped into steps a parent would recognise.

    The milestone's status is the **least advanced** of the objectives under it,
    which is the reading that makes "Counting and quantity: mastered" mean
    counting to ten rather than counting to three. It is paired with `furthest`,
    which never goes backwards, because the pair is what stops the honest reading
    from being a discouraging one: on the child's fourth birthday `count_to_10`
    joins their curriculum and the milestone drops from mastered to practising —
    true, and unreadable on its own. Next to "can count up to five visible
    things", it reads as what it is: a new step appearing, not a skill lost.
    """
    order = {NOT_STARTED: 0, EMERGING: 1, PRACTISING: 2, MASTERED: 3}
    grouped: dict[str, list[SkillView]] = {}
    for view in skill_views(child):
        grouped.setdefault(view.milestone, []).append(view)

    milestones = {
        objective.milestone.name: objective.milestone
        for objective in Objective.objects.filter(min_age__lte=child.age).select_related(
            "milestone"
        )
    }

    rollup: list[MilestoneView] = []
    for name, skills in grouped.items():
        milestone = milestones[name]
        status = min(skills, key=lambda view: order[view.status]).status
        # `skills` is already in curriculum order, so the last mastered one is the
        # furthest along the chain rather than the last one alphabetically.
        mastered = [view for view in skills if view.status == MASTERED]
        rollup.append(
            MilestoneView(
                name=name,
                domain=milestone.domain,
                description=milestone.description,
                status=status,
                furthest=(
                    f"can {mastered[-1].description.rstrip('.').lower()}" if mastered else ""
                ),
                skills=skills,
            )
        )
    rollup.sort(key=lambda view: (view.domain, view.name))
    return rollup
