"""Recompute stored sitting durations that were measured by wall clock.

    python manage.py repair_session_durations [--apply] [--min-minutes N]

Until `apps.story.duration` landed, a sitting's `duration_s` was
`ended_at - started_at`. That is only the same thing as "how long a child played"
when the row is closed at the moment they stop, and it often is not: an abandoned
sitting is finalised whenever the cap check next runs, so the walking away is
recorded as playing. Production carried one row of 2,931 minutes.

The fix changes what is written from now on and cannot reach what is already
stored, so this recomputes the old rows with the same function the orchestrator
now uses. It is **dry by default** — it prints what it would change and touches
nothing until `--apply`.

Only shrinks, never grows. A row whose recomputed value is larger is left alone
and reported: that would mean the new rule credits more time than the old one
did, which it is not supposed to be able to do, and quietly writing it would hide
a bug in `duration` behind a repair script.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.story import duration
from apps.story.models import PlaySession


class Command(BaseCommand):
    help = "Recompute sitting durations that were measured by wall clock."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the corrections. Without this the command only reports.",
        )
        parser.add_argument(
            "--min-minutes",
            type=int,
            default=30,
            help=(
                "Only examine sittings longer than this. The default skips the "
                "ordinary ones, which cannot be wrong by much."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply: bool = options["apply"]
        floor: int = options["min_minutes"] * 60

        candidates = PlaySession.objects.filter(
            ended_at__isnull=False, duration_s__gt=floor
        ).order_by("-duration_s")

        examined = corrected = skipped = 0
        reclaimed = 0

        for session in candidates:
            examined += 1
            was = session.duration_s or 0
            now = duration.played_seconds(session)

            if now >= was:
                skipped += 1
                self.stdout.write(
                    f"  skip  {session.pk}  {was // 60}min -> {now // 60}min "
                    "(not shorter; left alone)"
                )
                continue

            reclaimed += was - now
            corrected += 1
            self.stdout.write(f"  fix   {session.pk}  {was // 60}min -> {now // 60}min")

            if apply:
                session.duration_s = now
                session.save(update_fields=["duration_s", "updated_at"])

        self.stdout.write("")
        self.stdout.write(
            f"examined {examined} sitting(s) over {options['min_minutes']}min · "
            f"{corrected} to correct · {skipped} left alone · "
            f"{reclaimed // 60}min of wall clock removed"
        )
        if corrected and not apply:
            self.stdout.write(self.style.WARNING("dry run — re-run with --apply to write"))
        elif apply and corrected:
            self.stdout.write(self.style.SUCCESS("written"))
