"""Prove a restored database actually came back (PRODUCT.md §16.10, §11.10).

    DATABASE_URL=<the scratch copy> python manage.py restore_drill

Run by `infra/backup/restore-drill.sh` against the database it has just restored
into. Never against production: it is read-only, but it exists to answer a
question about a *copy*, and running it against the original would answer the
wrong one.

**Why this is not `SELECT count(*)`.** A restore can succeed and leave a database
nobody can use. The one that matters here is §11.10's: `memory.content` is
encrypted at rest with `MEMORY_ENCRYPTION_KEY`, so a perfectly restored copy read
by a process holding the wrong key is a table of ciphertext — full, correct, and
worthless. That is the exact failure mode a restore drill exists to find, and row
counts cannot see it.

So this asks the four questions a person actually cares about the morning after
losing a database:

  1. is everybody still here?              (children, parents, adventures)
  2. is a child's *history* still here?    (beats and choices, joined to a child)
  3. can we still read their memories?     (decrypt a sample, against the sentinel)
  4. do the pictures still resolve?        (media rows with URLs)

Each is reported with its number, and any that is empty *where the source was
not* is a failure. `--allow-empty` exists for the one honest case: a drill on a
brand-new deployment that genuinely has no families yet.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

#: How many memories to decrypt. A handful, not the table: this runs against a
#: restored copy of production and the question is "does the key work", which one
#: row answers and a full scan answers no better at a hundred times the cost.
SAMPLE = 25


class Command(BaseCommand):
    help = "Verify a restored database: rows are back, and encrypted memory still decrypts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--allow-empty",
            action="store_true",
            help="Pass on a database with no families yet (a brand-new deployment).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.contrib.auth import get_user_model

        from apps.analytics.models import Event
        from apps.companion.models import Companion
        from apps.media.models import Media
        from apps.memory.models import Memory
        from apps.profiles.models import Child
        from apps.story.models import Adventure, Beat, Choice

        database = settings.DATABASES["default"]
        self.stdout.write(f"Verifying {database.get('NAME')} on {database.get('HOST') or 'local'}")
        self.stdout.write("")

        counts = {
            "parents": get_user_model().objects.count(),
            "children": Child.objects.count(),
            "companions": Companion.objects.count(),
            "adventures": Adventure.objects.count(),
            "beats": Beat.objects.count(),
            "choices": Choice.objects.count(),
            "memories": Memory.objects.count(),
            "media": Media.objects.count(),
            "events": Event.objects.count(),
        }
        width = max(len(name) for name in counts)
        for name, count in counts.items():
            self.stdout.write(f"  {name:<{width}}  {count}")
        self.stdout.write("")

        problems: list[str] = []

        if not counts["children"]:
            message = "no children in the restored copy"
            if options["allow_empty"]:
                self.stdout.write(self.style.WARNING(f"  SKIP  {message} (--allow-empty)"))
                self.stdout.write(self.style.SUCCESS("\nRestore drill: nothing to verify."))
                return
            problems.append(message)

        # 2. A child's history, joined rather than counted. A restore that brought
        #    back the beats and lost the foreign keys has the same row count and
        #    none of the value: the story is what a child comes back for.
        orphans = Beat.objects.filter(adventure__isnull=True).count()
        if orphans:
            problems.append(f"{orphans} beats are not attached to an adventure")

        with_history = Child.objects.filter(adventures__beats__isnull=False).distinct().count()
        self.stdout.write(f"  children with a playable history: {with_history}")
        if counts["beats"] and not with_history:
            problems.append("beats exist but no child can be joined to one")

        # 3. **The one that matters.** Decrypt some memories and check they are
        #    readable. A copy read with the wrong MEMORY_ENCRYPTION_KEY restores
        #    perfectly and reads as ciphertext — full, correct, and worthless
        #    (§11.10).
        #
        #    Checked against `crypto.UNREADABLE` rather than by inspecting the
        #    string, and the difference is not cosmetic: `decrypt` does not raise
        #    on a wrong key, it *returns that sentinel*, deliberately, so the admin
        #    page for a child's memories does not 500. The first version of this
        #    command looked for "does it contain a space", and the sentinel
        #    contains four — so the drill passed with a deliberately wrong key,
        #    which is the one thing it exists to catch. Asking the module that
        #    owns the failure how it reports it is the only version of this check
        #    that cannot be fooled by the shape of the answer.
        if counts["memories"]:
            from apps.common.crypto import UNREADABLE

            sample = list(Memory.objects.order_by("-created_at")[:SAMPLE])
            unreadable = [row for row in sample if str(row.content) == UNREADABLE]
            readable = len(sample) - len(unreadable)

            if unreadable:
                problems.append(
                    f"{len(unreadable)} of {len(sample)} sampled memories are undecryptable. "
                    f"The restore is fine and MEMORY_ENCRYPTION_KEY is wrong — a child's "
                    f"history is present and unreadable, which is §11.10's worst case."
                )
            if not readable:
                problems.append("not one sampled memory could be read back")
            else:
                # The content is deliberately never printed. It is a sentence
                # about a real child, and a drill's output ends up in a scrollback
                # buffer and a CI log (§13.8).
                self.stdout.write(
                    f"  memories decrypted: {readable}/{len(sample)} sampled, readable"
                )
        else:
            self.stdout.write("  no memories yet (a young deployment); nothing to decrypt")

        # 4. Pictures and voice. Rows without URLs are rows pointing at nothing.
        if counts["media"]:
            broken = Media.objects.filter(url="").count()
            if broken:
                problems.append(f"{broken} media rows have no URL")
            self.stdout.write(f"  media rows with a URL: {counts['media'] - broken}")

        self.stdout.write("")
        if problems:
            for problem in problems:
                self.stdout.write(self.style.ERROR(f"  FAIL  {problem}"))
            # A non-zero exit, so the shell script's `exit $STATUS` fails the drill
            # rather than printing red text and returning success.
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Restore drill: the data came back."))
