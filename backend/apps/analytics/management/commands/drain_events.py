"""Drain the event log to the warehouse export, and age out what it holds.

    python manage.py drain_events [--out DIR] [--batch N] [--prune] [--dry-run]

§5.6 says events flow from every service *to a warehouse*. There is no warehouse
at alpha, and standing one up is not a Day-14 problem — so the export is the
thing a warehouse loads: newline-delimited JSON, one event per line, in files
named for the moment they were written. `COPY`, `LOAD DATA`, `bq load` and every
Spark reader take that shape unchanged, so the day a warehouse exists this
command's output is already its input and nothing about the write end has to
move.

**`exported_at` is a column, not a cursor.** A cursor that jumps a gap loses
events silently and cannot tell you it did; a null column cannot. A row is marked
only after the file it is in has been flushed and closed, so a crash mid-drain
re-exports rows rather than losing them — duplicates in the warehouse are a
`DISTINCT` away, and a missing week is not recoverable at all.

**`--prune` is §13.8, and it deletes only what has been exported.** Raw child
behavioural events get short retention; the funnel events that are facts about an
*account* (`parent.signed_up`, `parent.consent_recorded`) do not, because they are
not child data and because activation for a cohort has to stay computable after
that cohort's raw play has aged out. What survives a prune is the export file.
It is deliberately not automatic: nothing in this repo deletes a family's data on
a timer that a person did not start.

**What the export holds, and the one thing it does not reach.** Every row is a
name, a timestamp, three opaque UUIDs and a `props` object that `events._clean`
has already reduced to scalars. No first name, no birthdate, no narration, no
word a child said. That matters because of the gap: a `DELETE /account` cascades
through Postgres and cannot reach a file that was written last week. The rows
left behind carry an id that no longer resolves to anything and nothing else, so
they are de-identified rather than retained — but "de-identified" is a weaker
promise than "gone", and it is the honest word for it. Closing it properly is a
job for whoever stands up a real warehouse: a delete has to become a message that
reaches it, and there is nothing to send one to yet.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.analytics.models import Event

#: How many rows to hold in memory at once. Small enough that a month of a busy
#: alpha does not become one long transaction, large enough that the marking is
#: not a query per row.
DEFAULT_BATCH = 5000


class Command(BaseCommand):
    help = "Export new events to NDJSON for the warehouse, and optionally prune old ones."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--out",
            default="",
            help="Directory for the export files (default: settings.EVENT_EXPORT_DIR).",
        )
        parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "After exporting, delete exported child-behavioural events older than "
                "settings.EVENT_RETENTION_DAYS (§13.8). Account-level funnel events are "
                "never pruned."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be exported and pruned, and write nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        out = Path(options["out"] or settings.EVENT_EXPORT_DIR)
        dry_run = options["dry_run"]

        pending = Event.objects.filter(exported_at__isnull=True).order_by("ts")
        count = pending.count()

        if dry_run:
            self.stdout.write(f"would export {count} event(s) to {out}")
        elif count:
            path = self._export(pending, out=out, batch=options["batch"])
            self.stdout.write(f"exported {count} event(s) to {path}")
        else:
            self.stdout.write("nothing to export")

        if options["prune"]:
            self._prune(dry_run=dry_run)

    # -- export --------------------------------------------------------------

    def _export(self, pending: Any, *, out: Path, batch: int) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S")
        path = out / f"events-{stamp}.ndjson"

        marked: list[Any] = []
        with path.open("w", encoding="utf-8") as handle:
            # `iterator` so a large backlog streams rather than materialising. The
            # ids are collected as we go and marked *after* the file is closed —
            # see the module docstring on why the order matters.
            for event in pending.iterator(chunk_size=batch):
                handle.write(json.dumps(_row(event), ensure_ascii=False))
                handle.write("\n")
                marked.append(event.pk)

        now = timezone.now()
        for start in range(0, len(marked), batch):
            Event.objects.filter(pk__in=marked[start : start + batch]).update(exported_at=now)
        return path

    # -- retention (§13.8) ---------------------------------------------------

    def _prune(self, *, dry_run: bool) -> None:
        cutoff = timezone.now() - datetime.timedelta(days=settings.EVENT_RETENTION_DAYS)
        stale = Event.objects.filter(
            ts__lt=cutoff,
            # Never a row that has not reached the export. A prune that can delete
            # an un-exported event is a prune that can lose a week nobody knows is
            # missing.
            exported_at__isnull=False,
            # "Raw child behavioural events" (§13.8) — the ones with a child on
            # them. An account-level funnel event is not child data and stays.
            child_id__isnull=False,
        )
        count = stale.count()

        if dry_run:
            self.stdout.write(
                f"would prune {count} exported child event(s) older than "
                f"{settings.EVENT_RETENTION_DAYS} days"
            )
            return

        deleted, _ = stale.delete()
        self.stdout.write(
            f"pruned {deleted} exported child event(s) older than "
            f"{settings.EVENT_RETENTION_DAYS} days"
        )


def _row(event: Event) -> dict[str, Any]:
    """One event as the warehouse will read it.

    Flat, with the ids as bare strings under `*_key` names: a warehouse row must
    not depend on a join back into a database that will have deleted the child by
    the time anyone queries it. The key is what lets the warehouse group by child
    and by sitting; it is not a handle on anybody.
    """
    return {
        "id": str(event.pk),
        "name": event.name,
        "ts": event.ts.isoformat(),
        "child_key": str(event.child_id) if event.child_id else None,
        "parent_key": str(event.parent_id) if event.parent_id else None,
        "session_key": str(event.session_key) if event.session_key else None,
        "props": event.props,
    }
