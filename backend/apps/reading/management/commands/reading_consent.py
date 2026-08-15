"""Turn voice keeping on or off for pilot children, in one command.

    manage.py reading_consent --list
    manage.py reading_consent --on  --child <uuid>
    manage.py reading_consent --on  --all --confirm
    manage.py reading_consent --off --all --confirm

**Why this exists rather than a default.** The pilot needs a recording of every
line every child reads — the detector's thresholds were tuned against an adult
imitating a five-year-old and cannot be fixed without the real thing. The obvious
way to get that is to default `reading_audio_consent` to True, and it is the
wrong way twice over: `apps.accounts.privacy` tells parents in the present tense
that *"we keep a recording only if you switch that on for that child"*, and a
default-on switch is the exact shape regulators are looking for when the data is
a child's voice.

A pilot is a small number of families the founder has spoken to. So the honest
version of "capture everything" is: ask them, get a yes, then flip every child at
once — which is this command. The capture rate is identical and the consent is
real.

**It refuses to be run by accident.** `--all` needs `--confirm`, and every run
prints which children changed. Turning it *off* deletes the recordings already
kept, through the same path the parent-facing switch uses, because a withdrawal
that leaves the files behind is a pause wearing a withdrawal's label.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.profiles.models import Child
from apps.reading import service


class Command(BaseCommand):
    help = "Set reading_audio_consent for one child or for all of them."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--child", help="A child's UUID.")
        parser.add_argument("--all", action="store_true", help="Every child.")
        parser.add_argument("--on", action="store_true", help="Keep recordings.")
        parser.add_argument("--off", action="store_true", help="Stop, and delete what is kept.")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required with --all. Changing every family's setting is not a typo-sized act.",
        )
        parser.add_argument("--list", action="store_true", help="Show the current state.")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["list"]:
            self._list()
            return

        if options["on"] == options["off"]:
            raise CommandError("Choose exactly one of --on or --off.")
        turning_on = bool(options["on"])

        if options["child"]:
            children = list(Child.objects.filter(pk=options["child"]))
            if not children:
                raise CommandError(f"No child with id {options['child']}.")
        elif options["all"]:
            if not options["confirm"]:
                raise CommandError(
                    "--all changes every family's setting. Re-run with --confirm once you "
                    "have each parent's yes."
                )
            children = list(Child.objects.all())
        else:
            raise CommandError("Name a --child or pass --all.")

        changed = 0
        for child in children:
            if child.reading_audio_consent == turning_on:
                continue
            child.reading_audio_consent = turning_on
            child.save(update_fields=["reading_audio_consent", "updated_at"])
            changed += 1

            if not turning_on:
                # The same deletion the parent-facing switch performs. A
                # withdrawal that leaves the files is a pause.
                removed = service.delete_corpus_for(child)
                self.stdout.write(f"  {child.first_name}: off, {removed} recording(s) deleted")
            else:
                self.stdout.write(f"  {child.first_name}: on")

        state = "on" if turning_on else "off"
        self.stdout.write(
            self.style.SUCCESS(f"reading_audio_consent {state} for {changed} child(ren)")
        )
        if turning_on and changed:
            self.stdout.write(
                "Recordings are written to READING_CORPUS_DIR on this box and are not "
                "backed up. They go nowhere else."
            )

    def _list(self) -> None:
        rows = Child.objects.select_related("parent").order_by("first_name")
        if not rows:
            self.stdout.write("No children.")
            return
        for child in rows:
            gate = "reads" if not child.pre_reader else "pre-reader"
            consent = "keeping voice" if child.reading_audio_consent else "not keeping voice"
            self.stdout.write(
                f"  {child.pk}  {child.first_name:12} age {child.age}  {gate:11} {consent}"
            )
