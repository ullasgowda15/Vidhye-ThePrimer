"""Eyeball a whole adventure's worth of illustrations (PRODUCT.md §8.2, Build Day 6 §5).

    python manage.py render_consistency <child_id> --beats 6

Renders a scripted six-beat sequence for one child's companion and prints where
every file landed, so a human can open them in order and answer the two questions
no assertion can:

1. Is that the *same companion* in all six pictures? (§8.2, criterion 1)
2. Is the frog met in beat 2 the same frog in beat 4? (criterion 2)

The sequence is scripted rather than generated because this is a *rendering*
harness. Calling a language model first would make the output depend on what the
model happened to write today, which is the one variable you do not want moving
while judging whether the pictures are consistent — and it would put a beat spend
in front of every look at the art. The recurring character is persisted through
the real `companion.cast` path, so what is being demonstrated is the shipping
mechanism and not a mock of it.

It also prints one full illustration prompt (criterion 3) and the provider
counters (criterion 4). Run it twice: the second run should generate nothing.

Runs synchronously and in-process, like `render_companion` and for the same
reason — needing a Celery worker up in order to look at a picture would make it
useless exactly when the worker is what you are debugging.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.companion import cast
from apps.companion.models import CANONICAL_EXPRESSIONS, Character, Companion
from apps.companion.scenes import canonical_expression
from apps.companion.service import build_assets, ensure_visual_descriptor, render_scene
from apps.media import service as media_service
from apps.media.adapters import image as image_adapters
from apps.media.exceptions import MediaError
from apps.media.storage import media_storage
from apps.profiles.models import Child

#: The recurring character the sequence introduces, described exactly as a beat
#: that introduced one would describe it (`ai.schemas.CastMember`).
BRAMBLE: dict[str, str] = {
    "key": "bramble",
    "name": "Bramble",
    "visual_descriptor": (
        "a small round frog, moss-green with a paler belly, large friendly amber eyes, "
        "and one cream stripe down its back"
    ),
}

#: `(scene, emotion, who is present)` per beat — an ordinary arc, deliberately
#: varied in setting and light so "same character" is answered by looking at
#: genuinely different pictures rather than at six near-duplicates.
#:
#: Bramble enters at beat 2 and returns at beat 4 with two beats in between,
#: because a character that reappears immediately would look consistent even if
#: nothing were persisting it.
SEQUENCE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "standing at the edge of a mossy riverbank at dawn, mist rising off the water, "
        "looking out across the reeds",
        "playful_curious",
        ("pip",),
    ),
    (
        "crouched by a shallow pool among the reeds, meeting a small frog sitting on a "
        "flat wet stone, morning light",
        "happy",
        ("pip", "bramble"),
    ),
    (
        "walking along a narrow path between tall grasses, midday sun overhead, "
        "butterflies drifting past",
        "warm_proud",
        ("pip",),
    ),
    (
        "sitting together on a fallen log beside the river, the same small frog perched "
        "on the bark alongside, afternoon light through leaves",
        "gentle_concern",
        ("pip", "bramble"),
    ),
    (
        "wading through shallow water carrying a bundle of reeds, late golden light, "
        "long shadows on the surface",
        "curious",
        ("pip",),
    ),
    (
        "curled up on the riverbank under the first stars, a small lantern glowing "
        "nearby, the water quiet",
        "sleepy",
        ("pip",),
    ),
)


class Command(BaseCommand):
    help = "Render a full illustrated sequence for one child and report cache behaviour."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("child_id", help="UUID of the child whose companion to render.")
        parser.add_argument(
            "--beats",
            type=int,
            default=6,
            help=f"How many beats of the sequence to render (max {len(SEQUENCE)}).",
        )
        parser.add_argument(
            "--show-prompt",
            action="store_true",
            help="Print the full illustration prompt for every beat, not just the first.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        companion = self._companion(options["child_id"])
        child = companion.child
        beats = max(1, min(options["beats"], len(SEQUENCE)))
        media_service.STATS.reset()

        provider = image_adapters.select_image_provider()
        self.stdout.write(f"Child     : {child.first_name}")
        self.stdout.write(f"Companion : {companion.name} ({companion.archetype})")
        self.stdout.write(f"Provider  : {image_adapters.provider_label(provider)}")
        self.stdout.write(
            f"Anchors   : {'used' if provider.supports_image_reference else 'descriptor-only'} "
            "(the provider's own capability flag, never a constant)"
        )
        self.stdout.write("")

        ensure_visual_descriptor(companion)
        self._prewarm(companion)

        # Introduced through the shipping path, so beat 4 reads back exactly what
        # beat 2 wrote — which is the property being demonstrated.
        cast.remember(child, [BRAMBLE])

        self.stdout.write(self.style.MIGRATE_HEADING(f"{beats} beats"))
        first_prompt = ""
        for index, (scene, emotion, present) in enumerate(SEQUENCE[:beats], start=1):
            references = cast.refs_for(
                child, companion=companion, present=list(present), emotion=emotion
            )
            prompt = self._prompt_for(scene, references)
            first_prompt = first_prompt or prompt

            try:
                media = render_scene(child, scene, references)
            except MediaError as exc:
                raise CommandError(f"Beat {index} could not be rendered: {exc}") from exc

            # The picture that established a new character's look becomes its
            # anchor, exactly as `orchestrator.illustrate_beat` does it.
            cast.record_reference(cast.characters_for(child, list(present)), media)

            face = canonical_expression(emotion) or "-"
            who = ", ".join(ref.name for ref in references)
            self.stdout.write(f"  beat {index}  [{emotion} -> {face}]  cast: {who}")
            self.stdout.write(f"           {media.url}")
            self.stdout.write(f"           {self._local_path(media.url)}")
            if options["show_prompt"]:
                self.stdout.write(self._indented(prompt))
        self.stdout.write("")

        self._report_character(child)
        if not options["show_prompt"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Beat 1's illustration prompt, in full"))
            self.stdout.write(self._indented(first_prompt))
            self.stdout.write("")
        self._report_cache()

        self.stdout.write(
            self.style.SUCCESS(
                "\nOpen the files above in order. Same companion in all of them, and the "
                "same frog in beats 2 and 4, = consistency holds.\n"
                "Re-run this command: `generated` should be 0."
            )
        )

    # -- pieces ------------------------------------------------------------
    def _prewarm(self, companion: Companion) -> None:
        """Make sure the five canonical faces exist before any beat asks for one.

        Pre-warming is what makes a common emotion free (§9.6): the expression
        assets are generated once, here or at companion-create time, and every
        later beat anchors to them instead of paying for a new face. `build_assets`
        is idempotent, so this is a no-op on the second run — which is exactly why
        the counters below are worth reading.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Canonical expressions (§9.6)"))
        build_assets(companion)
        companion.refresh_from_db()
        for label in CANONICAL_EXPRESSIONS:
            have = companion.reference_manifest.get(label)
            mark = self.style.SUCCESS("pre-warmed") if have else self.style.WARNING("MISSING")
            self.stdout.write(f"  {label:<16} {mark}")
        self.stdout.write("")

    def _report_character(self, child: Child) -> None:
        row = Character.objects.filter(child=child, key=BRAMBLE["key"]).first()
        self.stdout.write(self.style.MIGRATE_HEADING("Recurring cast (§8.2)"))
        if row is None:
            self.stdout.write(self.style.WARNING("  Bramble was not persisted."))
            return
        anchor = row.reference_asset_ids[0] if row.reference_asset_ids else "(none yet)"
        self.stdout.write(f"  {row.name} [{row.key}]  first seen: {row.first_seen_adventure_id}")
        self.stdout.write(f"    descriptor : {row.visual_descriptor}")
        self.stdout.write(f"    anchor     : {anchor}")
        self.stdout.write("")

    def _report_cache(self) -> None:
        stats = media_service.STATS
        self.stdout.write(self.style.MIGRATE_HEADING("Provider calls this run"))
        self.stdout.write(f"  generated       : {stats.generated}")
        self.stdout.write(f"  cache hits      : {stats.cache_hits}")
        self.stdout.write(f"  shared children : {stats.reused_across_children}")
        self.stdout.write(f"  hit rate        : {stats.hit_rate():.0%}")
        for media_type in sorted({*stats.generated_by_type, *stats.hits_by_type}):
            generated = stats.generated_by_type.get(media_type, 0)
            hits = stats.hits_by_type.get(media_type, 0)
            self.stdout.write(
                f"    {media_type:<6} generated {generated}, hits {hits} "
                f"({stats.hit_rate(media_type):.0%})"
            )
        for name, count in sorted(stats.by_provider.items()):
            self.stdout.write(f"  {name}: {count}")

    def _prompt_for(self, scene: str, references: Any) -> str:
        from apps.media.prompts import build_illustration_prompt

        return build_illustration_prompt(
            scene,
            references,
            include_references=image_adapters.configured_supports_image_reference(),
        )

    def _indented(self, text: str) -> str:
        return "\n".join(f"    {line}" for line in text.splitlines())

    def _companion(self, child_id: str) -> Companion:
        child = Child.objects.filter(pk=child_id).first()
        if child is None:
            raise CommandError(
                f"No child with id {child_id}. List them with:\n"
                '    python manage.py shell -c "from apps.profiles.models import Child; '
                "print(list(Child.objects.values_list('id', 'first_name')))\""
            )
        companion = Companion.objects.filter(child=child).select_related("child").first()
        if companion is None:
            raise CommandError(
                f"{child.first_name} has no companion yet. Create one in the app, or:\n"
                f"    POST /api/v1/children/{child_id}/companion"
            )
        return companion

    def _local_path(self, url: str) -> str:
        """Where the file is on disk, when the media backend is the local disk."""
        storage = media_storage()
        location = getattr(storage, "location", None)
        if not location:
            return "(object storage)"
        key = url.replace("/media/", "", 1).lstrip("/")
        return str(location) + "/" + key
