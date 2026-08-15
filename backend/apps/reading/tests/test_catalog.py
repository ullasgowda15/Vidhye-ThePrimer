"""The join between the phonics catalog and the engine that produces its evidence.

`Objective.code` for a literacy row *is* a skill id in
`apps.reading.engine.content.skills`. Nothing enforces that at the type level —
one is a database string, the other a dict key — and the two live in different
halves of the codebase for good reasons: the engine must stay framework-free
(§5.1), so it cannot import the model, and the migration must stay frozen
content, so it cannot import the engine.

Which leaves this test as the only thing holding them together. If they drift, a
child's `LearningProgress` detaches from the thing that generated it: the engine
records evidence against `digraph_sh` and the selector has no such objective, and
neither side raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.learning.models import Domain, Objective
from apps.reading.engine.content import skills

pytestmark = pytest.mark.django_db


def _literacy_codes() -> set[str]:
    return set(Objective.objects.filter(domain=Domain.LITERACY).values_list("code", flat=True))


def test_the_phonics_catalog_is_seeded_by_migration() -> None:
    assert len(_literacy_codes()) == 10


def test_every_seeded_code_is_a_skill_the_engine_knows() -> None:
    """The direction that breaks selection.

    An objective whose code the engine does not recognise can be chosen, stamped
    on a beat, and then never credited — because nothing the child reads will
    ever attribute to it.
    """
    unknown = _literacy_codes() - set(skills.SKILLS)

    assert not unknown, f"seeded objectives with no matching skill: {sorted(unknown)}"


def test_every_skill_the_engine_teaches_has_an_objective() -> None:
    """The direction that breaks reporting.

    A skill with no catalog row still gets detected and still accumulates
    evidence, and none of it can be shown to a parent or scheduled for review —
    it becomes progress the product holds and cannot use.
    """
    unseeded = set(skills.SKILLS) - _literacy_codes()

    assert not unseeded, f"skills with no objective row: {sorted(unseeded)}"


def test_the_catalog_chain_follows_the_ufli_sequence() -> None:
    """A prerequisite must never point later in the teaching order than its own
    objective, or the curriculum gates a skill behind one taught after it."""
    order = {code: skills.SKILLS[code]["ufli_order"] for code in skills.SKILLS}
    rows = Objective.objects.filter(domain=Domain.LITERACY)

    for objective in rows:
        for prerequisite in objective.prerequisites:
            assert order[prerequisite] < order[objective.code], (
                f"{objective.code} is gated behind {prerequisite}, " "which UFLI teaches later"
            )


def test_no_reading_objective_is_offered_below_five() -> None:
    """The age gate, as data.

    §5.3 of the programme brief: no child under five is ever asked to read. This
    is half of how that is true — `engine.select_objective` filters the catalog
    on `min_age__lte=age`, so a four-year-old cannot reach these rows by any
    path, including one nobody has written yet.
    """
    for objective in Objective.objects.filter(domain=Domain.LITERACY):
        assert objective.min_age >= 5, objective.code


def test_the_phonics_catalog_does_not_leak_into_the_parent_dashboard(child: Any) -> None:
    """Seeded, and invisible until there is something to report.

    Seeding literacy put four milestones into `analytics.skills.milestone_rollup`,
    where they read "Not covered yet" — an accurate description of a feature that
    does not exist. A parent opening Insights would have found a roadmap.

    The dashboard filters on `engine.SELECTABLE_DOMAINS`, the same constant the
    selector uses, so the two cannot drift apart. Phase 4 removes literacy from
    that constant and this test should then be inverted rather than deleted.
    """
    from apps.analytics import skills as analytics_skills

    domains = {view.domain for view in analytics_skills.skill_views(child)}
    assert Domain.LITERACY not in domains

    rolled_up = {view.domain for view in analytics_skills.milestone_rollup(child)}
    assert Domain.LITERACY not in rolled_up
