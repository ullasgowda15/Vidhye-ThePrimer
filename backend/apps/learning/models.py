"""The invisible curriculum — catalog and skill model (PRODUCT.md §10.2, §10.6, §17.3).

Three tables, and the split between them is the design rather than an accident of
normalisation.

`Milestone` and `Objective` are **catalog**: seeded content, identical for every
child, versioned by migration (§17.5). Nothing a child does writes to them. That
is what makes §10.6's rollup possible — a parent hears "counting to ten", not
`count_to_10 = 0.62` — and what lets the curriculum be edited, extended or
localised without touching anybody's progress.

`LearningProgress` is the **skill model**: one row per child per objective, and
the only thing here that play changes. It is deliberately four numbers and two
timestamps (§10.2). Not because more would be hard, but because a 4-year-old
produces a handful of noisy signals per sitting, an over-parameterised estimate
would fit that noise, and *a parent is going to be shown this on Day 12*. Every
field below has a sentence a parent would accept as an explanation.

**There is no score column anywhere in this file**, and there is no table of
answers. Assessment is behavioural (§10.5): the evidence lives on `story.Choice`
as what the child did, and this is only ever our running belief about what they
can do. A child never sees any of it.
"""

from __future__ import annotations

import datetime

from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel


class Domain(models.TextChoices):
    """The MVP domains (§10.1): one done well, plus opportunistic vocabulary.

    A `TextChoices` rather than a free string because the selector interleaves
    *across* domains and the demand translator dispatches *on* them — so a typo
    would silently produce an objective that can never be picked and can never be
    turned into a story ask. The roadmap's other eight domains are added by
    extending this, one migration at a time.
    """

    NUMERACY = "numeracy", "Numeracy"
    VOCABULARY = "vocabulary", "Vocabulary"
    #: Decoding print — the phonics sequence behind "read this line to me"
    #: (Docs/Build/Reading-Integration.md). The third domain, added the way this
    #: docstring says the others would be: one migration, one `KIND_BY_DOMAIN`
    #: entry, one story rule that renders it.
    #:
    #: It differs from the first two in one way worth knowing before selecting it:
    #: its objectives are only reachable by a child in the older age band, because
    #: the ask is "read this", and a four-year-old cannot. `min_age` on each
    #: objective carries that, so the selector's existing age filter is the gate
    #: and no separate rule is needed.
    LITERACY = "literacy", "Literacy"


#: Mastery at or above this counts as "they can do this" for the purpose of
#: unlocking what comes after it (`Objective.prerequisites`).
#:
#: Deliberately short of certainty. A gate set at 0.9 would mean a child had to
#: be near-perfect at counting to three before the story was ever allowed to show
#: them four things, which is the opposite of a zone of *proximal* development —
#: the next objective is supposed to arrive while the last one is still settling.
PREREQUISITE_MASTERY = 0.6


class Milestone(UUIDModel):
    """A developmental step a parent would recognise (§10.6, §17.3 `milestone`).

    Objectives roll up into these, and this is the only level of the learning
    engine that is ever named to a human. "Counting to ten" is a milestone;
    `count_to_10` is an objective; `0.62` is nobody's business but ours.

    `standard_ref` maps loosely onto widely-used early-learning frameworks. It is
    JSON rather than columns because the mapping is *content* — it grows a
    framework at a time, it differs per locale, and none of it is ever joined on
    (§17.5). It exists now, unused, because retro-fitting a standards mapping onto
    a catalog that has already been seeded into production is a data migration,
    and writing it down while the catalog is five rows is free.
    """

    domain = models.CharField(max_length=32, choices=Domain.choices)
    name = models.CharField(max_length=120)
    description = models.TextField()
    standard_ref = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["domain", "name"]
        constraints = [
            models.UniqueConstraint(fields=["domain", "name"], name="milestone_domain_name_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.domain})"


class Objective(UUIDModel):
    """One thing a story can quietly practise (§17.3 `objective`).

    `code` is the identity, not the primary key, and every other table refers to
    an objective *by code*: `story.Adventure.primary_objective_id`,
    `story.Beat.learning["objective_id"]`, `prerequisites` below. That is a
    deliberate departure from §17.3's `uuid[]` prerequisites, and it buys two
    things. The catalog is seeded per environment, so a UUID written into a beat
    on a developer's machine means nothing in production, while `count_to_5` means
    the same thing everywhere and forever. And a beat is a durable record of what
    a child was practising — it has to stay readable after a catalog row has been
    superseded, which a dangling foreign key would not be.

    `min_age`/`max_age` are inclusive whole years and are a *filter*, never a
    schedule: a child inside the band may still never be offered an objective
    whose prerequisites they have not met.
    """

    domain = models.CharField(max_length=32, choices=Domain.choices)
    code = models.CharField(max_length=64, unique=True)
    description = models.TextField()
    min_age = models.IntegerField()
    max_age = models.IntegerField()
    milestone = models.ForeignKey(Milestone, on_delete=models.PROTECT, related_name="objectives")

    #: Objective codes that must be roughly in hand first (`PREREQUISITE_MASTERY`).
    #: A list rather than a graph table: the early-years curriculum is a handful
    #: of short chains ("three, then five, then ten"), and a join table for that
    #: would be structure with nothing to hold.
    prerequisites = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["domain", "min_age", "code"]

    def __str__(self) -> str:
        return self.code


class LearningProgress(UUIDModel):
    """What we believe one child can do, and when to look again (§10.2, §17.3).

    Four numbers, chosen so each one has a plain-English reading:

    * `mastery` — how hard a version of this objective they can handle unaided,
      on the same 0-1 scale as the difficulty the story is asked for. That shared
      scale is what closes the loop: the selector hands the story a difficulty
      near the estimate, the child's behaviour at that difficulty is the evidence,
      and the evidence moves the estimate (§5.5).
    * `uncertainty` — how much one more observation is allowed to move `mastery`.
      Starts at 1 (we know nothing, so learn fast) and decays with evidence, which
      is the whole of the "Bayesian-flavoured" in §10.2. It is a learning rate
      wearing an honest name, not a posterior variance, and calling it one would
      be a claim the arithmetic underneath does not support.
    * `evidence_count` — how many times a child has actually done this in a story.
      The denominator behind every other number here, and the first thing to look
      at when one of them seems wrong.
    * `due_at` — spaced retrieval (§10.3). The better they know it, the further
      out this goes.

    `last_practiced_at` is separate from `due_at` on purpose: one is history and
    one is intent, and the selector reads them for opposite reasons — recency
    *discourages* re-picking, dueness *encourages* it.
    """

    child = models.ForeignKey(
        "profiles.Child",
        on_delete=models.CASCADE,
        related_name="progress",
    )
    objective = models.ForeignKey(
        Objective,
        on_delete=models.CASCADE,
        related_name="progress",
    )

    mastery = models.FloatField(default=0.0)
    uncertainty = models.FloatField(default=1.0)
    last_practiced_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    evidence_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["child", "objective"]
        constraints = [
            models.UniqueConstraint(fields=["child", "objective"], name="uniq_child_obj"),
        ]
        indexes = [
            # The selector's own query: every candidate for one child, with the
            # due ones findable without a scan.
            models.Index(fields=["child", "due_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.child.first_name}/{self.objective.code} @ {self.mastery:.2f}"

    def is_due(self, *, now: datetime.datetime | None = None) -> bool:
        """Has spaced retrieval come round for this objective?

        An objective that has never been practised is **not** due. "Due" means a
        schedule we set has come round; a brand-new objective has no schedule, and
        its case for being picked is that it sits at the child's edge — which is
        the ZPD term's job, not this one. Conflating the two would make every
        untouched objective permanently overdue and drown out the review it is
        supposed to be balanced against (§10.3).
        """
        if self.due_at is None:
            return False
        return self.due_at <= (now or timezone.now())
