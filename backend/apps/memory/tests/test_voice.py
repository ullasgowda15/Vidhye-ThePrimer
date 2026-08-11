"""How a memory reads back (PRODUCT.md §11.3).

The Day 8 review's P3-1. `content` is not an internal field: §19.1 sends it back
into a prompt at recall, and Day 12 shows it to a parent in the transparency view.
A row that reads `Spidey chose "Count the spiders"` is a route log entry wearing a
memory's clothes.

The labels are imperative phrases by construction (§8.4), which is what makes a
small grammar sufficient — these tests pin the shapes that grammar has to get
right, including the two that break if you only lowercase and prepend "to".
"""

from __future__ import annotations

import pytest

from apps.memory.candidates import _choice_sentence

#: The beat's first clause, as `_anchor` would have produced it. Passed as a plain
#: string because the grammar under test has nothing to do with beats.
ANCHOR = "The frogs hop past."


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # The ordinary case: an imperative that wants an infinitive.
        ("Count the spiders", "Rina chose to count the spiders"),
        ("Follow more tracks", "Rina chose to follow more tracks"),
        ("Look under the slide", "Rina chose to look under the slide"),
        # A noun phrase is already a thing chosen — "chose to the blue ball" is broken.
        ("The blue ball", "Rina chose the blue ball"),
        ("A quiet path", "Rina chose a quiet path"),
        # A destination carries its own preposition and needs a verb, not "to".
        ("To the slide", "Rina chose to go to the slide"),
        ("Toward the flowers", "Rina chose to go toward the flowers"),
        # A name inside the label keeps its capital — it is what a parent scans for.
        ("Follow Ellie", "Rina chose to follow Ellie"),
    ],
)
def test_a_pick_reads_like_something_the_child_did(label: str, expected: str) -> None:
    sentence = _choice_sentence("Rina", label, ANCHOR)

    assert sentence.startswith(expected), sentence
    assert '"' not in sentence


def test_the_label_is_never_quoted_whatever_its_shape() -> None:
    for label in ["Count the spiders", "To the slide", "The blue ball", "Go!"]:
        assert '"' not in _choice_sentence("Rina", label, ANCHOR)


def test_trailing_punctuation_is_not_carried_into_the_sentence() -> None:
    assert _choice_sentence("Rina", "Go to the river!", ANCHOR).startswith(
        "Rina chose to go to the river —"
    )


def test_an_empty_label_falls_back_rather_than_producing_a_stub() -> None:
    """A pick we cannot name is still a moment; it just is not a described one."""
    assert _choice_sentence("Rina", "   ", ANCHOR).startswith("Rina carried on")


def test_the_anchor_is_still_appended() -> None:
    """The scenery stays in `content` — the fix was to the vector, not the record."""
    assert " — " in _choice_sentence("Rina", "Count the spiders", ANCHOR)
