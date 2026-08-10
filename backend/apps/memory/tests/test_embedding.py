"""The embedding adapter and the ANN index (PRODUCT.md §11.3, §11.9, §17.3).

Two halves.

The first is the adapter contract, which is the same contract the image and voice
adapters have: one provider name in configuration, one real implementation, typed
stubs that raise rather than silently not existing, and a width that is checked
rather than assumed.

The second is the part that matters for Day 9: **a nearest-neighbour query over the
pgvector column returns sensible neighbours.** That runs against real Postgres with
the real HNSW index, using the deterministic local provider — so what is being
proved is our column, our index and our ordering, rather than a vendor's weights.
"""

from __future__ import annotations

import math

import pytest
from pgvector.django import CosineDistance

from apps.media.adapters import embedding as adapter
from apps.media.exceptions import MediaConfigurationError, MediaProviderError
from apps.memory.models import EMBEDDING_DIMENSIONS, Memory, MemoryType


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------
def test_the_local_provider_returns_the_column_width() -> None:
    vector = adapter.embed("Aarav named the little dragon Sparkle.")

    assert len(vector) == EMBEDDING_DIMENSIONS
    assert len(vector) == adapter.LocalHashEmbeddingProvider.dimensions


def test_the_local_provider_is_deterministic_across_processes() -> None:
    """sha256 rather than `hash()`: yesterday's vectors must still match today's."""
    first = adapter.embed("the frog chorus by the river")
    second = adapter.embed("the frog chorus by the river")

    assert first == second
    # Unit-normalised, which is what makes cosine distance meaningful.
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)


def test_text_with_no_content_words_is_far_from_everything_rather_than_nowhere() -> None:
    """An all-zero vector has no cosine distance and would sort arbitrarily."""
    vector = adapter.embed("!!! ???")

    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)


def test_an_empty_string_is_refused_rather_than_embedded() -> None:
    with pytest.raises(MediaProviderError, match="empty"):
        adapter.embed("   ")


def test_an_unknown_provider_name_is_an_error_not_a_downgrade(settings) -> None:
    settings.EMBEDDING_PROVIDER = "nonesuch"

    with pytest.raises(MediaConfigurationError, match="Unknown embedding provider"):
        adapter.embed("anything")


def test_a_real_provider_without_a_key_says_so_usefully(settings) -> None:
    settings.EMBEDDING_PROVIDER = "openai"
    settings.OPENAI_API_KEY = ""

    with pytest.raises(MediaConfigurationError, match="OPENAI_API_KEY"):
        adapter.embed("anything")


def test_declared_but_unbuilt_providers_raise_rather_than_pretend(settings) -> None:
    """§19.3's fallback seam: a stub must be honest about being a stub."""
    for name in ("voyage", "google"):
        provider = adapter.EMBEDDING_PROVIDERS[name]()
        assert provider.is_configured() is False
        with pytest.raises(NotImplementedError):
            provider.embed("anything")


def test_a_provider_of_the_wrong_width_is_rejected_not_reshaped() -> None:
    """A silently padded vector is a vector that ranks wrongly forever."""

    class NarrowProvider:
        name = "narrow"
        dimensions = 8

        def is_configured(self) -> bool:
            return True

        def embed(self, text: str) -> list[float]:
            return [0.1] * 8

    with pytest.raises(MediaProviderError, match="8 dimensions"):
        adapter.embed("anything", NarrowProvider())


def test_the_provider_label_names_the_offline_one_as_offline() -> None:
    """A database of lexical vectors must never look like a semantic one."""
    assert adapter.provider_label(adapter.LocalHashEmbeddingProvider()) == "local-hash"
    assert adapter.provider_label(adapter.OpenAIEmbeddingProvider()) == "openai"


# ---------------------------------------------------------------------------
# The ANN index (§17.3, and the substrate of Day 9's retrieval)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_ann_index_exists_on_the_embedding_column() -> None:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'memory_memory' AND indexname = 'memory_embedding_hnsw'"
        )
        row = cursor.fetchone()

    assert row is not None, "the pgvector ANN index is missing"
    assert "hnsw" in row[0]
    # The opclass has to match the distance function the query uses, or the index
    # is built and silently never used.
    assert "vector_cosine_ops" in row[0]


@pytest.mark.django_db
def test_a_nearest_neighbour_query_returns_sensible_neighbours(aarav) -> None:
    """The Day-9 query, on Day 8's index.

    Three memories about quite different things, and a query about one of them. The
    right one has to come first — and the ordering has to come out of Postgres, not
    out of Python.
    """
    remembered = {
        "dragon": "Aarav named the little dragon Sparkle and gave it a hat.",
        "counting": "Aarav counted three frogs on the mossy log all by himself.",
        "fear": "Aarav was worried about the loud thunder over the meadow.",
    }
    for key, content in remembered.items():
        Memory.objects.create(
            child=aarav,
            type=MemoryType.EVENT,
            content=content,
            embedding=adapter.embed(content),
            salience=0.8,
            entities=[f"topic:{key}"],
        )

    def nearest(query: str) -> list[str]:
        vector = adapter.embed(query)
        return list(
            Memory.objects.filter(child=aarav)
            .annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance")
            .values_list("entities", flat=True)
        )

    assert nearest("tell me about the little dragon Sparkle")[0] == ["topic:dragon"]
    assert nearest("how did the counting of frogs go")[0] == ["topic:counting"]
    assert nearest("was there anything about loud thunder")[0] == ["topic:fear"]


@pytest.mark.django_db
def test_a_memory_with_no_vector_is_still_a_memory(aarav) -> None:
    """The nullable column is a deliberate asymmetry, not an oversight.

    An embedding provider having a bad day costs the vector; the memory is still
    written and is still reachable by the rules half of retrieval (§11.9).
    """
    Memory.objects.create(
        child=aarav, type=MemoryType.FEAR, content="Aarav is wary of thunder.", salience=0.9
    )

    by_rules = Memory.objects.filter(child=aarav, type=MemoryType.FEAR).order_by("-salience")
    assert by_rules.count() == 1
    found = by_rules.first()
    assert found is not None
    assert found.embedding is None
