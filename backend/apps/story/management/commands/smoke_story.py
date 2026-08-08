"""Live smoke test for the story loop (PRODUCT.md §8.1, §16.4, §18.5).

    python manage.py smoke_story --child <uuid> [--keep]

This makes **real calls** — Claude for the plan and the beat, the image provider
for the illustration, the voice provider for the narration — which is why it is
opt-in: absent from CI, absent from the default pytest run, and it needs real
keys. `make test` still makes zero live calls.

It exists because the automated tests use a stand-in model, and a stand-in cannot
answer the questions these days are actually judged on:

* **Is beat one personalised?** A test can prove a child's seeds reach the prompt.
  Only a real model can show a frog-lover getting a frog. So the seeds and the
  beat are printed side by side, and the judgment is one glance rather than a
  claim.
* **Do the illustration and the voice really overlap?** With offline adapters both
  finish in milliseconds and the difference between concurrent and sequential is
  noise. With real providers it is seconds, and the timing table below is the
  evidence.
* **Does a choice actually change the story?** (§8.5.) The stand-in echoes the
  chosen label back because it was told to, which proves the plumbing and nothing
  about the writing. So this plays a choice for real and prints the beat before
  and the beat after, next to each other. A consequence that would have read the
  same after the other option is the failure this is looking for, and only a human
  reading two paragraphs can see it.
* **Is the ending warm, and does it invite the child back?** (§8.8.)

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
            consequence = self._consequence(adventure, beat)
            close = self._close(adventure)
            self._verdict(child, beat, consequence, close)
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
        self._options(beat)
        return beat

    def _options(self, beat: Beat) -> None:
        """The illustrated choices, if this beat offers any (§14.4)."""
        if not beat.options:
            self.stdout.write("  choices:     (none — this beat only asks them to carry on)")
            return
        self.stdout.write("  choices:")
        for option in beat.options:
            self.stdout.write(f"    [{option.get('key')}] {option.get('label')}")
            self.stdout.write(f"        picture: {option.get('icon_prompt')}")

    def _consequence(self, adventure: Adventure, beat: Beat) -> Beat | None:
        """Play a choice for real and generate what follows it (§8.5).

        Picks the *last* option rather than the first, on the theory that a model
        writing a generic continuation will drift towards whatever it listed first.
        """
        self._section("4. A choice, and what it changed (Claude)")

        options = beat.options
        chosen = str(options[-1].get("key")) if options else "continue"
        label = str(options[-1].get("label")) if options else "carry on"
        self.stdout.write(f'  the child taps: "{label}"  ({chosen})')

        orchestrator.record_choice(beat, chosen_key=chosen, latency_ms=2400)

        started = time.monotonic()
        try:
            consequence = orchestrator.create_beat(
                adventure, seq=beat.seq + 1, prev_choice=orchestrator.turn_from(beat)
            )
        except AIError as exc:
            raise CommandError(f"The consequence beat failed to generate: {exc}") from exc

        self.stdout.write(f"\n  before ({beat.seq}):")
        self.stdout.write(f"    {beat.narration}")
        self.stdout.write(f"\n  after ({consequence.seq}, {time.monotonic() - started:.1f}s):")
        self.stdout.write(f"    {consequence.narration}")
        self._options(consequence)
        return consequence

    def _close(self, adventure: Adventure) -> Beat | None:
        """The last beat of the sitting, which must always be warm (§8.8)."""
        self._section("5. The warm close (Claude)")

        last = orchestrator.total_beats(adventure)
        if adventure.beats.filter(seq=last).exists():
            self.stdout.write("  (already generated above)")
            return adventure.beats.filter(seq=last).first()

        try:
            close = orchestrator.create_beat(adventure, seq=last)
        except AIError as exc:
            raise CommandError(f"The closing beat failed to generate: {exc}") from exc

        self.stdout.write(f"  {close.narration}")
        self.stdout.write(f"\n  interaction: {(close.interaction or {}).get('type')}")
        self.stdout.write(f"  choices:     {len(close.options)} (must be 0)")
        return close

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

    def _verdict(
        self,
        child: Child,
        beat: Beat,
        consequence: Beat | None,
        close: Beat | None,
    ) -> None:
        self._section("Your call")
        loves = ", ".join(child.interests) or "(nothing recorded)"
        self.stdout.write(f"  {child.first_name} loves: {loves}")
        self.stdout.write(f"  The beat is about:   {(beat.scene or {}).get('setting')}")
        self.stdout.write("")
        self.stdout.write("  Three questions, and nothing here can answer any of them for you:")
        self.stdout.write("")
        self.stdout.write("  1. Is this recognisably a story for *this* child? Open the")
        self.stdout.write("     illustration URL above and listen to the audio.")
        if consequence is not None:
            self.stdout.write("  2. Read beat and consequence again, side by side. Would the")
            self.stdout.write("     'after' have read the same if the child had picked the other")
            self.stdout.write("     option? If yes, the choice was cosmetic and §8.5 is not met.")
        if close is not None:
            self.stdout.write("  3. Does the close leave the child safe, and does it give them a")
            self.stdout.write("     reason to come back tomorrow (§8.8)? It must ask nothing.")
        self.stdout.write("")
        self.stdout.write("  Nowhere in any of the above may a child be told they were wrong.")

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
