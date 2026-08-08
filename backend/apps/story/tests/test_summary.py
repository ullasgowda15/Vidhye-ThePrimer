"""The running summary's bound (PRODUCT.md §8.6).

`tests/test_choices.py` measures the effect end to end — the prompt does not grow
with the story. This measures the mechanism, where the arithmetic is visible and a
regression is unambiguous: whatever you feed it, the summary fits in
`SUMMARY_MAX_CHARS`, and what it keeps when it has to drop something is the
opening plus the beats nearest the live window.
"""

import datetime
from typing import Any

import pytest

from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator, summary
from apps.story.models import Adventure, Beat, Choice

pytestmark = pytest.mark.django_db


@pytest.fixture
def adventure(db: Any) -> Adventure:
    from django.contrib.auth import get_user_model

    parent = get_user_model().objects.create_user(
        email="summary-parent@example.com", password="storybook-primer-42"
    )
    child = Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs"],
    )
    create_companion(
        child=child,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    return Adventure.objects.create(child=child, plan={"skeleton": ["a"] * 5})


def add_beat(adventure: Adventure, seq: int, narration: str, *, chose: str = "") -> Beat:
    """One beat, with an answer against it when the child picked something."""
    options = [{"key": "left", "label": chose, "icon_prompt": "a path"}] if chose else []
    beat = Beat.objects.create(
        adventure=adventure,
        seq=seq,
        narration=narration,
        interaction={"type": "choose" if chose else "none", "choices": options or None},
    )
    if chose:
        Choice.objects.create(adventure=adventure, beat=beat, chosen_key="left", options=options)
    return beat


def test_a_short_story_is_kept_whole(adventure) -> None:
    add_beat(adventure, 1, "Pip arrives at the river.")
    add_beat(adventure, 2, "The chorus is missing a singer.", chose="Follow the river")

    text = summary.compact_summary(adventure)

    assert "1. Pip arrives at the river." in text
    assert 'Aarav chose "Follow the river".' in text
    # Nothing is folded while everything still fits in the recent window.
    assert summary.HEAD_PREFIX not in text


def test_the_summary_stays_bounded_however_long_the_story_runs(adventure) -> None:
    """The property the whole module exists for.

    Forty beats is far more than an adventure will ever have, which is the point:
    the bound is arithmetic rather than a consequence of stories being short.
    """
    for seq in range(1, 41):
        add_beat(
            adventure,
            seq,
            f"Beat {seq}: " + "the reeds whisper and the water is very bright indeed. " * 3,
            chose=f"Option {seq}",
        )

    text = summary.compact_summary(adventure)

    assert len(text) <= summary.SUMMARY_MAX_CHARS
    # It is still a summary of *this* story, not an empty string that passes the
    # bound for the wrong reason.
    assert "Beat 1:" in text
    assert "Beat 40:" in text


def test_growth_is_flat_rather_than_linear(adventure) -> None:
    sizes = []
    for seq in range(1, 21):
        add_beat(adventure, seq, f"Beat {seq}: something happens by the water.", chose="Onward")
        sizes.append(len(summary.compact_summary(adventure)))

    # It grows while the fold's budget is filling, and then it *stops* — each
    # further beat adds a line and drops one, so the length stays put to within the
    # width of a sequence number. Linear growth, every beat pasted into every
    # prompt, is what this rules out, and "flat to a few characters" is a far
    # sharper statement of it than any ratio.
    assert max(sizes[-5:]) - min(sizes[-5:]) <= 4, sizes
    assert sizes[-1] <= summary.SUMMARY_MAX_CHARS
    # And it did grow at first, so the flatness above is saturation rather than a
    # summary that never recorded anything.
    assert sizes[0] < sizes[-1]


def test_the_fold_keeps_the_opening_and_the_beats_nearest_the_window(adventure) -> None:
    """What compaction throws away, stated as a test.

    The opening says where the child is and who is with them; the beats just before
    the verbatim window are the ones the next beat has to follow on from. The middle
    is what goes — see the module docstring on why that loss belongs to Day 8.
    """
    for seq in range(1, 16):
        add_beat(adventure, seq, f"Beat {seq}: " + "a long stretch of river and reeds. " * 4)

    head = summary.compact_summary(adventure).splitlines()[0]

    assert head.startswith(summary.HEAD_PREFIX)
    assert "Beat 1:" in head
    # The newest of the folded beats survives; the middle is what is dropped.
    assert "Beat 12:" in head
    assert "Beat 3:" not in head


def test_the_recent_window_is_verbatim(adventure) -> None:
    for seq in range(1, 11):
        add_beat(adventure, seq, f"Beat {seq}: the frogs sing.")

    lines = summary.compact_summary(adventure).splitlines()

    assert len(lines) == summary.SUMMARY_RECENT_BEATS + 1
    assert lines[-1] == "10. Beat 10: the frogs sing."


def test_only_the_first_sentence_of_a_beat_is_kept(adventure) -> None:
    add_beat(adventure, 1, "Pip waves hello. Then a great deal else happens that nobody needs.")

    text = summary.compact_summary(adventure)

    assert "Pip waves hello." in text
    assert "nobody needs" not in text


def test_scaffolding_is_carried_so_the_next_beat_can_be_kind(adventure) -> None:
    beat = add_beat(adventure, 1, "Three frogs sit on the log.", chose="Count them")
    Choice.objects.filter(beat=beat).update(assisted=True)

    text = summary.compact_summary(adventure)

    # Recorded as help given, never as a failure (§8.5).
    assert "needed a hand, and got one" in text
    assert "wrong" not in text.lower()


def test_refresh_persists_only_when_something_changed(adventure) -> None:
    add_beat(adventure, 1, "Pip arrives.")
    summary.refresh_running_summary(adventure)
    adventure.refresh_from_db()
    stamped = adventure.updated_at

    summary.refresh_running_summary(adventure)

    adventure.refresh_from_db()
    assert adventure.updated_at == stamped
    assert adventure.running_summary.startswith("1. Pip arrives.")


def test_an_empty_adventure_summarises_to_nothing(adventure) -> None:
    assert summary.compact_summary(adventure) == ""


def test_the_summary_is_what_the_next_beat_s_prompt_carries(adventure, fake_ai) -> None:
    """The seam between this module and the prompt: what is stored is what is sent."""
    add_beat(adventure, 1, "Pip arrives at the river.", chose="Follow the river")
    summary.refresh_running_summary(adventure)

    context = orchestrator.build_beat_context(adventure, seq=2)

    assert "Story so far: 1. Pip arrives at the river." in context["system"]
    assert 'Aarav chose "Follow the river".' in context["system"]
