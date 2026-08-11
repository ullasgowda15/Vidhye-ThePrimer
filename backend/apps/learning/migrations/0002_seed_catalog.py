"""Seed the MVP curriculum (PRODUCT.md §10.1, §10.6, §17.5).

Written out in full here rather than imported from application code, for the same
reason `safety.0002_seed_system_identity` is: catalog rows are **content**, and
migration history is the only place in this repo where the content that shipped on
a given day is literally frozen. A revised objective is a new migration, never an
edit to this one — a child's stored beat says `count_to_5`, and what `count_to_5`
meant when they played it has to stay findable.

Two domains and no more (§10.1): numeracy done properly, vocabulary alongside it.
The other eight domains in §10.1's table are roadmap, and adding one is a
migration plus a `demands.KIND_BY_CODE` entry plus the story rule that renders it.

**The chains are the curriculum.** `prerequisites` is what makes this a sequence
rather than a bag: a child cannot be handed `count_to_10` before five is roughly
in hand, whatever the selector's other terms say. That is the one hard gate in
§10.3, and it lives here as data rather than in code because it is the part a
curriculum person would want to change.

`standard_ref` maps loosely onto two widely-used early-years frameworks (§10.6).
Nothing reads it yet. It is written now because retro-fitting a standards mapping
onto a catalog that is already in production is a data migration, and writing it
down while the catalog is seven rows costs nothing.

**Descriptions name the skill and never the scenery.** The first draft of this
file illustrated `name_colours` with "the green frog", and a child whose interests
included frogs was handed vocabulary instead of counting on their very first
adventure — because `engine.interest_fit` compares the child's interests against
this text, and an example creature reads exactly like a topic the objective is
about. The creatures belong to the story; these say only what the child is
learning to do.
"""

from django.db import migrations

MILESTONES: tuple[dict[str, object], ...] = (
    {
        "key": "counting_and_quantity",
        "domain": "numeracy",
        "name": "Counting and quantity",
        "description": (
            "Says how many things there are by counting them, one number per thing, "
            "and knows the last number said is the answer."
        ),
        "standard_ref": {
            "head_start_elof": "P-MATH 2 — Counting and Cardinality",
            "ccss_math": "K.CC.B.4",
        },
    },
    {
        "key": "comparing_amounts",
        "domain": "numeracy",
        "name": "Comparing amounts",
        "description": "Looks at two groups of things and says which has more and which has fewer.",
        "standard_ref": {
            "head_start_elof": "P-MATH 5 — Comparison and Relationships",
            "ccss_math": "K.CC.C.6",
        },
    },
    {
        "key": "naming_the_world",
        "domain": "vocabulary",
        "name": "Words for the world",
        "description": (
            "Uses words for the colours, sizes and shapes of everyday things, and "
            "understands them when someone else uses them."
        ),
        "standard_ref": {
            "head_start_elof": "P-LC 2 — Vocabulary",
            "ccss_ela": "L.K.5",
        },
    },
    {
        "key": "naming_feelings",
        "domain": "vocabulary",
        "name": "Words for feelings",
        "description": "Names how someone is feeling, in themselves and in others.",
        "standard_ref": {
            "head_start_elof": "P-ATL 8 / P-SE 4 — Emotional Functioning",
            "ccss_ela": "L.K.5",
        },
    },
)

OBJECTIVES: tuple[dict[str, object], ...] = (
    {
        "code": "count_to_3",
        "domain": "numeracy",
        "milestone": "counting_and_quantity",
        "description": (
            "Count a small group of visible things, up to three, out loud, saying one "
            "number for each one."
        ),
        "min_age": 3,
        "max_age": 5,
        "prerequisites": [],
    },
    {
        "code": "count_to_5",
        "domain": "numeracy",
        "milestone": "counting_and_quantity",
        "description": "Count up to five visible things without losing track partway.",
        "min_age": 3,
        "max_age": 7,
        "prerequisites": ["count_to_3"],
    },
    {
        "code": "count_to_10",
        "domain": "numeracy",
        "milestone": "counting_and_quantity",
        "description": (
            "Count up to ten visible things, keeping one number to each one, and know "
            "the last number said is how many there are."
        ),
        "min_age": 4,
        "max_age": 7,
        "prerequisites": ["count_to_5"],
    },
    {
        "code": "more_fewer",
        "domain": "numeracy",
        "milestone": "comparing_amounts",
        "description": (
            "Look at two groups side by side and say which has more and which has fewer."
        ),
        "min_age": 3,
        "max_age": 7,
        "prerequisites": ["count_to_3"],
    },
    {
        "code": "name_colours",
        "domain": "vocabulary",
        "milestone": "naming_the_world",
        "description": (
            "Point at whatever a named colour describes, and use the colour word back."
        ),
        "min_age": 3,
        "max_age": 6,
        "prerequisites": [],
    },
    {
        "code": "size_words",
        "domain": "vocabulary",
        "milestone": "naming_the_world",
        "description": (
            "Understand and use words for size and scale — tiny, huge, enormous, "
            "narrow, deep."
        ),
        "min_age": 4,
        "max_age": 7,
        "prerequisites": [],
    },
    {
        "code": "feeling_words",
        "domain": "vocabulary",
        "milestone": "naming_feelings",
        "description": (
            "Name how a character is feeling — delighted, worried, proud, curious."
        ),
        "min_age": 4,
        "max_age": 7,
        "prerequisites": [],
    },
)


def seed(apps, schema_editor):
    Milestone = apps.get_model("learning", "Milestone")
    Objective = apps.get_model("learning", "Objective")

    by_key = {}
    for row in MILESTONES:
        milestone, _ = Milestone.objects.update_or_create(
            domain=row["domain"],
            name=row["name"],
            defaults={
                "description": row["description"],
                "standard_ref": row["standard_ref"],
            },
        )
        by_key[row["key"]] = milestone

    for row in OBJECTIVES:
        Objective.objects.update_or_create(
            code=row["code"],
            defaults={
                "domain": row["domain"],
                "description": row["description"],
                "min_age": row["min_age"],
                "max_age": row["max_age"],
                "milestone": by_key[row["milestone"]],
                "prerequisites": row["prerequisites"],
            },
        )


def unseed(apps, schema_editor):
    """Remove the catalog, objectives first.

    `Objective.milestone` is PROTECT, so the order is load-bearing rather than
    tidy. A child's `LearningProgress` cascades from the objective — which is
    correct for a reversal of *this* migration, because a progress row against an
    objective that no longer exists is a number with nothing to mean.
    """
    Milestone = apps.get_model("learning", "Milestone")
    Objective = apps.get_model("learning", "Objective")

    Objective.objects.filter(code__in=[row["code"] for row in OBJECTIVES]).delete()
    for row in MILESTONES:
        Milestone.objects.filter(domain=row["domain"], name=row["name"]).delete()


class Migration(migrations.Migration):
    dependencies = [("learning", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
