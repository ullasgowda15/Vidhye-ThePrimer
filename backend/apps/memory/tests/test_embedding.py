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
    """§19.3's fallback seam: a stub must be honest about being a stub.

    `google` used to be in this list and is not any more — it is the shipped
    provider now (`gemini-embedding-001` on Vertex). `voyage` is still a seam.
    """
    provider = adapter.EMBEDDING_PROVIDERS["voyage"]()
    assert provider.is_configured() is False
    with pytest.raises(NotImplementedError):
        provider.embed("anything")


def test_the_google_provider_is_no_longer_a_stub(settings) -> None:
    """The other half of the test above, so "it is implemented" is asserted
    somewhere rather than merely no longer denied."""
    settings.GOOGLE_CLOUD_PROJECT = "primer-test"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/service-account.json"

    provider = adapter.EMBEDDING_PROVIDERS["google"]()

    assert isinstance(provider, adapter.GoogleEmbeddingProvider)
    assert provider.is_configured() is True
    assert not isinstance(provider, adapter._UnimplementedEmbeddingProvider)


def test_a_provider_of_the_wrong_width_is_rejected_not_reshaped() -> None:
    """A silently padded vector is a vector that ranks wrongly forever."""

    class NarrowProvider:
        name = "narrow"
        dimensions = 8

        def is_configured(self) -> bool:
            return True

        def embed(self, text: str, *, task: str = adapter.TASK_DOCUMENT) -> list[float]:
            return [0.1] * 8

    with pytest.raises(MediaProviderError, match="8 dimensions"):
        adapter.embed("anything", NarrowProvider())


def test_the_provider_label_names_the_offline_one_as_offline() -> None:
    """A database of lexical vectors must never look like a semantic one."""
    assert adapter.provider_label(adapter.LocalHashEmbeddingProvider()) == "local-hash"
    assert adapter.provider_label(adapter.OpenAIEmbeddingProvider()) == "openai"


# ---------------------------------------------------------------------------
# The shipped provider: gemini-embedding-001 on Vertex
# ---------------------------------------------------------------------------
# Three things can go wrong here and two of them are silent.
#
# The width is the loud one: `memory.embedding` is a `vector(1536)` column, so a
# provider returning anything else is refused by `embed` and, failing that, by
# Postgres. The quiet ones are **normalization** — truncated `gemini-embedding-001`
# output is not unit-length (measured: 0.690528 at 1536 against 1.000000 at its
# native 3072) — and **task type**, where embedding a query as though it were a
# document builds a correct index and then searches it slightly wrongly, forever,
# with nothing failing.


class _FakeEmbeddings:
    """Stands in for the Vertex client, recording what it was asked for."""

    def __init__(
        self,
        values: list[float] | None = None,
        boom: Exception | None = None,
        tokens: int | None = None,
    ) -> None:
        self._values = values
        self._boom = boom
        #: `ContentEmbeddingStatistics.token_count` — Vertex's own meter, and what
        #: the cost span is priced from. `None` stands for a response that did not
        #: report one, which is the estimated path.
        self._tokens = tokens
        self.calls: list[dict] = []
        self.models = self

    def embed_content(self, **kwargs) -> object:
        self.calls.append(kwargs)
        if self._boom is not None:
            raise self._boom
        statistics = (
            type("S", (), {"token_count": self._tokens})() if self._tokens is not None else None
        )
        embedding = type("E", (), {"values": self._values, "statistics": statistics})()
        return type("R", (), {"embeddings": [embedding]})()


@pytest.fixture
def vertex_embeddings(monkeypatch, settings):
    """Vertex configured, with the embedding call under the test's control."""
    settings.EMBEDDING_PROVIDER = "google"
    settings.EMBEDDING_MODEL = "gemini-embedding-001"
    settings.EMBEDDING_DIMENSIONS = EMBEDDING_DIMENSIONS
    settings.GOOGLE_CLOUD_PROJECT = "primer-test"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/service-account.json"

    def install(
        values: list[float] | None = None,
        boom: Exception | None = None,
        tokens: int | None = None,
    ):
        from apps.common import vertex

        client = _FakeEmbeddings(values, boom, tokens)
        monkeypatch.setattr(vertex, "client", lambda: client)
        return client

    return install


class TestTheVertexEmbeddingProvider:
    def test_it_asks_for_the_column_width_and_returns_it(self, vertex_embeddings) -> None:
        client = vertex_embeddings([0.5] * EMBEDDING_DIMENSIONS)

        vector = adapter.embed("Aarav named the little dragon Sparkle.")

        assert len(vector) == EMBEDDING_DIMENSIONS
        assert client.calls[0]["model"] == "gemini-embedding-001"
        assert client.calls[0]["config"].output_dimensionality == EMBEDDING_DIMENSIONS

    def test_it_normalizes_what_the_model_returns(self, vertex_embeddings) -> None:
        """The measured hazard. A raw 1536-wide `gemini-embedding-001` vector has
        an L2 norm near 0.69, and this is the line that fixes it."""
        vertex_embeddings([0.5] * EMBEDDING_DIMENSIONS)

        vector = adapter.embed("anything")

        assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)

    def test_a_zero_vector_is_left_alone_rather_than_given_a_direction(self) -> None:
        """It cannot be scaled to unit length, and inventing a direction for it
        would be inventing a meaning."""
        assert adapter._l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]

    def test_it_reuses_the_one_vertex_client(self, vertex_embeddings) -> None:
        """Not a second auth path. `apps.common.vertex` is the single place that
        knows what a Vertex credential is, so the LLM route and the embedding
        route cannot end up configured differently."""
        client = vertex_embeddings([0.1] * EMBEDDING_DIMENSIONS)

        adapter.embed("anything")

        assert client.calls, "the provider did not go through apps.common.vertex"

    def test_it_is_configured_by_the_vertex_settings_and_no_api_key(self, settings) -> None:
        settings.OPENAI_API_KEY = ""
        settings.GOOGLE_CLOUD_PROJECT = "primer-test"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"
        assert adapter.GoogleEmbeddingProvider().is_configured() is True

        settings.GOOGLE_APPLICATION_CREDENTIALS = ""
        assert adapter.GoogleEmbeddingProvider().is_configured() is False

    def test_a_model_that_ignores_the_requested_width_fails_loudly(self, vertex_embeddings) -> None:
        """Criterion 3's second half. `embed`'s guard compares against the
        *column*; this one compares against what we asked *this provider* for, so
        a model silently ignoring `output_dimensionality` is named as the cause
        rather than read as "somebody changed EMBEDDING_DIMENSIONS"."""
        vertex_embeddings([0.5] * 3072)

        with pytest.raises(MediaProviderError, match="3072 dimensions for an"):
            adapter.embed("anything")

    def test_a_width_the_column_cannot_hold_fails_before_anything_is_spent(
        self, vertex_embeddings, settings
    ) -> None:
        """The Vertex review's P2-2. `memory.embedding` is pinned at a module
        constant, and the guard used to compare against `EMBEDDING_DIMENSIONS` —
        which meant setting the env var to 3072 made the check *agree* with the
        mistake: the provider was asked for 3072, returned 3072, passed a guard
        whose message then claimed the column was `vector(3072)`, and Postgres
        rejected the insert at the end of a consolidation run instead.

        Now the setting is checked against the column, before the call, so the
        message names the real constraint and a misconfigured box does not spend
        money producing vectors it cannot store.
        """
        client = vertex_embeddings([0.5] * 3072)
        settings.EMBEDDING_DIMENSIONS = 3072

        with pytest.raises(MediaConfigurationError) as caught:
            adapter.embed("anything")

        message = str(caught.value)
        assert "EMBEDDING_DIMENSIONS=3072" in message
        assert f"vector({EMBEDDING_DIMENSIONS})" in message
        assert "migration" in message
        assert not client.calls, "the provider was called for a vector nothing could store"

    def test_an_empty_response_is_an_error_not_an_empty_vector(self, vertex_embeddings) -> None:
        vertex_embeddings([])

        with pytest.raises(MediaProviderError, match="no vector"):
            adapter.embed("anything")

    def test_a_provider_failure_stays_a_mediaerror(self, vertex_embeddings) -> None:
        """`consolidation` catches `MediaError` to write the memory without a
        vector rather than lose the memory. An SDK type would not be caught."""
        vertex_embeddings(boom=RuntimeError("vertex is down"))

        with pytest.raises(MediaProviderError, match="Could not reach"):
            adapter.embed("anything")

    def test_storing_and_asking_are_embedded_as_different_tasks(self, vertex_embeddings) -> None:
        """`gemini-embedding-001` is asymmetric. Getting this wrong costs ranking
        quality and nothing observable, which is why it is asserted."""
        client = vertex_embeddings([0.1] * EMBEDDING_DIMENSIONS)

        adapter.embed("a memory being stored")
        adapter.embed("a context being matched", task=adapter.TASK_QUERY)

        assert client.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
        assert client.calls[1]["config"].task_type == "RETRIEVAL_QUERY"

    @pytest.mark.django_db
    def test_the_retrieval_half_asks_as_a_query(self, vertex_embeddings, aarav) -> None:
        """The call site, not just the parameter: `memory.retrieval` is the asking
        end and has to say so, or the distinction above buys nothing."""
        from apps.memory import retrieval

        client = vertex_embeddings([0.1] * EMBEDDING_DIMENSIONS)
        Memory.objects.create(
            child=aarav,
            type=MemoryType.EVENT,
            content="Aarav counted three frogs.",
            embedding=[0.1] * EMBEDDING_DIMENSIONS,
            salience=0.8,
        )

        retrieval._nearest(aarav, "how did the counting go", limit=3)

        assert client.calls[-1]["config"].task_type == "RETRIEVAL_QUERY"


def test_the_shipped_embedding_route_needs_no_openai_account(settings) -> None:
    """§13.8's supply chain, as configuration. Every route that carries child data
    is one vendor now; this asserts the last one to move actually moved."""
    settings.OPENAI_API_KEY = ""
    settings.EMBEDDING_PROVIDER = "google"
    settings.GOOGLE_CLOUD_PROJECT = "primer-test"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"

    provider = adapter.select_embedding_provider()

    assert provider.name == "google"
    assert getattr(provider, "credential_env_var", "") == "GOOGLE_APPLICATION_CREDENTIALS"


def _env_example_lines() -> list[str]:
    """`.env.example`, as lines.

    Read from the file rather than from `settings`: the resolved setting reflects
    whatever `.env` the machine happens to have (a developer running offline sets
    `EMBEDDING_PROVIDER=local`, correctly), so asserting on it would be a test that
    passes in CI and fails on the founder's laptop for a good reason.
    """
    from pathlib import Path

    from django.conf import settings as django_settings

    example = Path(django_settings.REPO_ROOT) / ".env.example"
    return example.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# Cost tracing (§19.9, §22.4) — the Vertex review's P2-5
# ---------------------------------------------------------------------------
# Embeddings were the one kind of real spend `cost.record` never saw: they go
# through neither `ModelRouter.generate_structured` nor
# `media.service._get_or_generate`, so every consolidation run and every
# branch-point recall was money `cost/session` did not count. The fix puts the
# span at `embed()`, which is the seam every caller already has to use.
class TestEmbeddingsAreCostTraced:
    @pytest.mark.django_db
    def test_a_vertex_embedding_writes_a_priced_span(self, vertex_embeddings) -> None:
        """Priced from Vertex's own token count, not from a guess about it."""
        from apps.analytics.models import CostKind, GenerationCost

        vertex_embeddings([0.1] * EMBEDDING_DIMENSIONS, tokens=11)

        adapter.embed("Aarav named the little dragon Sparkle.")

        span = GenerationCost.objects.get(operation="embedding")
        assert span.kind == CostKind.LLM
        assert span.provider == "google"
        assert span.model == "gemini-embedding-001"
        assert span.units["input_tokens"] == 11
        assert "estimated" not in span.units
        # Priced, and priced at something. A zero here would mean the route had
        # quietly landed in the "we do not know what this costs" column.
        assert span.priced is True
        assert span.cost_usd > 0

    @pytest.mark.django_db
    def test_a_provider_with_no_meter_is_estimated_and_says_so(self, settings) -> None:
        """`local-hash` reports no usage, so the span is a character estimate —
        marked `estimated`, because an unmarked estimate on a paid route would
        read as a measurement. It is priced at a known zero rather than left
        unknown: the hashing trick genuinely costs nothing."""
        from apps.analytics.models import GenerationCost

        settings.EMBEDDING_PROVIDER = "local"

        adapter.embed("the frog chorus by the river")

        span = GenerationCost.objects.get(operation="embedding")
        assert span.provider == "local-hash"
        assert span.units["estimated"] is True
        assert span.priced is True
        assert span.cost_usd == 0

    @pytest.mark.django_db
    def test_a_call_that_never_reached_the_model_is_not_priced(self, vertex_embeddings) -> None:
        """The mirror of the router's rule. A transport failure was never billed,
        and a character estimate for it would over-report spend that does not
        exist — the same dishonesty as under-reporting spend that does."""
        from apps.analytics.models import GenerationCost

        vertex_embeddings(boom=RuntimeError("vertex is down"))

        with pytest.raises(MediaProviderError):
            adapter.embed("anything")

        assert not GenerationCost.objects.filter(operation="embedding").exists()

    @pytest.mark.django_db
    def test_a_metered_call_that_is_then_refused_still_writes_its_span(
        self, vertex_embeddings
    ) -> None:
        """The expensive-failure case (§22.4). The model answered and was billed;
        we refused the answer for its width. That is spend, and it is exactly the
        spend worth seeing."""
        from apps.analytics.models import GenerationCost

        vertex_embeddings([0.5] * 3072, tokens=9)

        with pytest.raises(MediaProviderError, match="3072 dimensions for an"):
            adapter.embed("anything")

        span = GenerationCost.objects.get(operation="embedding")
        assert span.units["input_tokens"] == 9
        assert span.priced is True


def test_the_documented_configuration_is_the_all_google_one() -> None:
    """`.env.example` is what somebody copies, so it is where a doc/code drift
    actually costs something — the previous build shipped three documents saying
    `AI_PROVIDER_ORDER=gemini`, which is not a provider.

    **The whole deployment, not just the embedding route.** This test used to
    assert the three `EMBEDDING_*` lines and nothing else, so it passed on a file
    whose documented LLM was Claude and whose documented image gate was `auto` —
    which is the file the Vertex review found (P1-3): a template describing two
    different deployments, one route pre-switched to Google and the rest left on
    Anthropic. Copied verbatim that is a box `prod.py` now refuses to boot, and
    before the P1-1 guard it was a box that booted and silently never embedded
    anything. A test named for one deployment has to check the whole of it.
    """
    lines = _env_example_lines()

    # The LLM half: the router, the writer, the grader.
    assert "AI_PROVIDER_ORDER=google" in lines
    assert "AI_BEAT_MODEL=gemini-2.5-flash" in lines
    assert "AI_SAFETY_MODEL=gemini-2.5-flash-lite" in lines
    # The image gate. `auto` is not good enough here: it takes the first provider
    # with credentials, Anthropic first, so on the all-Google deployment it is the
    # setting that most easily says one thing and does another (P1-2).
    assert "IMAGE_MODERATION=auto" in lines
    assert "IMAGE_MODERATION_PROVIDER=google" in lines
    assert "IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite" in lines
    # The embedding half.
    assert "EMBEDDING_PROVIDER=google" in lines
    assert "EMBEDDING_MODEL=gemini-embedding-001" in lines
    # 1536 is what makes this a no-migration move: `gemini-embedding-001` is a
    # Matryoshka model asked for a prefix of its native 3072.
    assert "EMBEDDING_DIMENSIONS=1536" in lines
    # And the credential block the four routes above share is present for the
    # operator to fill in, rather than being something they have to discover.
    for key in ("GOOGLE_CLOUD_PROJECT=", "GOOGLE_APPLICATION_CREDENTIALS="):
        assert [line for line in lines if line.startswith(key)], key
    # No OpenAI credential is presented as something to fill in.
    assert not [line for line in lines if line.startswith("OPENAI_API_KEY=")]


def test_the_other_providers_survive_as_commented_alternatives() -> None:
    """The seams are real, and the template has to keep saying so.

    `anthropic` and `openrouter` are implemented, selectable and tested; flipping
    the file to one deployment must not read as deleting the other two. The rule
    is that every alternative is present and commented — one comment character
    away, never uncommented next to a value that contradicts it.
    """
    lines = _env_example_lines()
    commented = [line.strip().lstrip("#").strip() for line in lines if line.strip().startswith("#")]

    assert "AI_PROVIDER_ORDER=anthropic" in commented
    assert any(line.startswith("AI_BEAT_MODEL=claude-opus-5") for line in commented)
    assert any(line.startswith("AI_SAFETY_MODEL=claude-haiku") for line in commented)
    # And an Anthropic key is still somewhere to put one, just not a requirement.
    assert [line for line in lines if line.startswith("ANTHROPIC_API_KEY=")]


def test_the_column_comment_says_where_1536_comes_from() -> None:
    """The Vertex review's P2-3, and the one place the sweep missed.

    The constant's comment called 1536 "the native width of the shipped provider".
    `gemini-embedding-001`'s native width is **3072**; 1536 is a Matryoshka
    truncation, and that is precisely the fact which makes "no migration, no index
    rebuild" true. Every other file states it correctly. A comment that is wrong
    about the single thing it exists to explain will be trusted by whoever next
    considers changing the number.
    """
    from pathlib import Path

    from apps.memory import models as memory_models

    source = Path(str(memory_models.__file__)).read_text(encoding="utf-8")
    header = source.split("EMBEDDING_DIMENSIONS = 1536")[0]

    assert "3072" in header
    assert "native width of the shipped provider" not in header


def test_the_embedding_model_still_satisfies_the_training_rule() -> None:
    """§13.8. A bare Vertex id carries no `:free` suffix; the guard that blocks a
    prompt-reselling route for the three LLM settings covers this one too."""
    from apps.common import model_ids

    assert "EMBEDDING_MODEL" in model_ids.CHILD_DATA_MODEL_SETTINGS
    assert model_ids.training_error(setting="EMBEDDING_MODEL", model="gemini-embedding-001") is None
    assert model_ids.training_error(setting="EMBEDDING_MODEL", model="vendor/model:free")


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
