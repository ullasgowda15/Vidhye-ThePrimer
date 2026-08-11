"""Backfill §11.7's entity graph onto canonical keys.

The Day 8 review found the founder's live data already forked — `companion:ruby`
×4 beside `companion:Ruby` ×1 — because both write paths passed model-authored
refs through untouched. `apps.memory.entities` closes the write side; this closes
the rows that were written before it existed.

**Cheapest it will ever be.** There are ten rows today. Every session from here
adds more, and a forked key is not a cosmetic problem: it splits one thing into
two graph nodes, each carrying half the recurrence that feeds the `repetition`
salience factor, so the favourite thing is the one most quietly demoted.

**`values_list`, not the model manager.** `Memory.content` is an
`EncryptedTextField`, and loading whole instances would decrypt every memory to
edit a column that is not encrypted — pointless work that would also make this
migration fail on any environment whose `MEMORY_ENCRYPTION_KEY` is absent. Reading
and writing `(id, entities)` alone keeps it out of the ciphertext entirely.

Reverse is a no-op rather than an error: the canonical form is a strict
normalisation, so there is nothing to restore the old casing *from*, and refusing
to reverse would block an unrelated rollback for no benefit.
"""

from __future__ import annotations

from django.db import migrations


def normalise_existing(apps, schema_editor):
    # The real module, not a historical copy: this is pure string logic with no
    # model dependency, and a frozen duplicate of it here would be a second
    # definition of "what is an entity key" — the exact problem being fixed.
    from apps.memory.entities import normalise

    Memory = apps.get_model("memory", "Memory")

    updates = []
    for pk, entities in Memory.objects.values_list("id", "entities"):
        canonical = normalise(list(entities or []), context=f"migration:{pk}")
        if canonical != (entities or []):
            updates.append((pk, canonical))

    for pk, canonical in updates:
        Memory.objects.filter(pk=pk).update(entities=canonical)


class Migration(migrations.Migration):
    dependencies = [("memory", "0001_initial")]

    operations = [
        migrations.RunPython(normalise_existing, migrations.RunPython.noop),
    ]
