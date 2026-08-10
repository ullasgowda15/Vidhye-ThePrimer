"""Long-term memory (PRODUCT.md §11.3, §17.3).

This is the table the whole product is built to fill. §11 calls memory "the
differentiator and the moat", and the reason is the felt one: a companion that
remembers the dragon a child named eight months ago is a friend, and one that
does not is a story generator with a mascot.

Four properties of this table are load-bearing, and each is a decision visible in
the columns below.

**It is selective, not a transcript.** Every row here survived a salience score
(`apps.memory.salience`). The mundane is dropped at consolidation and never
reaches this table at all — which is what keeps retrieval about what matters, and
keeps the prompt bounded whatever the child's history is (§11.4). A design that
stored everything and ranked later would be cheaper to write and would make
"remembers like a friend" impossible: the ranking problem gets harder every
session, forever.

**It is typed *and* embedded.** The embedding answers "what is relevant to this
moment"; the type answers "what kind of thing is this", which is what lets
retrieval apply rules rather than only similarity — always include active fears,
prefer recent salient events (§11.9). Vector search alone would make a fear
retrievable only when the story happened to be about it.

**It decays but is never rewritten.** `decay`, `recall_count` and
`last_recalled_at` are the recall-strength bookkeeping (Day 9 reads and writes
them). A memory that stops being retrieved fades in ranking; it is not deleted,
because the whole promise is durability measured in years (§11.10).

**It is derived, so the rows it came from are disposable.** §17.5: adventures own
beats and choices, and memories are *derived* from them. That is what lets a
retention job prune raw behavioural rows aggressively while the parent-owned
memory graph lives on — short retention on the transcript, durable retention on
the meaning.

`content` is encrypted at rest (`apps.common.crypto`). See that module for why
the disk-level encryption a managed Postgres already gives us is not the same
thing.
"""

from __future__ import annotations

from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.common.crypto import EncryptedTextField
from apps.common.models import UUIDModel

#: The width of one embedding vector. Fixed in the column, so it is fixed for the
#: table: changing providers to one with a different native width is a migration
#: and a re-embed, not a config flip. 1536 is §17.3's number and the native width
#: of the shipped provider (`media.adapters.embedding`).
EMBEDDING_DIMENSIONS = 1536


class MemoryType(models.TextChoices):
    """§11.1's typed structure, as a closed set.

    Closed rather than free text because the types are what retrieval *rules* are
    written against (§11.9) — "always include active fears" is a query on this
    column — and a model that invented `feeling` alongside `emotional_moment`
    would silently make that rule cover half the rows it should.

    `ENTITY` and `PREFERENCE` carry §11.5's first-class cases: the dragon a child
    named, the treasure shell from the beach adventure, the colour purple. They
    are the reciprocity fuel of §9.3 and the reason this table is not just an
    event log.
    """

    EVENT = "event", "Something that happened"
    ENTITY = "entity", "A character, object or place that matters"
    PREFERENCE = "preference", "Something the child likes or wants"
    FEAR = "fear", "Something the child is afraid of"
    SKILL_MOMENT = "skill_moment", "Evidence of a skill, tried or mastered"
    EMOTIONAL_MOMENT = "emotional_moment", "A moment with real feeling in it"


class Memory(UUIDModel):
    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="memories",
    )

    #: One of `MemoryType`. `max_length=32` per §17.3; `choices` is validation and
    #: an admin filter, not a DB constraint, so a future type is a code change
    #: rather than a migration on a table that must never be locked.
    type = models.CharField(max_length=32, choices=MemoryType.choices)

    #: A short natural-language summary — one sentence, in the register a
    #: companion would say it in, because that is how it re-enters a prompt
    #: (§19.1's `memory_bundle`). Encrypted at rest; see the class docstring.
    content = EncryptedTextField()

    #: The retrieval handle (§11.9). **Nullable**, which is a deliberate
    #: asymmetry: an embedding provider having a bad day must cost us the *vector*
    #: and not the memory. A row with no embedding is still reachable by the rules
    #: half of retrieval (type, salience, recency) and can be re-embedded later,
    #: whereas a memory we declined to write because a side-car API was down is
    #: gone for good.
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)

    #: How much this mattered, 0-1, as scored at consolidation (§11.4). Never
    #: recomputed: it is a record of how the moment landed, and re-scoring it
    #: later from a different model's opinion would rewrite the child's history.
    salience = models.FloatField(default=0.0)

    #: Emotional tone, -1 (sad, frightened) to 1 (delighted). Separate from
    #: salience because intensity and direction are different things — a
    #: frightening moment and a joyful one can be equally worth remembering, and
    #: they must be *used* very differently (§11.6).
    valence = models.FloatField(default=0.0)

    #: What this memory is about: `["character:sparkle", "object:shell"]`. The
    #: seed of §11.7's interest graph and the relationship graph, and the reason
    #: this column is plain JSON rather than encrypted like `content` — the graphs
    #: are built by querying it, and these are story-world keys rather than
    #: anything a child disclosed.
    entities = models.JSONField(default=list, blank=True)

    #: The adventure this was consolidated from. SET_NULL, like every other FK
    #: pointing at story rows, and for the sharper reason here: §17.5 prunes raw
    #: beats and keeps memories, so this FK is *expected* to go null eventually.
    #: A memory whose source has been pruned is still a memory.
    source_adventure = models.ForeignKey(
        "story.Adventure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories",
    )

    #: Recall bookkeeping (Day 9). `last_recalled_at` is null for a memory that
    #: has never been used, which is different from one recalled long ago — the
    #: ranking treats them differently, so the column may not default to `now`.
    last_recalled_at = models.DateTimeField(null=True, blank=True)
    recall_count = models.IntegerField(default=0)

    #: Recall strength, 1.0 at birth, reduced over time by the decay pass. A
    #: multiplier on `salience` at ranking time rather than a mutation of it, so
    #: "this mattered" and "this is fading" stay separable.
    decay = models.FloatField(default=1.0)

    #: §17.3's `deleted_at`. Nothing writes it yet — parent-facing deletion is
    #: Day 12 — and it is here now for the reason `Character.salience` was: a
    #: column added after the fact cannot be backfilled with the history it was
    #: meant to record. A parent deleting one memory from their dashboard must
    #: leave a tombstone rather than a hole, so that the consolidation job does
    #: not helpfully rediscover the thing they asked us to forget.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-salience", "-created_at"]
        indexes = [
            # §17.3's two btree indexes. The first serves the rules half of
            # retrieval ("this child's most salient fears"); the second serves
            # the decay pass and recency ranking.
            models.Index(fields=["child", "type", "salience"], name="memory_child_type_salience"),
            models.Index(fields=["child", "last_recalled_at"], name="memory_child_recalled"),
            # §17.3's ANN index. **HNSW rather than IVFFlat**, which §17.3 leaves
            # open: IVFFlat has to be built against existing data to pick its lists
            # and degrades badly when built on an empty or tiny table — which is
            # exactly the state this table is in on the day it ships and for every
            # new deployment after. HNSW needs no training pass, so the index is
            # correct from the first row.
            #
            # `vector_cosine_ops` because these are text embeddings: the shipped
            # provider returns unit-normalised vectors and cosine is what the
            # provider's own similarity is defined in. It has to match the distance
            # function the query uses, or the index is built and silently never
            # used — Day 9's retrieval orders by `CosineDistance`.
            #
            # `m` and `ef_construction` are pgvector's defaults, stated rather than
            # left implicit: they are the build-time/recall trade-off, and a family's
            # whole memory graph is thousands of rows, so there is nothing to tune
            # yet and the defaults are the honest choice.
            HnswIndex(
                name="memory_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]
        verbose_name_plural = "memories"

    def __str__(self) -> str:
        # No content, deliberately. `__str__` ends up in log lines, admin change
        # messages and exception text, and this class exists to keep a child's
        # memories out of all three (§13.8).
        return f"{self.type} ({self.salience:.2f}) for {self.child_id}"
