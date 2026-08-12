"""The interest graph (PRODUCT.md §11.7, §2.4) — Day 11 criterion 1.

The claim under test is the one §11.7 makes and nothing weaker: the graph is
*updated from behaviour*, it *decays*, and what it says steers what happens next.
So the sitting these tests read is a real one — played through the orchestrator by
the `played` fixture, with real beats, real choices and a real STM mirror — rather
than a hand-built set of rows that would let the reader and the writer of the graph
drift apart without anything failing.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone

from apps.learning import engine
from apps.memory import interest
from apps.memory.models import InterestEdge
from apps.memory.tasks import update_interests
from apps.story import orchestrator
from apps.story.models import PlaySession

pytestmark = pytest.mark.django_db


def age(child: Any, topic: str, *, days: float) -> InterestEdge:
    """Backdate one edge, as though it had not been touched for `days`.

    `.update()` rather than `.save()`, deliberately: `last_reinforced_at` is
    `auto_now`, so saving would stamp it to now and quietly undo the very thing
    being set up.
    """
    when = timezone.now() - datetime.timedelta(days=days)
    InterestEdge.objects.filter(child=child, topic=topic).update(last_reinforced_at=when)
    return InterestEdge.objects.get(child=child, topic=topic)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------
def test_a_topic_is_one_word_however_it_was_written() -> None:
    """`frogs`, `Frog` and `the frogs` are one interest, not three.

    A fork here is the §11.4 duplicate-entity problem one table over, and what it
    quietly demotes is always the favourite: the child's most-loved thing is the one
    mentioned in the most different ways.
    """
    assert "frog" in interest.topic_tokens("Frogs")
    assert "frog" in interest.topic_tokens("the frogs")
    assert interest.topic_tokens("a") == frozenset(), "a two-letter word names nothing"
    assert "the" not in interest.topic_tokens("the river"), "function words are not interests"
    # The "ss" exception, which the crude singulariser needs and which is easy to
    # lose: without it "moss" is stored as "mos", matching no world's vocabulary,
    # no parent's seed word, and nothing a person would recognise in a list of
    # what their child loves.
    assert "moss" in interest.topic_tokens("moss and grass")
    assert "grass" in interest.topic_tokens("moss and grass")
    # And the same standard applied to the plurals a five-year-old actually uses.
    # "dragonflie" and "bunnie" are not words, match no world's vocabulary, and are
    # not something to show a parent as what their child loves (§11.7).
    assert "dragonfly" in interest.topic_tokens("the dragonflies")
    assert "butterfly" in interest.topic_tokens("butterflies")
    assert "bunny" in interest.topic_tokens("bunnies and puppies")
    assert "puppy" in interest.topic_tokens("bunnies and puppies")
    assert "fox" in interest.topic_tokens("foxes and beaches")
    assert "beach" in interest.topic_tokens("foxes and beaches")
    assert "horse" in interest.topic_tokens("horses"), "not 'hors'"


def test_a_topic_names_a_subject_rather_than_describing_one() -> None:
    """The words on a choice card are ours; only the nouns on it are theirs.

    Without this the graph fills with the verbs and adjectives of our own card copy
    — a real sitting produced `river, frog, sparkling, follow, dragonflie, ask` —
    and those go verbatim into the plan prompt's "This child is currently in love
    with: …" and into what §11.7 promises a parent as a true window.
    """
    topics = interest.topic_tokens("Follow the sparkling river")

    assert topics == {"river"}
    assert interest.topic_tokens("Ask the dragonflies") == {"dragonfly"}


def test_entity_keys_become_topics_only_when_they_name_a_subject() -> None:
    """§11.7 is a graph of what the child loves, not of who was on screen."""
    topics = interest.topics_from_entities(
        ["character:moss_frog", "world:whispering_woods", "companion:pip", "objective:count_to_5"]
    )

    assert {"moss", "frog"} <= topics
    assert "whispering" in topics
    # The companion is in every sitting and would win every ranking while saying
    # nothing; the objective is our machinery and the child has no opinion about it.
    assert "pip" not in topics
    assert "count" not in topics


# ---------------------------------------------------------------------------
# Criterion 1: weights update from behaviour, and unrelated topics decay
# ---------------------------------------------------------------------------
def test_a_played_sitting_raises_what_the_child_chose(played: PlaySession) -> None:
    """The child followed the sparkling river twice; the graph should say so.

    `played` taps `follow_river` and `ask_dragonflies` — so the words on the cards
    they took are what the graph learns, and the words on the cards they left are
    not. That is §11.7's "updated from choices" read literally: an option we offered
    and they declined is our content, not their interest.
    """
    outcome = interest.update_from_session(played)

    assert outcome.status == "updated"
    topics = {topic.name: topic.weight for topic in interest.top_interests(played.child, limit=20)}

    assert "river" in topics, "they chose the river twice"
    assert "dragonfly" in topics, "and the dragonflies once"
    assert "chorus" not in topics, "the card they never took is not an interest"
    # And what came off the cards is the *nouns* on them, not our verbs (§11.7).
    assert {"follow", "ask", "sparkling"}.isdisjoint(topics)


def test_choosing_something_outweighs_merely_being_shown_it(played: PlaySession) -> None:
    """§11.7's grading, which is what stops the graph becoming a graph of our scenery.

    Every beat of this sitting is set on a mossy riverbank, so `moss` is in the
    continuity of the whole story — and the child chose the river. Both are in the
    graph; they must not be worth the same, or the thing that decides a child's next
    world is whatever our own narration happened to repeat.
    """
    interest.update_from_session(played)
    weights = {topic.name: topic.weight for topic in interest.top_interests(played.child, limit=20)}

    assert weights["river"] > weights.get("mossy", weights.get("moss", 0.0))


def test_an_untouched_interest_fades_and_a_chosen_one_does_not(played: PlaySession) -> None:
    """Criterion 1's second half: unrelated topics decay.

    Two months is two half-lives, so a topic nobody has touched should be worth a
    quarter of what it was — enough to fall behind a topic the child chose *this*
    afternoon, which is the whole point of the weights being time-aware.
    """
    interest.update_from_session(played)
    InterestEdge.objects.create(child=played.child, topic="trains", weight=0.9)
    stale = age(played.child, "trains", days=60)

    assert interest.current_weight(stale) == pytest.approx(0.9 * 0.25, abs=0.02)

    ranked = [topic.name for topic in interest.top_interests(played.child, limit=20)]
    assert ranked.index("river") < ranked.index("trains")


def test_a_faded_interest_is_eventually_forgotten(played: PlaySession, aarav: Any) -> None:
    """A weight below `FORGET_BELOW` is a row, not an interest (§11.7).

    Deleted at the *next* update rather than by a sweep, so the graph is only ever
    written by something the child did — and so a table read on the story-start path
    cannot grow forever.
    """
    InterestEdge.objects.create(child=aarav, topic="tractors", weight=0.6)
    age(aarav, "tractors", days=365)

    outcome = interest.update_from_session(played)

    assert outcome.forgotten == 1
    assert not InterestEdge.objects.filter(child=aarav, topic="tractors").exists()


def test_reinforcement_saturates_rather_than_growing_without_bound(aarav: Any) -> None:
    """The tenth frog choice moves the number less than the first one does.

    Without this a child who has chosen one thing forty times would have a graph
    with a single entry in it that nothing could ever outrank, which is a companion
    that has stopped noticing them.
    """
    reinforced, _ = interest.apply_reinforcement(aarav, {"frog": interest.CHOSE})
    first_step = InterestEdge.objects.get(child=aarav, topic="frog").weight

    for _ in range(8):
        interest.apply_reinforcement(aarav, {"frog": interest.CHOSE})
    before_last = InterestEdge.objects.get(child=aarav, topic="frog").weight
    interest.apply_reinforcement(aarav, {"frog": interest.CHOSE})
    after_last = InterestEdge.objects.get(child=aarav, topic="frog").weight

    assert reinforced == 1
    assert after_last <= interest.MAX_WEIGHT
    # Still rising, and rising far less than it did the first time.
    assert 0 < after_last - before_last < first_step / 10


# ---------------------------------------------------------------------------
# Seeds are the cold start (§17.2)
# ---------------------------------------------------------------------------
def test_a_child_who_has_never_played_still_has_interests(aarav: Any) -> None:
    """§2.4's bet has to pay on the *first* adventure, before any graph exists."""
    assert not InterestEdge.objects.filter(child=aarav).exists()

    names = [topic.name for topic in interest.top_interests(aarav)]

    assert "frog" in names and "river" in names


def test_play_outranks_a_seed_nobody_has_acted_on(aarav: Any) -> None:
    """The parent said frogs and rivers; the child keeps choosing the sea.

    Both stay in the ranking — a parent's answer is evidence too — but the one the
    child's own behaviour confirms has to come first, or the graph is a slower way
    of reading the onboarding form.
    """
    for _ in range(3):
        interest.apply_reinforcement(aarav, {"sea": interest.CHOSE})

    ranked = [topic.name for topic in interest.top_interests(aarav)]

    assert ranked[0] == "sea"
    assert "frog" in ranked


# ---------------------------------------------------------------------------
# The Day-10 seam, now wired (§10.3)
# ---------------------------------------------------------------------------
def test_the_learning_selector_reads_the_graph_rather_than_the_seed_field(aarav: Any) -> None:
    """`engine.interest_fit`'s vocabulary is the live graph from Day 11.

    The term is still a tie-break and still weak by design (`INTEREST_WEIGHT`); what
    changed is that it can now say something that was not true at onboarding.
    """
    interest.apply_reinforcement(aarav, {"lantern": interest.SPOKE})

    tokens = engine._interest_tokens(aarav)

    assert "lantern" in tokens, "something only play knows about"
    assert "frog" in tokens, "and the seeds are still in there"


# ---------------------------------------------------------------------------
# The task (§16.3)
# ---------------------------------------------------------------------------
def test_the_close_enqueues_the_interest_update(
    played: PlaySession, monkeypatch: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """Three siblings now, and a failure to queue one must not cost the others."""
    sent: list[str] = []
    monkeypatch.setattr(orchestrator.consolidate, "delay", lambda session_id: None)
    monkeypatch.setattr(orchestrator.update_from_session, "delay", lambda session_id: None)
    monkeypatch.setattr(orchestrator.update_interests, "delay", lambda sid: sent.append(sid))

    adventure = played.adventures.first()
    assert adventure is not None
    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert sent == [str(played.pk)]
    assert not InterestEdge.objects.filter(
        child=played.child
    ).exists(), "the close hands the work over; it does not do it on the child's request"


def test_the_task_is_idempotent(played: PlaySession) -> None:
    """Celery is at-least-once, and a doubled weight is a favourite we invented."""
    assert update_interests(str(played.pk)) == "updated"
    once = {row.topic: row.weight for row in InterestEdge.objects.filter(child=played.child)}

    assert update_interests(str(played.pk)) == "already"
    twice = {row.topic: row.weight for row in InterestEdge.objects.filter(child=played.child)}

    assert once == twice


def test_an_empty_sitting_is_a_finished_update(aarav: Any) -> None:
    """ "We looked and there was nothing" is done, not a permanent retry."""
    session = PlaySession.objects.create(child=aarav)

    assert update_interests(str(session.pk)) == "empty"

    session.refresh_from_db()
    assert session.interests_updated_at is not None


def test_a_deleted_family_is_not_an_error(aarav: Any) -> None:
    """§13.8: the deletion is the newer instruction."""
    assert update_interests("00000000-0000-0000-0000-000000000000") == "missing"
