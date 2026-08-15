"""Seed the phonics curriculum (Docs/Build/Reading-Integration.md §7).

Written out in full here for the reason `0002_seed_catalog` gives: catalog rows
are **content**, and migration history is the only place the content that shipped
on a given day is frozen. A revised objective is a new migration, never an edit
to this one.

**Where this curriculum comes from.** The sequence is UFLI Foundations — the
University of Florida Literacy Institute's free, science-of-reading-aligned scope
and sequence — because Common Core names the skills a child should have but not
the order to teach them in. `standard_ref` carries the Common Core RF code
alongside, as the assessment reference. Both are already attached to every skill
in `apps.reading.engine.content.skills`, and the two must not drift: that module
decides which skill a missed word exercised, and these rows are what the selector
picks from. `apps.reading.tests.test_catalog_matches_engine` asserts they agree.

**Why `min_age` is 5 on every row.** This is the age gate, and it is data rather
than a rule in code. `engine.select_objective` already filters the catalog on
`min_age__lte=age`, so a four-year-old cannot be handed a literacy objective by
any path — including a future one nobody has written yet. §5.3 of the programme
brief says no child under five is ever asked to read; this migration is half of
how that is true, and the surface gate is the other half.

**The chains are the curriculum.** `prerequisites` walks the UFLI order, so a
child meets `sh` before `ch`, and blends only once the short vowels are roughly
in hand. Long-vowel patterns sit behind the digraphs rather than behind the
blends, because they are a different kind of difficulty and not a harder version
of the same one.

**Descriptions name the skill and never the scenery**, per `0002_seed_catalog`'s
note: `engine.interest_fit` compares a child's interests against this text, and a
worked example ("the ship on the shelf") reads exactly like a topic the objective
is about.
"""

from django.db import migrations

MILESTONES: tuple[dict[str, object], ...] = (
    {
        "key": "short_vowel_words",
        "domain": "literacy",
        "name": "Sounding out short words",
        "description": (
            "Reads a short word by saying each sound in order and blending them "
            "together, rather than guessing it from the picture."
        ),
        "standard_ref": {
            "ccss_ela": "RF.K.3a / RF.K.3b",
            "ufli": "Units 1-4 — short vowels",
        },
    },
    {
        "key": "two_letters_one_sound",
        "domain": "literacy",
        "name": "Two letters, one sound",
        "description": (
            "Knows that some pairs of letters make a single new sound, and reads "
            "them as one sound instead of two."
        ),
        "standard_ref": {
            "ccss_ela": "RF.K.3a",
            "ufli": "Units 5-6 — consonant digraphs",
        },
    },
    {
        "key": "blending_consonants",
        "domain": "literacy",
        "name": "Blending consonants",
        "description": (
            "Reads two consonants that run together at the start of a word without "
            "dropping either one."
        ),
        "standard_ref": {
            "ccss_ela": "RF.1.3b",
            "ufli": "Unit 7 — initial blends",
        },
    },
    {
        "key": "long_vowel_patterns",
        "domain": "literacy",
        "name": "Long vowel patterns",
        "description": (
            "Recognises the spelling patterns that make a vowel say its own name, "
            "and reads them differently from a short vowel."
        ),
        "standard_ref": {
            "ccss_ela": "RF.1.3c",
            "ufli": "Units 8-10 — silent e and vowel teams",
        },
    },
)

#: The ten skills of `apps.reading.engine.content.skills`, in `ufli_order`, as
#: catalog rows. `code` is the engine's own skill id and the join between the two
#: — changing one without the other silently detaches a child's progress from the
#: thing that generated it.
OBJECTIVES: tuple[dict[str, object], ...] = (
    {
        "code": "cvc_short_a",
        "milestone": "short_vowel_words",
        "description": "Read a three-sound word with a short a in the middle.",
        "prerequisites": [],
    },
    {
        "code": "cvc_short_i",
        "milestone": "short_vowel_words",
        "description": "Read a three-sound word with a short i in the middle.",
        "prerequisites": ["cvc_short_a"],
    },
    {
        "code": "cvc_short_o",
        "milestone": "short_vowel_words",
        "description": "Read a three-sound word with a short o in the middle.",
        "prerequisites": ["cvc_short_i"],
    },
    {
        "code": "digraph_sh",
        "milestone": "two_letters_one_sound",
        "description": "Read s and h together as one sound, at either end of a word.",
        "prerequisites": ["cvc_short_a"],
    },
    {
        "code": "digraph_ch",
        "milestone": "two_letters_one_sound",
        "description": "Read c and h together as one sound, at either end of a word.",
        "prerequisites": ["digraph_sh"],
    },
    {
        "code": "digraph_th",
        "milestone": "two_letters_one_sound",
        "description": "Read t and h together as one sound, at either end of a word.",
        "prerequisites": ["digraph_ch"],
    },
    {
        "code": "blends_l",
        "milestone": "blending_consonants",
        "description": (
            "Read a word beginning with a consonant and l run together, keeping "
            "both sounds."
        ),
        "prerequisites": ["cvc_short_o"],
    },
    {
        "code": "blends_s",
        "milestone": "blending_consonants",
        "description": (
            "Read a word beginning with s and another consonant run together, "
            "keeping both sounds."
        ),
        "prerequisites": ["blends_l"],
    },
    {
        "code": "vce_long_a",
        "milestone": "long_vowel_patterns",
        "description": (
            "Read a word where a silent e at the end makes the vowel say its name."
        ),
        "prerequisites": ["digraph_th"],
    },
    {
        "code": "vowel_team_ai",
        "milestone": "long_vowel_patterns",
        "description": "Read a and i together as one long vowel sound.",
        "prerequisites": ["vce_long_a"],
    },
)

#: Reading starts at five. See the module docstring — this is the age gate, and
#: it is enforced by `engine.select_objective`'s existing catalog filter rather
#: than by anything new.
MIN_AGE = 5
MAX_AGE = 7


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
                "domain": "literacy",
                "description": row["description"],
                "min_age": MIN_AGE,
                "max_age": MAX_AGE,
                "milestone": by_key[row["milestone"]],
                "prerequisites": row["prerequisites"],
            },
        )


def unseed(apps, schema_editor):
    """Remove the phonics catalog, objectives first (`Objective.milestone` is PROTECT)."""
    Milestone = apps.get_model("learning", "Milestone")
    Objective = apps.get_model("learning", "Objective")

    Objective.objects.filter(code__in=[row["code"] for row in OBJECTIVES]).delete()
    for row in MILESTONES:
        Milestone.objects.filter(domain=row["domain"], name=row["name"]).delete()


class Migration(migrations.Migration):
    dependencies = [("learning", "0003_alter_milestone_domain_alter_objective_domain")]

    operations = [migrations.RunPython(seed, unseed)]
