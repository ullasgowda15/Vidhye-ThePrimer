"""The seeded curriculum (PRODUCT.md §10.1, §10.6, §17.3, §17.5).

These assert on the *migration*, not on a fixture. The catalog is content that
ships, and a suite that built its own objectives would go green against an empty
seed — which is the one way this day's first acceptance criterion can fail
silently in production and nowhere else.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.learning import engine
from apps.learning.demands import KIND_BY_CODE
from apps.learning.models import Domain, Milestone, Objective

pytestmark = pytest.mark.django_db


def test_the_catalog_is_seeded_by_migration() -> None:
    codes = set(Objective.objects.values_list("code", flat=True))

    # §10.1's MVP: numeracy done properly, vocabulary alongside it.
    assert {"count_to_3", "count_to_5", "count_to_10", "more_fewer"} <= codes
    assert len(codes & {"name_colours", "size_words", "feeling_words"}) >= 3


def test_no_domain_beyond_numeracy_and_vocabulary_can_be_selected() -> None:
    """§10.1 ships *one* domain done well plus opportunistic vocabulary.

    This used to assert on what was *seeded*, and the distinction now matters.
    `0004_seed_literacy` seeds a third domain — the ten phonics objectives, so a
    curriculum person can read them and so an offline analysis can write progress
    against them — while nothing can yet render a reading ask.

    The concern the original test protected is unchanged and is what is asserted
    here instead: **an objective that can be selected and cannot be taught.**
    `engine.SELECTABLE_DOMAINS` is the gate, and Phase 4 of
    Docs/Build/Reading-Integration.md is what opens it.
    """
    assert engine.SELECTABLE_DOMAINS == {Domain.NUMERACY, Domain.VOCABULARY}

    selectable = set(
        Objective.objects.filter(domain__in=engine.SELECTABLE_DOMAINS).values_list(
            "domain", flat=True
        )
    )
    assert selectable == {Domain.NUMERACY, Domain.VOCABULARY}


def test_a_reading_objective_is_never_handed_to_a_child(child: Any) -> None:
    """The gate, exercised rather than asserted about.

    A five-year-old is inside every literacy objective's age band, so age alone
    does not keep them out. Only `SELECTABLE_DOMAINS` does.
    """
    literacy = set(Objective.objects.filter(domain=Domain.LITERACY).values_list("code", flat=True))
    assert literacy, "the phonics catalog should be seeded"

    for _ in range(20):
        selection = engine.select_objective(child)
        assert selection is not None
        assert selection.objective.code not in literacy
        assert selection.objective.domain != Domain.LITERACY


def test_every_objective_rolls_up_to_a_milestone_with_a_standard_reference() -> None:
    """§10.6: a parent hears "counting to ten", never `count_to_10 = 0.62`."""
    for objective in Objective.objects.select_related("milestone"):
        milestone = objective.milestone
        assert milestone is not None, objective.code
        assert milestone.domain == objective.domain, objective.code
        assert milestone.name and milestone.description, objective.code
        # The seed of future standards/curriculum alignment (§25). Unused today;
        # retro-fitting it onto a live catalog would be a data migration.
        assert milestone.standard_ref, objective.code
        assert all(milestone.standard_ref.values()), objective.code


def test_every_prerequisite_names_an_objective_that_exists() -> None:
    """A chain is only a curriculum if every link resolves.

    A prerequisite naming a code that was never seeded would gate its objective
    forever: `engine._unlocked` finds no progress row for it, so the objective can
    never be selected and no child would ever be told why.
    """
    codes = set(Objective.objects.values_list("code", flat=True))
    for objective in Objective.objects.all():
        for prerequisite in objective.prerequisites:
            assert prerequisite in codes, f"{objective.code} -> {prerequisite}"
            assert prerequisite != objective.code


def test_the_counting_chain_is_ordered() -> None:
    """Three before five before ten — the sequence is data, not code (§10.3)."""
    by_code = {row.code: row for row in Objective.objects.all()}

    assert by_code["count_to_3"].prerequisites == []
    assert "count_to_3" in by_code["count_to_5"].prerequisites
    assert "count_to_5" in by_code["count_to_10"].prerequisites


def test_every_objective_has_a_shape_a_beat_can_actually_ask() -> None:
    """Catalog and demand translator have to stay in step (§8.9).

    An objective with no entry in `KIND_BY_CODE` falls back to its domain's usual
    shape, which is a safe default and a silent one — a vocabulary objective about
    rhyme would be asked as "point at the thing this word describes" and nobody
    would notice. This is the assertion that makes adding a row to the seed a
    two-file change on purpose.
    """
    for code in Objective.objects.values_list("code", flat=True):
        assert code in KIND_BY_CODE, code


def test_age_bands_are_the_right_way_round_and_cover_the_audience() -> None:
    """The Primer is for 3-7 (§1), so something has to exist at each end."""
    rows = list(Objective.objects.all())
    assert all(row.min_age <= row.max_age for row in rows)
    assert any(row.min_age <= 3 for row in rows), "nothing for a three-year-old"
    assert any(row.max_age >= 7 for row in rows), "nothing for a seven-year-old"


def test_a_milestone_cannot_be_deleted_out_from_under_its_objectives() -> None:
    """`on_delete=PROTECT` (§17.3): objectives roll up, and the rollup must hold."""
    from django.db.models import ProtectedError

    milestone = Milestone.objects.get(name="Counting and quantity")
    with pytest.raises(ProtectedError):
        milestone.delete()
