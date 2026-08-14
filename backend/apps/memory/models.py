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

**Two derived graphs live alongside it** (Day 11): `InterestEdge` is §11.7's
weighted, time-decaying record of what the child loves, and `WorldPlace` is §8.3's
growing map of where they have been. Both are here rather than in `story` because
§8.3 says it outright — "the world state is memory" — and because both are built
by *reading a finished sitting back*, which is what everything else in this app
does. `Memory` is what happened; these two are what it added up to.
"""

from __future__ import annotations

from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.common.crypto import EncryptedTextField
from apps.common.models import UUIDModel

#: The width of one embedding vector. Fixed in the column, so it is fixed for the
#: table: changing it is a migration and a re-embed, not a config flip. 1536 is
#: §17.3's number, and it is a *truncation* rather than the shipped provider's
#: native width — `gemini-embedding-001` is a Matryoshka model whose native output
#: is 3072 and whose prefixes are usable embeddings, so it is asked for 1536
#: (`output_dimensionality`) and re-normalized (`media.adapters.embedding`).
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


class InterestEdge(UUIDModel):
    """One thing this child loves, and how much (§11.7, §17.3 `interest_edge`).

    The interest graph is the derived half of §17.2's note that "the richest
    personalization lives in the derived interest graph and memory, not these seed
    fields". `Child.interests` is what a parent typed once at onboarding; this is
    what the child has actually reached for, session after session, and it is the
    thing §2.4 calls the central bet — a story about the thing they love is a story
    they attend to completely.

    **One row per (child, topic), and the topic is a bare word.** Not a namespaced
    entity key like `Memory.entities` (`character:bramble`), because the two answer
    different questions: an entity key names a *participant* in one remembered
    moment, and a topic names a *subject* that outlives it. `apps.memory.interest`
    is what converts one into the other, and its vocabulary is deliberately the same
    shape as a parent's seed word ("frogs", "space") so the graph and the seeds can
    be merged into one ranking rather than two.

    **The weight is a number with a date attached, and the date is load-bearing.**
    §11.7 asks for a *time-decaying* graph, and what makes it time-decaying here is
    not a nightly job: it is that `weight` is only ever meaningful as of
    `last_reinforced_at`, and every reader ages it to now
    (`interest.current_weight`). That is why nothing rewrites the weights of topics
    a sitting did not touch — see the module docstring of `apps.memory.interest` for
    why a decay pass that wrote every row would make this column lie about when a
    child last cared.
    """

    # `interest_edges`, not `interests`: `Child.interests` is already the seed JSON
    # column (§17.2's cold start), and a reverse accessor by that name does not
    # merely shadow it — Django refuses to start (fields.E302). The name is worth
    # the friction it causes here, because the two really are different things and a
    # reader who confuses them will reach for the wrong one.
    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="interest_edges",
    )

    #: The subject, normalised by `interest.topic_tokens` — lowercase, one word,
    #: crudely singular. `frogs` and `Frog` are the same interest and must not be
    #: two rows: a fork here is the §11.4 repetition problem again, one table over,
    #: and the thing it quietly demotes is always the favourite.
    topic = models.CharField(max_length=64)

    #: How much they love it, 0-1, **as of `last_reinforced_at`**. Read through
    #: `interest.current_weight`, never straight off the column.
    weight = models.FloatField(default=0.0)

    #: When play last said so. `auto_now` per §17.2, which means it moves on every
    #: save — so a row is only ever saved when it was genuinely reinforced.
    last_reinforced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-weight"]
        constraints = [
            models.UniqueConstraint(fields=["child", "topic"], name="uniq_child_topic"),
        ]
        indexes = [models.Index(fields=["child", "-weight"], name="interest_child_weight")]

    def __str__(self) -> str:
        return f"{self.topic} ({self.weight:.2f}) for {self.child_id}"


class WorldPlace(UUIDModel):
    """Somewhere in this child's world (§8.3, §17.2 `world_place`).

    §8.3 asks for "a persistent, growing map of places, each with mood,
    inhabitants, and history", and the sentence that follows is the requirement
    this table exists to meet: *today's forest is yesterday's forest*. A place the
    child visited last week has to come back as itself — same name, same mood, the
    same creatures living in it — or the world is a backdrop that gets repainted
    every night, which is exactly the "generic content produces generic attention"
    failure of §2.4.

    It is the same argument `companion.Character` makes about a frog, applied one
    level up, and it is deliberately the same shape: a row that is written once and
    read back, never regenerated. `visit_count` and `last_visited_at` are what let
    the planner choose between comfort (go back to the Hollow Oak) and novelty
    (find somewhere new) on evidence rather than on a coin toss (§8.3).

    Scoped to the child, like every other row in this graph. Two children who both
    have a Hollow Oak have different Hollow Oaks, and one family's map must never
    reach another family's story (§17.5).
    """

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="places",
    )
    name = models.CharField(max_length=120)

    #: The feeling of the place — `dappled_and_green`, `hushed_and_starlit`. Read
    #: by the planner and, through it, by the world palette the child's screen
    #: wears (§15.1): a place's mood is the one thing about it that has to reach
    #: the design layer as well as the prompt.
    mood = models.CharField(max_length=32)
    description = models.TextField()

    #: Who lives here, as plain names: `["the moss frogs", "Bramble"]`. Seeded from
    #: the world registry when a place is discovered and extended as the child meets
    #: people there, so a revisit can be told who should already be waiting.
    inhabitants = models.JSONField(default=list, blank=True)

    # SET_NULL for the reason every FK into `story` is: the place outlives the
    # adventure that found it, which is the entire point of the table (§17.5 prunes
    # story rows long before a child stops remembering where they went).
    first_seen_adventure = models.ForeignKey(
        "story.Adventure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_visited_at = models.DateTimeField(null=True, blank=True)
    visit_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-last_visited_at", "name"]
        constraints = [
            # Not in §17.2's column list, and added anyway — for the same reason
            # `Character` is unique on `(child, key)`. The name is how a place is
            # matched when the planner asks "have we been here before", and two rows
            # answering to "the Hollow Oak" make that question unanswerable at
            # exactly the moment the child is being told they are back somewhere
            # they know.
            models.UniqueConstraint(fields=["child", "name"], name="world_place_child_name_uniq"),
        ]
        indexes = [models.Index(fields=["child", "-last_visited_at"], name="place_child_visited")]

    def __str__(self) -> str:
        return f"{self.name} (visits={self.visit_count})"
