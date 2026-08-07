"""Eyeball companion consistency (PRODUCT.md §8.2, Build Day 3 §5).

    python manage.py render_companion <child_id> --scenes 3

Builds the companion's reference set if it is missing, then renders N ordinary
story scenes and prints where every file landed, so a human can open them side
by side and answer the only question that matters: *is that the same character?*

It also prints the provider counters, which is how the cache claim is checked by
hand — run it twice and the second run should report zero generations.

Runs synchronously and in-process on purpose: it is a developer tool, and
needing a Celery worker running to look at a picture would make it useless
exactly when the worker is what you are debugging.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.companion.models import GREETING_AUDIO, Companion
from apps.companion.scenes import DEMO_SCENES, REFERENCE_ORDER
from apps.companion.service import build_assets, ensure_visual_descriptor, illustrate
from apps.media import service as media_service
from apps.media.adapters import image as image_adapters
from apps.media.exceptions import MediaError
from apps.media.models import Media
from apps.media.storage import media_storage
from apps.profiles.models import Child


class Command(BaseCommand):
    help = "Render a companion's reference set plus N story scenes, and report cache behaviour."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("child_id", help="UUID of the child whose companion to render.")
        parser.add_argument(
            "--scenes",
            type=int,
            default=3,
            help=f"How many extra story scenes to render (max {len(DEMO_SCENES)}).",
        )
        parser.add_argument(
            "--skip-references",
            action="store_true",
            help="Only render the story scenes; assume the reference set exists.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        companion = self._companion(options["child_id"])
        scenes = max(0, min(options["scenes"], len(DEMO_SCENES)))
        media_service.STATS.reset()

        provider = image_adapters.select_image_provider()
        self.stdout.write(f"Companion : {companion.name} ({companion.archetype})")
        self.stdout.write(f"Child     : {companion.child.first_name}")
        self.stdout.write(f"Provider  : {image_adapters.provider_label(provider)}")
        self.stdout.write("")

        descriptor = ensure_visual_descriptor(companion)
        self.stdout.write(self.style.MIGRATE_HEADING("Canonical visual descriptor"))
        self.stdout.write(f"  {descriptor}\n")

        if not options["skip_references"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Reference set"))
            build_assets(companion)
            companion.refresh_from_db()
            for label in (*REFERENCE_ORDER, GREETING_AUDIO):
                media_id = companion.reference_manifest.get(label)
                self.stdout.write(f"  {label:<16} {self._describe(media_id)}")
            self.stdout.write("")

        if scenes:
            self.stdout.write(self.style.MIGRATE_HEADING(f"{scenes} story scenes"))
            for index, scene in enumerate(DEMO_SCENES[:scenes], start=1):
                try:
                    media = illustrate(companion, scene)
                except MediaError as exc:
                    raise CommandError(f"Scene {index} could not be rendered: {exc}") from exc
                self.stdout.write(f"  scene {index}: {media.url}")
                self.stdout.write(f"            {self._local_path(media.url)}")
            self.stdout.write("")

        stats = media_service.STATS
        self.stdout.write(self.style.MIGRATE_HEADING("Provider calls this run"))
        self.stdout.write(f"  generated  : {stats.generated}")
        self.stdout.write(f"  cache hits : {stats.cache_hits}")
        for name, count in sorted(stats.by_provider.items()):
            self.stdout.write(f"  {name}: {count}")

        self.stdout.write(
            self.style.SUCCESS(
                "\nOpen the files above side by side. Same creature, different scenes = "
                "consistency holds.\nRe-run this command: `generated` should be 0."
            )
        )

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

    def _describe(self, media_id: str | None) -> str:
        if not media_id:
            return "(missing)"
        media = Media.objects.filter(id=media_id).first()
        if media is None:
            return "(missing)"
        return f"{media.url}  [{media.provider}, {media.safety_status}, {media.bytes} bytes]"

    def _local_path(self, url: str) -> str:
        """Where the file is on disk, when the media backend is the local disk."""
        storage = media_storage()
        location = getattr(storage, "location", None)
        if not location:
            return "(object storage)"
        key = url.replace("/media/", "", 1).lstrip("/")
        return str(location) + "/" + key
