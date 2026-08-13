"""Read and pull the levers (PRODUCT.md §16.10).

    python manage.py flags                       # what is set, and where it came from
    python manage.py flags set ai.beat_model claude-haiku-4-5-20251001 --note "opus regression"
    python manage.py flags clear ai.beat_model   # back to the setting/default

The admin does the same job with a mouse. This exists because the moment a flag
is most needed is the moment somebody is on a phone with a shell and not a
browser, and because a rollback that can be pasted into a runbook is a rollback
that gets done the same way every time.

Every write goes through `FeatureFlag.full_clean`, so `ai.beat_model` is checked
against the provider that will actually be selected before it is stored. A
rollback lever that will happily accept an unreachable model id is a lever that
breaks the product at the moment it is being used to fix it.
"""

import itertools
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.common import flags
from apps.common.models import FeatureFlag


class Command(BaseCommand):
    help = "Show, set or clear a feature flag (apps.common.flags)."

    def add_arguments(self, parser: CommandParser) -> None:
        sub = parser.add_subparsers(dest="action")

        setter = sub.add_parser("set", help="Set a flag's value.")
        setter.add_argument("name")
        setter.add_argument("value")
        setter.add_argument("--note", default="", help="Why. The next person will want it.")
        setter.add_argument("--by", default="", help="Who is pulling the lever.")

        clearer = sub.add_parser("clear", help="Remove the override; back to settings/default.")
        clearer.add_argument("name")

    def handle(self, *args: Any, **options: Any) -> None:
        action = options.get("action")
        if action == "set":
            self._set(options)
        elif action == "clear":
            self._clear(options["name"])
        self._show()

    def _set(self, options: dict[str, Any]) -> None:
        name, value = options["name"], options["value"]
        if name not in flags.REGISTRY:
            raise CommandError(
                f"'{name}' is not a declared flag. Known flags: "
                f"{', '.join(sorted(flags.REGISTRY))}."
            )

        row = FeatureFlag.objects.filter(name=name).first() or FeatureFlag(name=name)
        row.value = value
        row.note = options["note"] or row.note
        row.set_by = options["by"] or row.set_by or "manage.py"
        try:
            row.full_clean()
        except ValidationError as exc:
            # The message from `_validate_beat_model` is the useful part — it names
            # the provider that will be selected and what shape of id it needs.
            raise CommandError(
                "; ".join(itertools.chain.from_iterable(exc.message_dict.values()))
            ) from exc
        row.save()
        self.stdout.write(self.style.WARNING(f"set {name} = {value!r}"))

    def _clear(self, name: str) -> None:
        deleted, _ = FeatureFlag.objects.filter(name=name).delete()
        flags.invalidate(name)
        self.stdout.write(
            self.style.WARNING(f"cleared {name}") if deleted else f"{name} had no override"
        )

    def _show(self) -> None:
        rows = flags.snapshot()
        width = max(len(row["name"]) for row in rows)
        self.stdout.write("")
        for row in rows:
            line = f"  {row['name']:<{width}}  {row['value'] or '(empty)':<12} [{row['source']}]"
            # Only a database row is somebody's deliberate act; the other two are
            # the product as shipped. Colouring the difference is the whole reason
            # `snapshot` reports a source at all.
            self.stdout.write(self.style.WARNING(line) if row["source"] == "database" else line)
        self.stdout.write("")
        self.stdout.write(f"  A change takes effect within {flags.CACHE_TTL_S}s in every process.")
