"""Prompt version management and rollback (PRODUCT.md §19.2, §16.10, §19.7).

    python manage.py prompts                                  # what is live
    python manage.py prompts publish system_identity 1.1.0 --file new.txt --by priya
    python manage.py prompts rollback system_identity 1.0.0   # and back, in one command

A system prompt is code that ships to a child, so it is versioned in a table
rather than in a string constant (`safety.models.PromptVersion`, Day 1) and every
version we have ever run is still there to go back to. What has been missing is
the *going back*: a partial unique index guarantees at most one active row per
layer, which means a rollback is two writes that must not be separately visible,
which means it needs to be one command rather than two admin clicks.

**A rollback publishes nothing new.** It selects a row that already exists and
has already been reviewed — that is the difference between a rollback and an
edit, and why `publish` is the only path that can introduce text. There is no way
in this command to change the template of a version that has shipped.
"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.safety.models import PromptVersion


class Command(BaseCommand):
    help = "List, publish or roll back a system prompt version."

    def add_arguments(self, parser: CommandParser) -> None:
        sub = parser.add_subparsers(dest="action")

        publish = sub.add_parser("publish", help="Add a new version and make it live.")
        publish.add_argument("name")
        publish.add_argument("version")
        publish.add_argument("--file", required=True, help="Path to the template text.")
        publish.add_argument("--by", default="", help="Who published it.")
        publish.add_argument(
            "--stage",
            action="store_true",
            help="Store it without making it live (§19.7's 'ship behind a flag').",
        )

        rollback = sub.add_parser("rollback", help="Make an existing version live again.")
        rollback.add_argument("name")
        rollback.add_argument("version")

    def handle(self, *args: Any, **options: Any) -> None:
        action = options.get("action")
        if action == "publish":
            self._publish(options)
        elif action == "rollback":
            self._activate(options["name"], options["version"], verb="rolled back to")
        self._show()

    def _publish(self, options: dict[str, Any]) -> None:
        name, version = options["name"], options["version"]
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"No such template file: {path}")

        template = path.read_text(encoding="utf-8").strip()
        if not template:
            raise CommandError(f"{path} is empty. A prompt layer with no text is not a layer.")

        if PromptVersion.objects.filter(name=name, version=version).exists():
            raise CommandError(
                f"{name} v{version} already exists. A published version is a record of "
                f"what a child was actually told, so it is never edited — publish a new "
                f"version number instead."
            )

        row = PromptVersion.objects.create(
            name=name,
            version=version,
            template=template,
            created_by=options["by"] or "manage.py",
            active=False,
        )
        self.stdout.write(
            self.style.WARNING(f"published {name} v{version} ({len(template)} chars)")
        )
        if not options["stage"]:
            self._activate(name, version, verb="activated")
        else:
            self.stdout.write(
                f"  staged, not live. Activate with: prompts rollback {name} {row.version}"
            )

    def _activate(self, name: str, version: str, *, verb: str) -> None:
        target = PromptVersion.objects.filter(name=name, version=version).first()
        if target is None:
            known = ", ".join(
                PromptVersion.objects.filter(name=name).values_list("version", flat=True)
            )
            raise CommandError(
                f"No {name} v{version}. Known versions of '{name}': {known or '(none)'}."
            )

        # One transaction, and the deactivate must land first: the partial unique
        # index (`uniq_active_prompt_per_name`) means two active rows is not a
        # state the database will hold even for an instant, so doing this the
        # other way round fails rather than racing. Wrapping both is what makes
        # the layer go from one reviewed template to another with no moment in
        # between where `active_template` raises `PromptVersionUnavailable` and a
        # child's beat has no identity layer attached to it.
        with transaction.atomic():
            PromptVersion.objects.filter(name=name, active=True).exclude(pk=target.pk).update(
                active=False
            )
            PromptVersion.objects.filter(pk=target.pk).update(active=True)

        self.stdout.write(self.style.WARNING(f"{verb} {name} v{version}"))

    def _show(self) -> None:
        rows = list(PromptVersion.objects.order_by("name", "-created_at"))
        if not rows:
            self.stdout.write("No prompt versions. Run migrations — the seed installs the first.")
            return

        self.stdout.write("")
        for row in rows:
            live = "LIVE" if row.active else "    "
            line = f"  {row.name:<20} v{row.version:<10} {live}  {row.created_by}"
            self.stdout.write(self.style.WARNING(line) if row.active else line)
        self.stdout.write("")
        # Plain ASCII: this prints to a terminal, and the Windows console mangles
        # anything that is not (same reason `router.select_provider` says so).
        self.stdout.write("  A change is live on the next generation: no restart, no deploy.")
