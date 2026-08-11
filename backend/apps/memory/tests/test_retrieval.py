"""Hybrid retrieval (PRODUCT.md §11.9, §19.4) — Day 9's acceptance criteria 1, 4, 6.

These run against a real Postgres with pgvector and the `local` embedding provider
(`conftest.offline_media`), so the vector column, the HNSW index and the ordering of
a cosine query are genuinely exercised rather than mocked. The local provider is
*lexical* — see `media.adapters.embedding.LocalHashEmbeddingProvider` — which is
exactly what makes it useful here: a query that shares words with a memory really
does come back nearer, so "the right memory ranked first" is a checkable claim
without a key and without spend.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.companion.service import create_companion
from apps.media.adapters import embedding as embedding_adapter
from apps.media.exceptions import MediaProviderError
from apps.memory import retrieval
from apps.memory.models import Memory, MemoryType
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db

PASSWORD = "storybook-primer-42"

#: Distinct subjects for tests that need *many different* memories rather than one
#: moment told many times. `_dedupe` folds near-identical sentences together — which
#: is correct, and which would quietly make a budget test pass for the wrong reason.
CREATURES = (
    "heron",
    "dragonfly",
    "otter",
    "kingfisher",
    "water vole",
    "moorhen",
    "stickleback",
    "damselfly",
    "grass snake",
    "newt",
    "toad",
    "swan",
    "reed warbler",
    "beetle",
    "minnow",
    "crayfish",
    "wagtail",
    "mayfly",
    "pond skater",
    "sand martin",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _child(email: str, name: str, **overrides: Any) -> Child:
    parent = get_user_model().objects.create_user(email=email, password=PASSWORD)
    fields: dict[str, Any] = {
        "first_name": name,
        "birthdate": datetime.date(datetime.date.today().year - 5, 5, 4),
    }
    fields.update(overrides)
    return Child.objects.create(parent=parent, **fields)


def remember(child: Child, content: str, **overrides: Any) -> Memory:
    """Write one memory the way consolidation does — embedded, scored, typed."""
    fields: dict[str, Any] = {
        "type": MemoryType.EVENT,
        "salience": 0.7,
        "valence": 0.6,
        "embedding": embedding_adapter.embed(content),
    }
    fields.update(overrides)
    return Memory.objects.create(child=child, content=content, **fields)


@pytest.fixture
def rina() -> Child:
    """A returning child: a companion, seeds, and a history worth recalling."""
    child = _child(
        "retrieval-rina@example.com",
        "Rina",
        interests=["dragons", "the sea"],
        fears=["the dark"],
    )
    create_companion(
        child=child,
        archetype="dragon",
        name="Sparkle",
        palette="ember",
        personality_preset="brave_spark",
    )
    return child


# ---------------------------------------------------------------------------
# Criterion 1 — small, ranked, token-bounded; rule-includes always present
# ---------------------------------------------------------------------------
class TestTheBundleIsSmallRankedAndBounded:
    def test_the_rule_includes_are_in_every_bundle(self, rina: Child) -> None:
        """§11.9's "always": the companion and the active fears, unprompted.

        Asserted on a bundle retrieved for a context that has nothing to do with
        either — that is the whole point of a rule-include. A fear that only
        surfaced when the story was already about the dark would arrive exactly
        when it is least useful.
        """
        remember(rina, "Rina went quiet at the dark bridge.", type=MemoryType.FEAR, salience=0.8)

        bundle = retrieval.retrieve(rina, "counting frogs in the sunshine")
        rendered = " ".join(item.content for item in bundle)

        assert any(item.type == retrieval.COMPANION_STATE for item in bundle)
        assert "Sparkle" in rendered
        # Both halves of "active fears": the one the parent typed, and the one the
        # sessions revealed.
        assert "the dark" in rendered
        assert "dark bridge" in rendered
        assert all(item.pinned for item in bundle if item.type == MemoryType.FEAR)

    def test_it_returns_few_memories_not_all_of_them(self, rina: Child) -> None:
        """The claim §19.4 actually makes: *few*, high-value, token-bounded."""
        for index in range(40):
            remember(rina, f"Rina met a small creature number {index} by the river.")

        bundle = retrieval.retrieve(rina, "a river with creatures in it")

        assert bundle.considered > len(bundle), "nothing was filtered"
        assert bundle.tokens <= retrieval.token_budget()
        # A dozen-ish items, not forty. The exact number is a tuning question; that
        # it does not grow with the history is the property.
        assert len(bundle) < 20

    def test_the_prompt_stays_flat_as_the_history_grows(self, rina: Child) -> None:
        """§8.6's flat prompt, applied to memory.

        The failure this guards against is invisible and slow: every session adds
        rows, every row adds tokens, and eighteen months later beats cost four times
        what they cost at launch. Same budget, ten memories or four hundred.
        """
        for index in range(10):
            remember(rina, f"Rina found shell number {index} on the beach.")
        small = retrieval.retrieve(rina, "shells on the beach")

        for index in range(10, 200):
            remember(rina, f"Rina found shell number {index} on the beach.")
        large = retrieval.retrieve(rina, "shells on the beach")

        assert large.tokens <= retrieval.token_budget()
        assert large.tokens <= small.tokens * 1.5

    def test_the_budget_cuts_the_tail_and_keeps_the_rules(self, rina: Child) -> None:
        """A budget too small to hold everything drops the *ranked* half first.

        Which is what makes `rank`'s pinning meaningful rather than decorative: the
        rule-includes are offered the budget first, so they are the last things it
        can refuse.
        """
        for index in range(10):
            remember(rina, f"Rina counted {index} stepping stones across the stream.")

        bundle = retrieval.retrieve(rina, "stepping stones across a stream", budget=90)

        assert bundle.tokens <= 90
        assert bundle.items, "the budget emptied the bundle"
        assert bundle.items[0].type == retrieval.COMPANION_STATE
        assert all(item.pinned for item in bundle), "a ranked item outranked a rule"

    def test_the_vector_half_finds_what_no_rule_would_have(self, rina: Child) -> None:
        """§11.9's "semantically relevant", isolated from the rules.

        Every memory here scores below `RECENT_SALIENCE_FLOOR`, so none of them is a
        rule-include: nothing but similarity can put any of them in the bundle. With
        twenty unremarkable riverbank afternoons in the table and a budget that fits
        two of them, the one that survives is the one today is about — which is the
        vector half proving itself rather than riding along on a rule.
        """
        for creature in CREATURES:
            remember(rina, f"Rina met a {creature} by the river and said hello.", salience=0.3)
        remember(rina, "Rina counted three stepping stones across the stream.", salience=0.3)

        bundle = retrieval.retrieve(rina, "stepping stones across the stream", budget=200)
        recalled = [item.content for item in bundle.remembered]

        assert recalled, "the vector half returned nothing"
        assert "stepping stones" in recalled[0]

    def test_relevance_orders_the_bundle_even_among_the_rule_includes(self, rina: Child) -> None:
        """A memory both halves found keeps the similarity the vector half measured.

        Dropping the vector copy as a duplicate — the obvious reading of "dedupe" —
        left every recent memory ranked by recency alone, so the most relevant thing
        the child did last week landed in the middle of the bundle. See
        `retrieval._dedupe`.
        """
        remember(rina, "Rina named the little dragon Sparkle.", salience=0.8)
        remember(rina, "Rina counted three stepping stones across the stream.", salience=0.8)

        recalled = [
            item
            for item in retrieval.retrieve(rina, "stepping stones across the stream")
            if item.stored
        ]

        assert "stepping stones" in recalled[0].content
        assert recalled[0].similarity > 0.0, "the vector copy's similarity was thrown away"
        assert recalled[0].pinned, "and its rule-include status survived the merge"

    def test_one_moment_is_not_recalled_twice(self, rina: Child) -> None:
        """A memory that is both a rule-include and a near neighbour appears once.

        The same near-duplicate question `salience.select` asks at write time, asked
        again at read time — because a bundle is drawn from the whole history rather
        than from one sitting, and two rows written from one beat can be one moment.
        """
        remember(rina, "Rina went quiet at the dark bridge.", type=MemoryType.FEAR, salience=0.9)

        bundle = retrieval.retrieve(rina, "Rina went quiet at the dark bridge")
        bridges = [item for item in bundle if "dark bridge" in item.content]

        assert len(bridges) == 1

    def test_the_bundle_renders_as_labeled_prompt_items(self, rina: Child) -> None:
        """`as_prompt` is what `prompting.assemble(memory=…)` reads — and no ids."""
        memory = remember(rina, "Rina chose the sparkling river.")

        items = retrieval.retrieve(rina, "the sparkling river").as_prompt()

        assert all(set(item) == {"type", "content"} for item in items)
        assert str(memory.pk) not in str(items)


# ---------------------------------------------------------------------------
# Criterion 6 — no cross-child leakage
# ---------------------------------------------------------------------------
class TestOneChildsMemoriesStayTheirs:
    def test_retrieval_never_returns_another_childs_memory(self) -> None:
        """§17.5 and §11.10, proved with two children and one shared topic.

        Both children have a dragon memory, so a query about dragons matches
        something for each of them. A filter that had been dropped would show up
        here as the *other* child's sentence in this child's bundle — which is the
        single worst bug this product could ship, and the reason it is asserted from
        both directions rather than once.
        """
        rina = _child("leak-rina@example.com", "Rina", interests=["dragons"])
        omar = _child("leak-omar@example.com", "Omar", interests=["dragons"])
        create_companion(
            child=rina,
            archetype="dragon",
            name="Sparkle",
            palette="ember",
            personality_preset="brave_spark",
        )
        create_companion(
            child=omar,
            archetype="fox",
            name="Rusty",
            palette="moss",
            personality_preset="calm_anchor",
        )
        remember(rina, "Rina named the little dragon Sparkle.")
        remember(omar, "Omar named the big dragon Thunderclap.")

        hers = retrieval.retrieve(rina, "a dragon with a name")
        his = retrieval.retrieve(omar, "a dragon with a name")

        assert hers.child_id == rina.pk and his.child_id == omar.pk
        assert "Sparkle" in " ".join(item.content for item in hers)
        assert "Thunderclap" not in " ".join(item.content for item in hers)
        assert "Rusty" not in " ".join(item.content for item in hers)
        assert "Thunderclap" in " ".join(item.content for item in his)
        assert "Sparkle" not in " ".join(item.content for item in his)

    def test_recall_bookkeeping_cannot_touch_another_childs_rows(self) -> None:
        """The write side of the same rule.

        A bundle is a value, so a caller could hand `mark_recalled` one built from
        somebody else's ids. It is scoped to the bundle's own child, so the answer
        is zero rows rather than a quiet cross-family update.
        """
        rina = _child("bookkeep-rina@example.com", "Rina")
        omar = _child("bookkeep-omar@example.com", "Omar")
        theirs = remember(omar, "Omar built a tower of seven stones.")

        forged = retrieval.MemoryBundle(
            child_id=rina.pk,
            items=(retrieval.Recalled(type="event", content="x", memory_id=theirs.pk),),
        )

        assert retrieval.mark_recalled(forged) == 0
        theirs.refresh_from_db()
        assert theirs.recall_count == 0


# ---------------------------------------------------------------------------
# Criterion 4 — recall bookkeeping
# ---------------------------------------------------------------------------
class TestRecallIsRecorded:
    def test_using_a_memory_stamps_it_and_counts_it(self, rina: Child) -> None:
        used = remember(rina, "Rina counted three stepping stones across the stream.")
        unused = remember(rina, "Rina waved at a heron.", salience=0.1)

        bundle = retrieval.retrieve(rina, "stepping stones across the stream", budget=200)
        assert used.pk in bundle.memory_ids

        assert retrieval.mark_recalled(bundle) == len(bundle.memory_ids)
        used.refresh_from_db()
        unused.refresh_from_db()

        assert used.recall_count == 1
        assert used.last_recalled_at is not None
        # And nothing was stamped merely for existing.
        if unused.pk not in bundle.memory_ids:
            assert unused.recall_count == 0
            assert unused.last_recalled_at is None

    def test_recall_counts_accumulate_across_visits(self, rina: Child) -> None:
        memory = remember(rina, "Rina named the little dragon Sparkle.")

        for _ in range(3):
            retrieval.mark_recalled(retrieval.retrieve(rina, "the little dragon Sparkle"))

        memory.refresh_from_db()
        assert memory.recall_count == 3

    def test_a_memory_recalled_recently_ranks_above_an_equally_old_one(self, rina: Child) -> None:
        """Why `last_recalled_at` is written rather than merely counted.

        A memory the companion mentioned last week is live in the child's mind
        whatever its birthday says. Both rows here are a year old and equally
        salient; only one has been spoken about.
        """
        long_ago = timezone.now() - datetime.timedelta(days=365)
        stale = remember(rina, "Rina waved at the heron on the far bank.", salience=0.6)
        live = remember(rina, "Rina waved at the swan on the far bank.", salience=0.6)
        Memory.objects.filter(pk__in=[stale.pk, live.pk]).update(created_at=long_ago)
        Memory.objects.filter(pk=live.pk).update(last_recalled_at=timezone.now())

        scored = {item.memory_id: item.recency for item in retrieval.retrieve(rina, "the far bank")}

        assert scored[live.pk] > scored[stale.pk]


# ---------------------------------------------------------------------------
# Degradation — the vector half is optional, the rules are not
# ---------------------------------------------------------------------------
class TestRetrievalDegradesTowardTheRules:
    def test_an_embedding_outage_costs_relevance_and_nothing_else(
        self, rina: Child, monkeypatch: Any
    ) -> None:
        """Day 8 made `embedding` nullable for this; Day 9 has to honour it."""
        remember(rina, "Rina counted three stepping stones across the stream.")

        def refuse(*_: Any, **__: Any) -> list[float]:
            raise MediaProviderError("the embedding provider is having a bad day")

        monkeypatch.setattr(retrieval.embedding_adapter, "embed", refuse)
        bundle = retrieval.retrieve(rina, "stepping stones across the stream")

        assert bundle.degraded is True
        assert any(item.type == retrieval.COMPANION_STATE for item in bundle)
        assert any("the dark" in item.content for item in bundle)

    def test_a_brand_new_child_gets_their_seeds_and_no_provider_call(
        self, rina: Child, monkeypatch: Any
    ) -> None:
        """No memories means no embedding call at all — a call per beat, for nothing.

        And the bundle is still not empty: the onboarding seeds are what make beat
        one recognisably this child's on the day §4.4 has to prove the product works.
        """
        calls: list[str] = []
        monkeypatch.setattr(
            retrieval.embedding_adapter, "embed", lambda text, *a, **k: calls.append(text)
        )

        bundle = retrieval.retrieve(rina, "a story about dragons")

        assert calls == []
        assert bundle.degraded is False
        assert not bundle.remembered, "a child with no rows should have nothing to recall"
        assert any("loves dragons" in item.content for item in bundle)

    def test_a_child_with_no_companion_yet_still_gets_a_bundle(self) -> None:
        """Onboarding is not finished. Nothing is invented about a friend they have
        not chosen — the safety layer explicitly tells the model not to assert one.
        """
        child = _child("nocompanion@example.com", "Ada", interests=["trains"])

        bundle = retrieval.retrieve(child, "a train journey")

        assert not any(item.type == retrieval.COMPANION_STATE for item in bundle)
        assert any("loves trains" in item.content for item in bundle)


# ---------------------------------------------------------------------------
# The budget arithmetic
# ---------------------------------------------------------------------------
class TestTheTokenEstimate:
    def test_it_counts_the_framing_as_well_as_the_text(self) -> None:
        """A bundle of short memories is a third framing (`_memory_block`)."""
        assert retrieval.estimate_tokens("") == retrieval.ITEM_OVERHEAD_TOKENS
        assert retrieval.estimate_tokens("a" * 400) > 100

    def test_the_budget_is_read_from_settings_at_call_time(
        self, rina: Child, settings: Any
    ) -> None:
        """Distinct memories, deliberately — twenty tellings of one moment are one
        memory (`_dedupe`), and a bundle that stayed small because of *that* would
        say nothing about the budget.
        """
        for creature in CREATURES:
            remember(rina, f"Rina met a {creature} by the river and said hello.")

        settings.MEMORY_RETRIEVAL_TOKEN_BUDGET = 150
        assert retrieval.retrieve(rina, "the creatures by the river").tokens <= 150

        settings.MEMORY_RETRIEVAL_TOKEN_BUDGET = 800
        assert retrieval.retrieve(rina, "the creatures by the river").tokens > 150
