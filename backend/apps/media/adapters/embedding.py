"""Text-embedding adapters (PRODUCT.md §16.6, §11.3).

The third adapter in this package, and deliberately the same shape as the other
two: one interface — `embed(text) -> list[float]` — a provider name in
configuration, real implementations, and typed seams for the vendors that are
declared but not built. `apps.memory` never learns which vendor produced a
vector, exactly as the story orchestrator never learns which model drew a picture.

**The shipped provider is `google`** — `gemini-embedding-001` on Vertex AI, the
same service account that writes beats, grades them, consolidates memory and
looks at generated pictures. That is what makes every route carrying child data
one vendor under one credential and one set of terms (§13.8), and it is why this
product needs no OpenAI account. `OpenAIEmbeddingProvider` still works and is
still selectable; it is simply no longer required.

**Why it lives here rather than in `apps.memory`.** An embedding is a generated
asset from a third-party provider with a key, a timeout and a failure mode, which
is what this package is for. Putting the provider registry next to the memory
model would give `apps.memory` a vendor dependency and would mean the next
consumer of embeddings — §11.7's interest graph, semantic dedupe — either
imports from the memory app or grows a second copy.

**The width is the contract, not a suggestion.** `memory.embedding` is a
`vector(1536)` column, so a provider that returns 768 floats cannot be stored —
Postgres rejects the insert, and it rejects it at the end of a consolidation job
rather than at configuration time. So `dimensions` is declared per provider and
checked on the way out (`embed`), which turns "half of last night's memories have
no vector" into a startup-shaped error naming the mismatch.

**This adapter does not fail over, and that is the honest choice.** The voice
adapter chains providers because a missing voice is a child stuck on an
unreadable screen (§4.9). Nothing here is on a child's path: consolidation runs
after the sitting has ended, with nobody waiting, and a memory written without a
vector is still a memory — reachable by the rules half of retrieval (§11.9) and
re-embeddable later. Two vendors' worth of complexity to avoid a degradation
nobody can perceive would be the wrong trade; `consolidation` logs the gap
instead.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol, runtime_checkable

from django.conf import settings

from apps.media.exceptions import MediaConfigurationError, MediaProviderError

logger = logging.getLogger(__name__)

#: An embedding is one small POST. Short, because this runs inside a Celery task
#: that has a whole session's worth of candidates to get through.
REQUEST_TIMEOUT_SECONDS = 30.0

#: The longest string worth embedding. A memory `content` is one sentence by
#: construction (§11.3), so anything approaching this is a bug upstream — the cap
#: is here so that a bug upstream costs a truncated vector rather than a provider
#: bill.
MAX_INPUT_CHARS = 2000


#: What an embedding is *for*. Modern embedding models are trained
#: **asymmetrically**: the vector for a stored document and the vector for the
#: query that should find it are produced by the same weights under different
#: instructions, and using one where the other belongs costs real ranking quality.
#: Named here rather than in the one provider that honours them, because the
#: *call sites* are what know which is which — `memory.consolidation` is storing,
#: `memory.retrieval` is asking — and that knowledge should not have to be
#: rediscovered by the next provider that can use it.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """One embedding model, reduced to what the memory service needs."""

    name: str

    #: The width of the vectors this provider returns. Declared rather than
    #: discovered, so a mismatch with the column is caught by `embed` on the
    #: first call instead of by Postgres at the end of a consolidation.
    dimensions: int

    def is_configured(self) -> bool: ...

    def embed(self, text: str, *, task: str = TASK_DOCUMENT) -> list[float]:
        """Return the embedding of `text`.

        `task` is a *hint*, not a contract: a provider whose model is symmetric
        ignores it, which is why it has a default and why no caller has to know
        which kind of provider is configured.
        """
        ...


class OpenAIEmbeddingProvider:
    """`text-embedding-3-small` — a working provider, no longer the shipped one.

    Chosen originally for the boring reasons: 1536 native dimensions (so §17.3's
    column width was the provider's own default rather than a truncation), a
    price low enough that a family's whole memory graph costs less than one
    illustration, and an SDK already in `requirements.txt` for the OpenRouter
    path. It is kept because all three are still true and because switching back
    should stay a config change.

    **It is no longer required, and that is the point of keeping this note.** The
    line that used to be here said `OPENAI_API_KEY` was "used here and nowhere
    else" and was "the one place the OpenAI key is load-bearing" — true when
    written, and false since embeddings moved to `GoogleEmbeddingProvider`. The
    product now needs no OpenAI account at all: every route carrying child data
    is one Vertex service account under one set of terms (§13.8). Selecting this
    provider means adding a vendor back, which is a decision rather than a
    default, and the 1536 width means it can be selected without a migration.
    """

    name = "openai"
    credential_env_var = "OPENAI_API_KEY"
    dimensions = 1536

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or settings.OPENAI_API_KEY
        self._model = model or settings.EMBEDDING_MODEL

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def embed(self, text: str, *, task: str = TASK_DOCUMENT) -> list[float]:
        # `task` is ignored: `text-embedding-3-small` is symmetric — one set of
        # weights, no document/query distinction to make.
        from openai import OpenAI, OpenAIError

        client = OpenAI(api_key=self._api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response = client.embeddings.create(model=self._model, input=text[:MAX_INPUT_CHARS])
        except OpenAIError as exc:
            raise MediaProviderError(f"Could not reach the embedding provider: {exc}") from exc

        if not response.data:
            raise MediaProviderError("Embedding provider returned no vector.")
        return [float(value) for value in response.data[0].embedding]


class LocalHashEmbeddingProvider:
    """A deterministic offline embedding. Development and CI only.

    **It is not a semantic model and never pretends to be.** It is the hashing
    trick: content words are stemmed, hashed into buckets, weighted by frequency,
    and the resulting sparse vector is L2-normalised. Two texts about frogs land
    near each other because they *share the word* frog, not because anything
    understood them — "puppy" and "dog" are orthogonal here and would be close
    under a real model.

    What that buys is worth having anyway, and is why the whole pipeline is
    exercisable with no key and no spend: the vector column, the HNSW index, the
    cosine ordering of a nearest-neighbour query, and the shape of a retrieval
    bundle are all real. A test can assert that querying "the dragon Sparkle"
    ranks the dragon memory above the counting memory, and that assertion is
    about our index and our query rather than about a vendor's weights.

    Every consolidation run that used it says so in its log line, so a database
    full of lexical vectors is never mistaken for a semantic one.
    """

    name = "local"
    credential_env_var = ""
    provider_label = "local-hash"
    dimensions = 1536

    _WORD = re.compile(r"[a-z0-9']+")

    def is_configured(self) -> bool:
        return True

    def embed(self, text: str, *, task: str = TASK_DOCUMENT) -> list[float]:
        # `task` is ignored: there are no weights here to instruct. A hash is a
        # hash whichever end of the query it is on.
        vector = [0.0] * self.dimensions
        words = self._WORD.findall(text.lower()[:MAX_INPUT_CHARS])
        for word in words:
            stem = word[:6]
            # sha256 rather than `hash()`: Python salts string hashing per
            # process, so the same memory would embed differently after a restart
            # and yesterday's vectors would stop matching today's queries.
            digest = hashlib.sha256(stem.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            # The sign comes from the hash too, so two different words landing in
            # one bucket are as likely to cancel as to reinforce — which keeps a
            # collision from reading as a similarity.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # A string with no content words at all. An all-zero vector has no
            # cosine distance to anything, so it would sort arbitrarily against
            # every query; a single fixed non-zero component makes it merely far
            # from everything, which is the truth about it.
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class _UnimplementedEmbeddingProvider:
    """Typed seam for an embedding vendor that is declared but not built."""

    name = "unimplemented"
    credential_env_var = ""
    dimensions = 0

    def is_configured(self) -> bool:
        return False

    def embed(self, text: str, *, task: str = TASK_DOCUMENT) -> list[float]:
        raise NotImplementedError(f"Embedding provider '{self.name}' is not implemented yet.")


class VoyageEmbeddingProvider(_UnimplementedEmbeddingProvider):
    """Fallback seam — Voyage AI, Anthropic's recommended embedding partner."""

    name = "voyage"
    dimensions = 1024


class GoogleEmbeddingProvider:
    """`gemini-embedding-001` on Vertex AI — the shipped provider (§11.3, §16.6).

    **The same service account as everything else.** Beats, the safety grader, the
    memory digest and the image gate already run on Vertex
    (`apps.common.vertex`); this is the fourth and last child-data route to move,
    and it moves the product to *one* vendor with *one* credential under the GCP
    terms that exclude our inputs from training (§13.8). Before it, a family's
    memories were the one thing that still needed an OpenAI account.

    **1536 by truncation, not by native width, and that is the whole design.**
    `gemini-embedding-001` is a Matryoshka model: its native 3072-dimensional
    output is trained so that any prefix of it is still a usable embedding. Asking
    for `output_dimensionality=1536` therefore keeps `memory.embedding` a
    `vector(1536)` column with its existing HNSW index — no migration, no
    re-embed, no index rebuild — and costs some accuracy rather than correctness.

    **Truncated output is not unit-length, and this normalizes it.** Measured
    against the live API on 13 Aug 2026, embedding the same sentence:

        3072 dims -> L2 norm 1.000000   (the native width; already normalized)
        1536 dims -> L2 norm 0.690528
         768 dims -> L2 norm 0.588767

    Google's guidance is to re-normalize anything below the native width, and this
    provider does. **Be precise about why**, because the tempting reason is wrong:
    retrieval ranks with pgvector's `CosineDistance` under a `vector_cosine_ops`
    index, and cosine is magnitude-invariant — so today, unnormalized vectors
    would rank *identically*. What normalization actually buys is that the column
    stays a set of unit vectors, which is what makes it safe to ever put an L2 or
    inner-product query or index over it, what keeps these vectors comparable in
    magnitude with `LocalHashEmbeddingProvider`'s (a database holding both should
    not hold two different scales), and what makes a stored vector mean the same
    thing if `EMBEDDING_DIMENSIONS` is ever changed. It costs one pass over 1536
    floats.
    """

    name = "google"
    credential_env_var = "GOOGLE_APPLICATION_CREDENTIALS"

    def __init__(self, model: str = "", dimensions: int = 0) -> None:
        self._model = model or settings.EMBEDDING_MODEL
        #: Read from settings rather than fixed as a class attribute, unlike the
        #: providers above: this model's width is a *request parameter*, so the
        #: only honest declared width is the one we are going to ask for.
        self.dimensions: int = dimensions or settings.EMBEDDING_DIMENSIONS

    def is_configured(self) -> bool:
        """True when the Vertex credentials are set — the same question the LLM
        provider asks, through the same module, so a deployment cannot be
        configured for one and not the other."""
        from apps.common import vertex

        return vertex.is_configured()

    def embed(self, text: str, *, task: str = TASK_DOCUMENT) -> list[float]:
        from google.genai import types

        from apps.common import vertex

        try:
            response = vertex.client().models.embed_content(
                model=self._model,
                contents=text[:MAX_INPUT_CHARS],
                config=types.EmbedContentConfig(
                    output_dimensionality=self.dimensions,
                    task_type=task,
                ),
            )
        except Exception as exc:
            # Same boundary as the OpenAI provider's: `MediaProviderError` is what
            # `consolidation` catches to write the memory without a vector rather
            # than lose the memory. An SDK type reaching that handler would not be
            # caught by it.
            raise MediaProviderError(f"Could not reach the embedding provider: {exc}") from exc

        embeddings = getattr(response, "embeddings", None) or []
        values = list(getattr(embeddings[0], "values", None) or []) if embeddings else []
        if not values:
            raise MediaProviderError("Embedding provider returned no vector.")

        if len(values) != self.dimensions:
            # A second guard in front of `embed`'s. That one compares against the
            # *column*; this one compares against what we asked this provider for,
            # so a model that silently ignores `output_dimensionality` is named
            # here as the cause instead of surfacing as a column mismatch whose
            # obvious reading is "somebody changed EMBEDDING_DIMENSIONS".
            raise MediaProviderError(
                f"'{self._model}' returned {len(values)} dimensions for an "
                f"output_dimensionality of {self.dimensions}."
            )

        return _l2_normalize([float(value) for value in values])


def _l2_normalize(vector: list[float]) -> list[float]:
    """Scale a vector to unit length. See `GoogleEmbeddingProvider` on why.

    A zero vector is returned unchanged rather than divided: it cannot be scaled
    to unit length, and inventing a direction for it would be inventing a meaning.
    `embed`'s width guard still passes it, and it is the honest representation of
    "the model returned nothing directional" — which for a one-sentence memory
    would be a provider fault worth seeing rather than papering over.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


EMBEDDING_PROVIDERS: dict[str, type] = {
    OpenAIEmbeddingProvider.name: OpenAIEmbeddingProvider,
    LocalHashEmbeddingProvider.name: LocalHashEmbeddingProvider,
    VoyageEmbeddingProvider.name: VoyageEmbeddingProvider,
    GoogleEmbeddingProvider.name: GoogleEmbeddingProvider,
}


def provider_label(provider: EmbeddingProvider) -> str:
    """How a provider is named in a log line — `local` reports as `local-hash`."""
    return getattr(provider, "provider_label", provider.name)


def select_embedding_provider() -> EmbeddingProvider:
    """The provider named by `EMBEDDING_PROVIDER`, if it can actually be called."""
    name = settings.EMBEDDING_PROVIDER
    factory = EMBEDDING_PROVIDERS.get(name)
    if factory is None:
        raise MediaConfigurationError(
            f"Unknown embedding provider '{name}' in EMBEDDING_PROVIDER. "
            f"Known providers: {', '.join(sorted(EMBEDDING_PROVIDERS))}."
        )

    provider = factory()
    if not provider.is_configured():
        raise MediaConfigurationError(
            f"Embedding provider '{name}' has no credentials. Set the credential it "
            f"needs ({getattr(factory, 'credential_env_var', '') or 'see the adapter'}) "
            "in your .env - for the shipped `google` provider that is the Vertex "
            "service account, GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION / "
            "GOOGLE_APPLICATION_CREDENTIALS - or set EMBEDDING_PROVIDER=local for "
            "deterministic offline vectors while developing."
        )
    return provider


def embed(
    text: str,
    provider: EmbeddingProvider | None = None,
    *,
    task: str = TASK_DOCUMENT,
) -> list[float]:
    """Embed one memory's content. The seam every caller should use.

    `task` says what the vector is *for* — `TASK_DOCUMENT` for a memory being
    stored, `TASK_QUERY` for the context being matched against them. It defaults
    to the storing case because that is the overwhelming majority of calls and
    because a wrong default should be the one that affects the smaller side.

    Raises `MediaConfigurationError` when no provider is usable or when the
    configured width is not the column's, and `MediaProviderError` when a provider
    was reached and did not answer. Both are `MediaError`, which is the single
    exception the consolidation job catches before writing the memory without a
    vector.
    """
    if not text.strip():
        raise MediaProviderError("Refusing to embed an empty string.")

    # The **column's** width, not the setting's. `memory.embedding` is pinned at a
    # module constant, so a guard that compared against `EMBEDDING_DIMENSIONS`
    # agreed with whatever the env var said: set it to 3072, and a 3072-wide vector
    # passed a check whose message claimed the column was vector(3072), and
    # Postgres rejected the insert at the end of a consolidation run instead. The
    # setting is what the provider is *asked* for; this is what can be stored — so
    # a disagreement between them is checked here, before anything is spent on a
    # vector that could not be written down.
    from apps.memory.models import EMBEDDING_DIMENSIONS as COLUMN_DIMENSIONS

    if settings.EMBEDDING_DIMENSIONS != COLUMN_DIMENSIONS:
        raise MediaConfigurationError(
            f"EMBEDDING_DIMENSIONS={settings.EMBEDDING_DIMENSIONS}, but "
            f"memory.embedding is vector({COLUMN_DIMENSIONS}) - every vector this "
            "produces would be rejected by Postgres on insert. The width is a "
            "column, so changing it is a migration plus a re-embed of every row, "
            "not a config flip."
        )

    chosen = provider if provider is not None else select_embedding_provider()
    vector = chosen.embed(text, task=task)

    if len(vector) != COLUMN_DIMENSIONS:
        # Loud rather than truncated or padded. A silently reshaped vector is a
        # vector that ranks wrongly forever, and the failure would surface as
        # "retrieval feels random" months later.
        raise MediaProviderError(
            f"Embedding provider '{chosen.name}' returned {len(vector)} dimensions, "
            f"but memory.embedding is vector({COLUMN_DIMENSIONS}). Either point "
            "EMBEDDING_PROVIDER at a model of that width or migrate the column."
        )
    return vector
