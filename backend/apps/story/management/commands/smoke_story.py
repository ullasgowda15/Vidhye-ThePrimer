"""Live smoke test for the story loop (PRODUCT.md §8.1, §16.4, §18.5).

    python manage.py smoke_story --child <uuid> [--keep]

This makes **real calls** — Claude for the plan and the beat, the image provider
for the illustration, the voice provider for the narration — which is why it is
opt-in: absent from CI, absent from the default pytest run, and it needs real
keys. `make test` still makes zero live calls.

It exists because the automated tests use a stand-in model, and a stand-in cannot
answer the two questions this day is actually judged on:

* **Is beat one personalised?** A test can prove a child's seeds reach the prompt.
  Only a real model can show a frog-lover getting a frog. So the seeds and the
  beat are printed side by side, and the judgment is one glance rather than a
  claim.
* **Do the illustration and the voice really overlap?** With offline adapters both
  finish in milliseconds and the difference between concurrent and sequential is
  noise. With real providers it is seconds, and the timing table below is the
  evidence.

It calls exactly what the endpoint calls. The only thing it does differently is
print.
"""

from __future__ import annotations

import time
from typing import Any

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.companion.models import Companion
from apps.media.exceptions import MediaError
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.ai.router import AIError
from apps.story.models import Adventure, Beat

# Matching `smoke_ai` and `smoke_media`: 2 means "you have not set this up yet",
# and Django's own `CommandError` exits 1 for "the story path is broken". Only one
# of those is a real failure, which is why they are different codes.
EXIT_MISCONFIGURED = 2

RULE = "-" * 72


class Command(BaseCommand):
    help = "Generate one real adventure and stream its first beat. Costs money."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--child", required=True, help="Child UUID (see `make superuser`).")
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Leave the adventure in the database. By default it is deleted again.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        child = self._child(options["child"])
        companion = self._companion(child)

        self._section("The child this story is for")
        self.stdout.write(f"  {child.first_name}, aged {child.age} (band {child.age_band})")
        self.stdout.write(f"  loves:  {', '.join(child.interests) or '(nothing recorded)'}")
        self.stdout.write(f"  fears:  {', '.join(child.fears) or '(none recorded)'}")
        self.stdout.write(f"  avoid:  {', '.join(child.avoid_topics) or '(none)'}")
        self.stdout.write(f"  ceiling: {child.intensity_ceiling}/3")
        self.stdout.write(f"  companion: {companion.name}, voice {companion.voice_id}")

        adventure = self._plan(child)
        try:
            beat = self._beat(adventure)
            self._media(beat)
            self._verdict(child, beat)
        finally:
            if not options["keep"]:
                adventure.delete()
                self.stdout.write("\n(adventure deleted; pass --keep to inspect it in admin)")

    # -- steps --------------------------------------------------------------
    def _plan(self, child: Child) -> Adventure:
        self._section("1. The plan (Claude)")
        started = time.monotonic()
        adventure = orchestrator.create_adventure(child)
        elapsed = time.monotonic() - started

        if adventure.plan.get("source") == "fallback":
            adventure.delete()
            raise CommandError(
                "Plan generation fell back to the built-in skeleton, which means the "
                "provider call failed. Check AI_PROVIDER_ORDER and your key — the "
                "logged exception above says which."
            )

        self.stdout.write(f'  "{adventure.title}"  ({elapsed:.1f}s)')
        for index, stage in enumerate(adventure.plan.get("skeleton") or [], start=1):
            self.stdout.write(f"    {index}. {stage}")
        self.stdout.write(f"  branches: {', '.join(adventure.plan.get('branch_themes') or [])}")
        self.stdout.write(f"  estimatedBeats: {adventure.estimated_beats}")
        return adventure

    def _beat(self, adventure: Adventure) -> Beat:
        self._section("2. Beat one (Claude, schema-validated)")
        started = time.monotonic()
        try:
            beat = orchestrator.create_beat(adventure, seq=orchestrator.FIRST_BEAT_SEQ)
        except AIError as exc:
            raise CommandError(f"Beat generation failed after its repair attempt: {exc}") from exc
        elapsed = time.monotonic() - started

        scene = beat.scene or {}
        interaction = beat.interaction or {}
        self.stdout.write(f"  narration ({elapsed:.1f}s):")
        self.stdout.write(f"    {beat.narration}")
        self.stdout.write(f"  emotion:     {beat.emotion}")
        self.stdout.write(f"  setting:     {scene.get('setting')}")
        self.stdout.write(
            f"  interaction: {interaction.get('type')} -> {interaction.get('prompt_audio')}"
        )
        self.stdout.write(f"  learning:    {beat.learning}")
        self.stdout.write("  illustration prompt (the picture, not the style):")
        self.stdout.write(f"    {scene.get('illustration_prompt')}")
        return beat

    def _media(self, beat: Beat) -> None:
        self._section("3. Illustration and voice, together")
        started = time.monotonic()
        try:
            results = async_to_sync(orchestrator.attach_media)(beat)
        except MediaError as exc:  # pragma: no cover - `attach_media` swallows these
            raise CommandError(f"Media generation failed: {exc}") from exc
        wall = time.monotonic() - started

        beat.refresh_from_db()
        for label, media in results.items():
            if media is None:
                self.stdout.write(self.style.WARNING(f"  {label:13} FAILED (see the log above)"))
                continue
            self.stdout.write(f"  {label:13} {media.provider:18} {media.bytes:>8} bytes")
            self.stdout.write(f"  {'':13} {media.url}")

        self.stdout.write(f"\n  wall time for both: {wall:.1f}s")
        self.stdout.write(
            "  Compare that against the two per-stage durations in the "
            "`story.media.timing` log lines: concurrency is working when the wall "
            "time is close to the *slower* of the two, not their sum."
        )
        self.stdout.write(f"\n  spoken line: {orchestrator.spoken_line(beat)}")

    def _verdict(self, child: Child, beat: Beat) -> None:
        self._section("Your call")
        loves = ", ".join(child.interests) or "(nothing recorded)"
        self.stdout.write(f"  {child.first_name} loves: {loves}")
        self.stdout.write(f"  The beat is about:   {(beat.scene or {}).get('setting')}")
        self.stdout.write("")
        self.stdout.write("  Is this recognisably a story for *this* child? Open the")
        self.stdout.write("  illustration URL above and listen to the audio. That judgment")
        self.stdout.write("  is the acceptance criterion; nothing here can make it for you.")

    # -- lookups ------------------------------------------------------------
    def _child(self, child_id: str) -> Child:
        child = Child.objects.filter(pk=child_id).first()
        if child is None:
            known = ", ".join(f"{c.first_name} ({c.id})" for c in Child.objects.all()[:10])
            raise CommandError(
                f"No child with id {child_id}. Create one through the parent app.\n"
                f"Children on this database: {known or '(none)'}"
            )
        return child

    def _companion(self, child: Child) -> Companion:
        companion = Companion.objects.filter(child=child).first()
        if companion is None:
            self.stderr.write(
                self.style.ERROR(
                    f"{child.first_name} has no companion. A story needs someone to tell "
                    "it — create one in the parent app first."
                )
            )
            raise SystemExit(EXIT_MISCONFIGURED)
        if not companion.visual_descriptor:
            self.stderr.write(
                self.style.WARNING(
                    f"{companion.name} has no canonical visual descriptor, so the "
                    "illustration will not be on-model (§8.2). Run "
                    f"`make render-companion CHILD={child.id}` first."
                )
            )
        return companion

    def _section(self, title: str) -> None:
        self.stdout.write(f"\n{RULE}\n{title}\n{RULE}")
