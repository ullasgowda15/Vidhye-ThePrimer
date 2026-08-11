"""Entity keys are canonical, whichever half proposed them (§11.7).

The Day 8 review's P1: `companion:ruby` and `companion:Ruby` in the same child's
memories, which is one companion stored as two graph nodes. These tests are
written against the *observed* fork rather than an invented one.
"""

from __future__ import annotations

import pytest

from apps.memory import entities
from apps.memory.salience import Candidate, Signals

pytestmark = pytest.mark.django_db


def test_two_spellings_of_one_companion_become_one_key() -> None:
    """The exact fork the review found in the founder's data."""
    assert entities.normalise(["companion:ruby", "companion:Ruby"]) == ["companion:ruby"]


def test_the_model_half_and_the_rules_half_agree_on_a_key() -> None:
    """Both write paths run the same normaliser, so they cannot disagree.

    The rules half reads `continuity_refs` written by the story model; the model
    half reads the consolidation prompt's answer. Day 8 trusted both.
    """
    from_rules = entities.normalise(["companion:Ruby"])
    from_model = entities.normalise(["Companion: RUBY"])
    assert from_rules == from_model == ["companion:ruby"]


def test_interest_is_reconciled_onto_character() -> None:
    """`interest:Elephant` and `character:elephant` were the same elephant."""
    assert entities.normalise(["interest:Elephant", "character:elephant"]) == ["character:elephant"]


def test_an_unknown_namespace_is_dropped_not_guessed_at(caplog) -> None:
    """A ref we cannot place is an edge we cannot query — worse stored than absent."""
    with caplog.at_level("INFO"):
        assert entities.normalise(["quux:thing", "companion:pip"]) == ["companion:pip"]
    assert "memory.entities_dropped" in caplog.text
    # The log names the namespace, never the value — this column is child data.
    assert "thing" not in caplog.text


def test_a_bare_token_with_no_namespace_is_dropped() -> None:
    assert entities.normalise(["elephant", "companion:pip"]) == ["companion:pip"]


def test_the_machine_written_namespace_survives_untouched() -> None:
    """`objective:count_to_3` is written by our own code and must not be mangled."""
    assert entities.normalise(["objective:count_to_3"]) == ["objective:count_to_3"]


def test_order_is_preserved_and_duplicates_collapse() -> None:
    assert entities.normalise(
        ["world:Sunny Path", "companion:Ruby", "world:sunny_path", "companion:ruby"]
    ) == ["world:sunny_path", "companion:ruby"]


def test_spacing_and_punctuation_collapse_to_one_key() -> None:
    """A model writing the same thing three ways still gets one node."""
    assert entities.normalise(
        ["character:Big Bear", "character:big-bear", "character:big_bear"]
    ) == ["character:big_bear"]


def test_a_key_that_is_all_punctuation_is_dropped() -> None:
    assert entities.normalise(["character:???"]) == []


def test_keys_are_length_capped() -> None:
    key = entities.normalise([f"character:{'a' * 400}"])[0]
    assert len(key) <= entities.MAX_KEY_LENGTH


def test_normalising_is_idempotent() -> None:
    """Consolidation and the backfill migration both run it; twice must equal once."""
    once = entities.normalise(["companion:Ruby", "interest:Elephant", "world:Sunny Path"])
    assert entities.normalise(once) == once


def test_a_candidates_entities_reach_the_row_canonical(aarav, closed) -> None:
    """End to end: nothing between a proposal and the column re-forks the key."""
    from apps.memory import consolidation
    from apps.memory.models import Memory

    consolidation.consolidate_session(closed)
    stored = {key for memory in Memory.objects.all() for key in memory.entities}
    assert stored, "expected at least one entity key from a played session"
    for key in stored:
        namespace, separator, value = key.partition(":")
        assert separator, f"{key!r} has no namespace"
        assert namespace in entities.NAMESPACES, f"{namespace!r} is not a graph namespace"
        assert value == value.casefold(), f"{key!r} is not casefolded"


def test_repetition_counts_the_canonical_key() -> None:
    """The point of the fix: a forked key halved the factor it feeds (§11.4).

    Two spellings of one companion must count as two sightings of the *same*
    thing, not one sighting each of two things.
    """
    from collections import Counter

    from apps.memory import candidates as rules

    scored = rules.repetition_of(
        entities.normalise(["companion:Ruby"]), Counter({"companion:ruby": 2}), 2
    )
    assert scored > 0.0


def test_a_candidate_holds_whatever_it_was_given() -> None:
    """`Candidate` is a proposal object and does not normalise on its own.

    Pinned so the choke point stays where the docstrings say it is — in
    `entities.normalise`, called by both halves — rather than migrating into the
    dataclass where only one of the two paths would hit it.
    """
    candidate = Candidate(type="event", content="x", entities=["companion:Ruby"], signals=Signals())
    assert candidate.entities == ["companion:Ruby"]
