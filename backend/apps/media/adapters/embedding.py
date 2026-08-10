"""Text-embedding adapters (PRODUCT.md §16.6, §11.3).

The third adapter in this package, and deliberately the same shape as the other
two: one interface — `embed(text) -> list[float]` — a provider name in
configuration, one real implementation, and typed seams for the vendors that are
declared but not built. `apps.memory` never learns which vendor produced a
vector, exactly as the story orchestrator never learns which model drew a picture.

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


@runtime_checkable
class EmbeddingProvider(Protocol):
    """One embedding model, reduced to what the memory service needs."""

    name: str

    #: The width of the vectors this provider returns. Declared rather than
    #: discovered, so a mismatch with the column is caught by `embed` on the
    #: first call instead of by Postgres at the end of a consolidation.
    dimensions: int

    def is_configured(self) -> bool: ...

    def embed(self, text: str) -> list[float]:
        """Return the embedding of `text`."""
        ...


class OpenAIEmbeddingProvider:
    """`text-embedding-3-small` — the implemented real provider.

    Chosen for the boring reasons: 1536 native dimensions (so §17.3's column
    width is the provider's own default rather than a truncation), a price low
    enough that a family's whole memory graph costs less than one illustration,
    and an SDK already in `requirements.txt` for the OpenRouter path.

    **`OPENAI_API_KEY` is used here and nowhere else.** §16.1 puts Claude on the
    generation path and OpenAI on the fallback seam; Anthropic publishes no
    embedding model, so this is the one place the OpenAI key is load-bearing
    rather than optional. That is worth saying out loud because it means memory
    embedding and story generation fail independently, which is the behaviour we
    want: an Anthropic outage must not also stop us remembering.
    """

    name = "openai"
    credential_env_var = "OPENAI_API_KEY"
    dimensions = 1536

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or settings.OPENAI_API_KEY
        self._model = model or settings.EMBEDDING_MODEL

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def embed(self, text: str) -> list[float]:
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

    def embed(self, text: str) -> list[float]:
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

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(f"Embedding provider '{self.name}' is not implemented yet.")


class VoyageEmbeddingProvider(_UnimplementedEmbeddingProvider):
    """Fallback seam — Voyage AI, Anthropic's recommended embedding partner."""

    name = "voyage"
    dimensions = 1024


class GoogleEmbeddingProvider(_UnimplementedEmbeddingProvider):
    """Fallback seam — Google `text-embedding-004`. Not implemented."""

    name = "google"
    dimensions = 768


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
            f"Embedding provider '{name}' has no credentials. Set OPENAI_API_KEY in "
            "your .env, or set EMBEDDING_PROVIDER=local for deterministic offline "
            "vectors while developing."
        )
    return provider


def embed(text: str, provider: EmbeddingProvider | None = None) -> list[float]:
    """Embed one memory's content. The seam every caller should use.

    Raises `MediaConfigurationError` when no provider is usable and
    `MediaProviderError` when one was reached and did not answer. Both are
    `MediaError`, which is the single exception the consolidation job catches
    before writing the memory without a vector.
    """
    if not text.strip():
        raise MediaProviderError("Refusing to embed an empty string.")

    chosen = provider if provider is not None else select_embedding_provider()
    vector = chosen.embed(text)

    expected = settings.EMBEDDING_DIMENSIONS
    if len(vector) != expected:
        # Loud rather than truncated or padded. A silently reshaped vector is a
        # vector that ranks wrongly forever, and the failure would surface as
        # "retrieval feels random" months later.
        raise MediaProviderError(
            f"Embedding provider '{chosen.name}' returned {len(vector)} dimensions, "
            f"but memory.embedding is vector({expected}). Either point "
            "EMBEDDING_PROVIDER at a model of that width or migrate the column."
        )
    return vector
