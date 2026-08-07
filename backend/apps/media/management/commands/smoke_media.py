"""Live smoke test for the media path (PRODUCT.md §16.6, §8.2, §13.8).

    python manage.py smoke_media [--fresh] [--child <uuid>] [--scenes N]

This makes **real calls to Cloudflare Workers AI and ElevenLabs**, which is why
it is opt-in: it is absent from CI and from the default pytest run, and it needs
real keys. `make test` still makes zero live calls.

It exists because the Day-3 unit tests prove the *pipeline* — hashing, the
safety seam, storage, the cache — against a deterministic offline provider, and
that is exactly the shape of test that cannot notice the two things most likely
to be wrong with a real vendor: that the bytes coming back are a real raster
picture rather than the placeholder SVG, and that the voice is really being
synthesised. Everything below is the founder's manual check, written down and
asserted.

It calls the same service and adapters the product calls. The only things it
does differently are counting provider calls and writing a contact sheet, so the
one judgment a script cannot make — *is this recognisably the same character?* —
is one click rather than a hunt through a media directory.

**Credit-frugal by default.** A companion and its reference set are generated
once and reused on every later run; the steady-state cost of a run is a single
image (the cache probe, which has to miss to prove anything). `--fresh` throws
the companion away and pays for the whole set again.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.companion.models import CANONICAL_EXPRESSIONS, GREETING_AUDIO, PORTRAIT, Companion
from apps.companion.scenes import DEMO_SCENES
from apps.companion.service import (
    build_assets,
    character_ref,
    create_companion,
    greeting_line,
    illustrate,
    illustration_prompt,
)
from apps.media import service as media_service
from apps.media.adapters.tts import ElevenLabsTTSProvider
from apps.media.hashing import hashes_for
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.prompts import REFERENCES_LABEL, character_signature
from apps.media.safety import sniff_content_type
from apps.media.storage import media_storage
from apps.media.style import NEGATIVE_TERMS, style_signature
from apps.profiles.models import Child

# Exit codes, matching `smoke_ai`: 2 means "you have not set this up yet" and 1
# means "the media path is broken". Only one of those is a real failure.
EXIT_MISCONFIGURED = 2
EXIT_CHECK_FAILED = 1

User = get_user_model()

#: Content types that prove a real image model answered. The placeholder adapter
#: emits SVG and nothing else does, so SVG *is* the "this is not real art" signal.
RASTER_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PLACEHOLDER_TYPE = "image/svg+xml"
AUDIO_TYPES = frozenset({"audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4"})

#: Size floors. A 1024x768 storybook illustration is tens of kilobytes; anything
#: under a few KB is an error page, a 1x1, or a truncated download.
MIN_IMAGE_BYTES = 5 * 1024
MIN_AUDIO_BYTES = 2 * 1024

#: The dedicated fixture this harness owns. A stable, obviously-named row that is
#: reused across runs (that reuse is the whole frugality story), in a reserved
#: domain (RFC 2606) that can never be a real address. The account has an
#: unusable password, so it is not a login.
SMOKE_PARENT_EMAIL = "smoke-media@primer.invalid"
SMOKE_CHILD_NAME = "Smokey"

#: The scene the cache probe renders. Deliberately not one of `DEMO_SCENES`:
#: proving the cache means forcing a miss, and forcing a miss means deleting the
#: cached row — which should never throw away art the contact sheet depends on.
PROBE_SCENE = (
    "sitting on a wooden fence at golden hour, looking out over a wide meadow, "
    "long grass moving in the wind"
)
PROBE_LABEL = "cache probe"

MAX_DETAIL_CHARS = 300

# Provider errors are echoed into the summary, and a failing smoke run is exactly
# what gets pasted into an issue. Nothing here is a secret we hold, but account
# identifiers travelling in error bodies are not ours to forward.
_SENSITIVE = re.compile(
    r"""(['"]?(?:account_id|user_id|api_key|apikey|key|token|authorization|xi-api-key)"""
    r"""['"]?\s*[:=]\s*)(['"][^'"]*['"]|\S+)""",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    """Redact identifiers and clip runaway provider errors."""
    redacted = _SENSITIVE.sub(r"\1<redacted>", text)
    if len(redacted) > MAX_DETAIL_CHARS:
        redacted = f"{redacted[:MAX_DETAIL_CHARS]}..."
    return " ".join(redacted.split())


class SmokeFailure(Exception):
    """A check's assertion failed. Carries the reason, not a traceback."""


@dataclass
class CheckResult:
    key: str
    title: str
    passed: bool
    detail: str


@dataclass
class Asset:
    """One thing that ends up on the contact sheet."""

    label: str
    media: Media
    content_type: str = ""
    size: int = 0
    how: str = ""

    @property
    def is_image(self) -> bool:
        return self.media.type == MediaType.IMAGE


@dataclass
class Probe:
    """What the cache probe observed, shared by checks A and C."""

    media: Media | None = None
    first_call_count: int = 0
    second_call_count: int = 0
    reused: Media | None = None
    provider: str = ""


@dataclass
class Run:
    """State the checks read, gathered by the warm-up phase."""

    companion: Companion
    assets: list[Asset] = field(default_factory=list)
    probe: Probe = field(default_factory=Probe)
    greeting_was_live: bool = False
    generated_this_run: int = 0


def probe_asset(url: str) -> tuple[str, int, str]:
    """Fetch a stored asset and report `(content_type, bytes, how)`.

    Handles both deployment shapes rather than assuming one: a CDN or S3 URL is
    fetched over HTTP and judged on its status and header, because that is
    literally what a child's tablet will do; a development URL is relative to
    `MEDIA_URL`, so it is read back through the media storage backend and its
    type is sniffed from the bytes.
    """
    if url.startswith(("http://", "https://")):
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise SmokeFailure(f"{url} could not be fetched: {exc}") from exc
        if response.status_code != 200:
            raise SmokeFailure(f"{url} returned HTTP {response.status_code}, not 200")

        header = response.headers.get("content-type", "").split(";")[0].strip()
        body = response.content
        # Trust the header when it says something specific — it is what the
        # browser will act on — and fall back to the bytes when it does not.
        content_type = header if header and header != "application/octet-stream" else ""
        return content_type or sniff_content_type(body), len(body), "HTTP 200"

    key = url.removeprefix(settings.MEDIA_URL).lstrip("/")
    storage = media_storage()
    if not storage.exists(key):
        raise SmokeFailure(f"{key} is referenced by a Media row but is not in storage")
    with storage.open(key, "rb") as handle:
        body = handle.read()
    return sniff_content_type(body), len(body), "local storage"


def assert_real_image(asset: Asset) -> None:
    """The core assertion of this harness: real art, not the placeholder."""
    if asset.content_type == PLACEHOLDER_TYPE:
        raise SmokeFailure(
            f"{asset.label} is {PLACEHOLDER_TYPE} - that is the offline placeholder "
            "adapter, not a real image model. Set IMAGE_PROVIDER=cloudflare."
        )
    if asset.content_type not in RASTER_TYPES:
        raise SmokeFailure(
            f"{asset.label} is {asset.content_type or 'an unrecognised type'}, "
            f"expected one of {', '.join(sorted(RASTER_TYPES))}"
        )
    if asset.size < MIN_IMAGE_BYTES:
        raise SmokeFailure(
            f"{asset.label} is only {asset.size} bytes - below the {MIN_IMAGE_BYTES} "
            "floor, so it is an error page or a truncated download, not a picture"
        )
    if asset.media.provider == "local-placeholder":
        raise SmokeFailure(f"{asset.label} was produced by the placeholder adapter")


class Command(BaseCommand):
    help = (
        "Live smoke test of the media path: real Cloudflare + ElevenLabs calls, asserted, "
        "plus a contact sheet for the visual check. Costs real (free-tier) credits; "
        "never run in CI."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--fresh",
            action="store_true",
            help=(
                "Delete the smoke companion and regenerate every asset. Costs a full "
                "set of image and audio credits; use when the adapters or the art "
                "direction changed."
            ),
        )
        parser.add_argument(
            "--child",
            default="",
            help=(
                "Run against an existing child's companion instead of the dedicated "
                "smoke fixture. Read-only apart from any assets that are missing."
            ),
        )
        parser.add_argument(
            "--scenes",
            type=int,
            default=3,
            help=f"Story scenes to include on the contact sheet (max {len(DEMO_SCENES)}).",
        )

    # --- entry point -------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        image_model = self.preflight()

        run = self.warm_up(
            fresh=options["fresh"],
            child_id=options["child"],
            scenes=max(0, min(options["scenes"], len(DEMO_SCENES))),
        )

        self.stdout.write("")
        results = [
            self.run_check("A", "real image, not placeholder", lambda: self._check_a(run)),
            self.run_check("B", "real audio from ElevenLabs", lambda: self._check_b(run)),
            self.run_check("C", "cache hit (1 call, then 0)", lambda: self._check_c(run)),
            self.run_check("D", "full reference set", lambda: self._check_d(run)),
            self.run_check("E", "safety scan ran on every row", lambda: self._check_e(run)),
        ]

        sheet = self.write_contact_sheet(run)
        self.print_summary(results, run=run, image_model=image_model, sheet=sheet)

        if not all(result.passed for result in results):
            raise CommandError(
                "Media smoke test FAILED - see the table above.",
                returncode=EXIT_CHECK_FAILED,
            )

    # --- preflight ---------------------------------------------------------

    def preflight(self) -> str:
        """Verify the real adapters are selected and keyed. Exits 2 if not.

        Reports only that a variable is *set* — never any part of its value,
        because a smoke run's output gets pasted into issues and chat.
        """
        if settings.IMAGE_PROVIDER != "cloudflare":
            raise CommandError(
                f"IMAGE_PROVIDER is '{settings.IMAGE_PROVIDER}', so this would not "
                "exercise the real image model. Set IMAGE_PROVIDER=cloudflare in your "
                ".env and re-run.",
                returncode=EXIT_MISCONFIGURED,
            )
        if settings.TTS_PROVIDER != "elevenlabs":
            raise CommandError(
                f"TTS_PROVIDER is '{settings.TTS_PROVIDER}', so this would not exercise "
                "the real voice model. Set TTS_PROVIDER=elevenlabs in your .env and re-run.",
                returncode=EXIT_MISCONFIGURED,
            )

        required = {
            "CLOUDFLARE_ACCOUNT_ID": settings.CLOUDFLARE_ACCOUNT_ID,
            "CLOUDFLARE_API_TOKEN": settings.CLOUDFLARE_API_TOKEN,
            "ELEVENLABS_API_KEY": settings.ELEVENLABS_API_KEY,
            "ELEVENLABS_VOICE_ID": settings.ELEVENLABS_VOICE_ID,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise CommandError(
                f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set, so "
                "the real providers cannot be called.\n"
                "Set them in your .env (see .env.example) and re-run:\n"
                "    make smoke-media",
                returncode=EXIT_MISCONFIGURED,
            )

        model = str(settings.IMAGE_MODEL)
        self.stdout.write(self.style.MIGRATE_HEADING("Media smoke test"))
        self.stdout.write(f"  image  : cloudflare / {model} (credentials are set)")
        self.stdout.write(f"  voice  : elevenlabs / {settings.ELEVENLABS_MODEL_ID} (key is set)")
        self.stdout.write(f"  storage: {settings.MEDIA_STORAGE}")
        return model

    # --- warm-up -----------------------------------------------------------

    def warm_up(self, *, fresh: bool, child_id: str, scenes: int) -> Run:
        """Get a companion with a full asset set, generating only what is missing."""
        companion = self.resolve_companion(fresh=fresh, child_id=child_id)
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Companion: {companion.name} ({companion.archetype}) "
                f"for {companion.child.first_name}"
            )
        )

        media_service.STATS.reset()
        before_audio = Media.objects.filter(child=companion.child, type=MediaType.AUDIO).exists()

        # The real one-time build. Idempotent, so a warm run spends nothing.
        build_assets(companion)
        companion.refresh_from_db()

        run = Run(companion=companion)
        run.greeting_was_live = not before_audio

        for index, scene in enumerate(DEMO_SCENES[:scenes], start=1):
            media = illustrate(companion, scene)
            run.assets.append(Asset(label=f"scene {index}", media=media))

        run.generated_this_run = media_service.STATS.generated
        self.stdout.write(
            f"  reference set + {scenes} scenes ready "
            f"({run.generated_this_run} generated this run, "
            f"{media_service.STATS.cache_hits} from cache)"
        )

        self.run_cache_probe(run)
        return run

    def resolve_companion(self, *, fresh: bool, child_id: str) -> Companion:
        """The companion under test: an existing one, or the smoke fixture."""
        if child_id:
            child = Child.objects.filter(pk=child_id).select_related("parent").first()
            if child is None:
                raise CommandError(f"No child with id {child_id}.", returncode=EXIT_MISCONFIGURED)
            companion = Companion.objects.filter(child=child).select_related("child").first()
            if companion is None:
                raise CommandError(
                    f"{child.first_name} has no companion yet.", returncode=EXIT_MISCONFIGURED
                )
            if fresh:
                raise CommandError(
                    "--fresh would delete a real child's companion and its art. "
                    "Run it against the smoke fixture (drop --child) instead.",
                    returncode=EXIT_MISCONFIGURED,
                )
            return companion

        parent = User.objects.filter(email=SMOKE_PARENT_EMAIL).first()
        if parent is None:
            # No usable password: this is a fixture, never a login.
            parent = User.objects.create_user(
                email=SMOKE_PARENT_EMAIL, password=None, display_name="Media smoke fixture"
            )
        child, _ = Child.objects.get_or_create(
            parent=parent,
            first_name=SMOKE_CHILD_NAME,
            defaults={"birthdate": timezone.localdate().replace(year=timezone.now().year - 5)},
        )

        if fresh:
            # Through the real deletion path (§17.5), so a repeated --fresh does
            # not quietly grow a bucket of art nothing points at any more.
            deleted, objects = media_service.delete_child_media(child)
            Companion.objects.filter(child=child).delete()
            self.stdout.write(
                f"  --fresh: dropped the smoke companion, {deleted} media rows "
                f"and {objects} stored objects"
            )

        companion = Companion.objects.filter(child=child).select_related("child").first()
        if companion is None:
            companion = create_companion(
                child=child,
                archetype="dragon",
                name="Pip",
                palette="ember",
                personality_preset="brave_spark",
            )
        return companion

    def run_cache_probe(self, run: Run) -> None:
        """Force one cache miss, then one hit, and record both.

        A cache that is never missed proves nothing, so this deliberately spends
        exactly one image: it deletes the probe scene's rows — for *every* child,
        because the service reuses another child's render — renders it, and
        renders it again. That single image is also what check A inspects, so the
        two checks share one credit rather than each buying their own.
        """
        _, character_hash, scene_hash = hashes_for(
            style_signature(),
            character_signature([character_ref(run.companion)]),
            PROBE_SCENE,
        )
        media_service.purge_media(
            Media.objects.filter(character_hash=character_hash, scene_hash=scene_hash)
        )

        media_service.STATS.reset()
        first = illustrate(run.companion, PROBE_SCENE)
        first_count = media_service.STATS.generated

        second = illustrate(run.companion, PROBE_SCENE)
        second_count = media_service.STATS.generated

        run.probe = Probe(
            media=first,
            first_call_count=first_count,
            second_call_count=second_count,
            reused=second,
            provider=first.provider,
        )
        run.generated_this_run += first_count
        run.assets.append(Asset(label=PROBE_LABEL, media=first))

    # --- checks ------------------------------------------------------------

    def _check_a(self, run: Run) -> str:
        """A real raster image came back from Cloudflare, not placeholder SVG.

        Plus the regression guard for what Day 3's review caught: the *outgoing
        prompt* must not contain the things we were trying to keep out of the
        picture. A diffusion model reads "no text" as *text*, and it painted the
        companion's name into a render to prove it.
        """
        media = run.probe.media
        if media is None:
            raise SmokeFailure("the cache probe produced no image")

        asset = Asset(label="probe image", media=media)
        asset.content_type, asset.size, asset.how = probe_asset(media.url)
        assert_real_image(asset)

        if media.provider != "cloudflare":
            raise SmokeFailure(
                f"the image was produced by '{media.provider}', not the cloudflare adapter"
            )

        self.assert_clean_prompt(illustration_prompt(run.companion, PROBE_SCENE))
        return (
            f"{asset.content_type} {asset.size // 1024}KB via {asset.how}, "
            f"provider={media.provider}, model={settings.IMAGE_MODEL}, "
            f"prompt clean of {len(NEGATIVE_TERMS)} negative terms"
        )

    @staticmethod
    def assert_clean_prompt(prompt: str) -> None:
        """Nothing in the positive prompt that we asked not to be drawn.

        Structural and deterministic, unlike looking at the picture: the exact
        text that goes to the model is right here, so "the negations leaked back
        in" fails on the next run rather than in a render three weeks later.
        Cloudflare's flux-1-schnell has neither a negative-prompt parameter nor
        image conditioning, so both the negatives and any anchor URL must be
        absent entirely.
        """
        lowered = prompt.lower()
        leaked = [term for term in NEGATIVE_TERMS if term.lower() in lowered]
        if leaked:
            raise SmokeFailure(
                f"the positive prompt contains {len(leaked)} negative term(s) "
                f"({', '.join(leaked[:3])}) - a text-to-image model draws those "
                "tokens instead of avoiding them"
            )

        if f"{REFERENCES_LABEL}:" in prompt:
            raise SmokeFailure(
                f"the prompt carries a {REFERENCES_LABEL} block, but flux-1-schnell "
                "cannot consume a reference image - it would be read as words to draw"
            )
        if settings.MEDIA_URL in prompt:
            raise SmokeFailure(
                f"the prompt contains a storage path ('{settings.MEDIA_URL}...'), "
                "which no image provider can fetch"
            )

    def _check_b(self, run: Run) -> str:
        """The greeting is real audio, in the configured ElevenLabs voice."""
        greeting_id = run.companion.reference_manifest.get(GREETING_AUDIO)
        media = Media.objects.filter(id=greeting_id).first() if greeting_id else None
        if media is None:
            raise SmokeFailure("no greeting audio was produced for this companion")

        asset = Asset(label="greeting", media=media)
        asset.content_type, asset.size, asset.how = probe_asset(media.url)

        if asset.content_type not in AUDIO_TYPES:
            raise SmokeFailure(
                f"the greeting is {asset.content_type or 'an unrecognised type'}, "
                f"expected one of {', '.join(sorted(AUDIO_TYPES))}"
            )
        if asset.size < MIN_AUDIO_BYTES:
            raise SmokeFailure(
                f"the greeting is only {asset.size} bytes - below the {MIN_AUDIO_BYTES} "
                "floor, so nothing was really spoken"
            )
        if media.provider != "elevenlabs":
            raise SmokeFailure(
                f"the greeting was produced by '{media.provider}', not the elevenlabs adapter"
            )

        # Which voice the adapter resolves for this companion is a pure function,
        # so it can be asserted whether or not this run made the call.
        voice = ElevenLabsTTSProvider().resolve_voice(run.companion.voice_id or "")
        if not voice:
            raise SmokeFailure(
                f"voice key '{run.companion.voice_id}' resolves to nothing - "
                "set ELEVENLABS_VOICE_ID"
            )
        source = (
            "ELEVENLABS_VOICE_ID"
            if voice == settings.ELEVENLABS_VOICE_ID
            else "ELEVENLABS_VOICE_MAP"
        )
        freshness = "synthesised this run" if run.greeting_was_live else "cache-served, use --fresh"
        return (
            f"{asset.content_type} {asset.size // 1024}KB via {asset.how}, "
            f"voice={run.companion.voice_id}->{source} ({freshness})"
        )

    def _check_c(self, run: Run) -> str:
        """The same (style, character, scene) is paid for once, ever."""
        probe = run.probe
        if probe.first_call_count != 1:
            raise SmokeFailure(
                f"expected exactly 1 provider call on a cache miss, got "
                f"{probe.first_call_count}"
            )
        if probe.second_call_count != probe.first_call_count:
            raise SmokeFailure(
                f"the second render called the provider again "
                f"({probe.second_call_count - probe.first_call_count} extra calls) - "
                "the cache is not being hit"
            )
        if probe.reused is None or probe.media is None or probe.reused.id != probe.media.id:
            raise SmokeFailure("the second render returned a different Media row")
        return f"miss=1 call, hit=0 calls, same row {str(probe.media.id)[:8]}"

    def _check_d(self, run: Run) -> str:
        """Portrait + five canonical expressions + a greeting, all servable."""
        companion = run.companion
        manifest = companion.reference_manifest
        expected = (PORTRAIT, *CANONICAL_EXPRESSIONS, GREETING_AUDIO)

        missing = [label for label in expected if not manifest.get(label)]
        if missing:
            raise SmokeFailure(f"the reference set is missing: {', '.join(missing)}")
        if companion.assets_status != "ready":
            raise SmokeFailure(f"assets_status is '{companion.assets_status}', expected 'ready'")
        if len(companion.reference_asset_ids) != len(expected):
            raise SmokeFailure(
                f"reference_asset_ids holds {len(companion.reference_asset_ids)} ids, "
                f"expected {len(expected)}"
            )

        for label in expected:
            media = Media.objects.filter(id=manifest[label]).first()
            if media is None:
                raise SmokeFailure(f"{label} points at a Media row that no longer exists")
            asset = Asset(label=label, media=media)
            asset.content_type, asset.size, asset.how = probe_asset(media.url)
            if asset.is_image:
                assert_real_image(asset)
            elif asset.content_type not in AUDIO_TYPES:
                raise SmokeFailure(f"{label} is {asset.content_type}, expected audio")
            # Ahead of the scenes, so the sheet reads portrait, expressions,
            # greeting, then the story scenes.
            run.assets.insert(expected.index(label), asset)

        return (
            f"portrait + {len(CANONICAL_EXPRESSIONS)} expressions + greeting, "
            f"{len(companion.reference_asset_ids)} ids, all servable"
        )

    def _check_e(self, run: Run) -> str:
        """Nothing reached storage without passing through the scan (§13.8)."""
        rows = Media.objects.filter(child=run.companion.child)
        unscanned = [row for row in rows if row.safety_status == SafetyStatus.PENDING]
        if unscanned:
            raise SmokeFailure(
                f"{len(unscanned)} of {rows.count()} media rows are still "
                f"'{SafetyStatus.PENDING}' - the scan did not run before the store"
            )

        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row.safety_status] = by_status.get(row.safety_status, 0) + 1

        referenced = set(run.companion.reference_manifest.values())
        unservable = [row for row in rows if str(row.id) in referenced and not row.is_servable]
        if unservable:
            raise SmokeFailure(
                f"{len(unservable)} referenced assets did not pass the scan, so the "
                "companion is holding art a child must not be shown"
            )
        return ", ".join(f"{count} {status}" for status, count in sorted(by_status.items()))

    # --- contact sheet -----------------------------------------------------

    def write_contact_sheet(self, run: Run) -> Path:
        """The human handoff: every image side by side, plus the greeting.

        Written as one self-contained page so the only manual step left in the
        Day-3 founder test is opening it: drift between scenes is obvious in a
        grid and invisible in a list of file paths.
        """
        directory = Path(settings.MEDIA_ROOT) / "smoke"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "contact-sheet.html"

        images = [asset for asset in run.assets if asset.is_image]
        audio = next((asset for asset in run.assets if not asset.is_image), None)
        companion = run.companion

        cards = "\n".join(self._card(asset) for asset in images)

        if audio:
            player = (
                f'    <audio controls preload="metadata" '
                f'src="{html.escape(self._src(audio.media.url))}"></audio>\n'
                f'    <p class="line">&ldquo;{html.escape(greeting_line(companion))}&rdquo;</p>'
            )
        else:
            player = '    <p class="line">No greeting audio was produced.</p>'

        target.write_text(
            _CONTACT_SHEET.format(
                name=html.escape(companion.name),
                archetype=html.escape(companion.archetype),
                child=html.escape(companion.child.first_name),
                generated=timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
                model=html.escape(str(settings.IMAGE_MODEL)),
                voice=html.escape(str(settings.ELEVENLABS_MODEL_ID)),
                descriptor=html.escape(companion.visual_descriptor),
                count=len(images),
                cards=cards,
                player=player,
            ),
            encoding="utf-8",
        )
        return target

    def _card(self, asset: Asset) -> str:
        """One labelled tile.

        The caption carries the provider on purpose: a `local-placeholder` label
        on a contact sheet is the fastest possible way to notice that a run was
        not testing what it claimed to be testing.
        """
        meta = (
            f"{asset.content_type or '?'} &middot; {asset.size // 1024}KB "
            f"&middot; {html.escape(asset.media.provider)}"
        )
        return (
            "      <figure>\n"
            f'        <img src="{html.escape(self._src(asset.media.url))}" '
            f'alt="{html.escape(asset.label)}" loading="lazy">\n'
            f"        <figcaption>{html.escape(asset.label)}"
            f"<span>{meta}</span></figcaption>\n"
            "      </figure>"
        )

    @staticmethod
    def _src(url: str) -> str:
        """A src the sheet can load from disk as well as from a CDN.

        The sheet lives at `MEDIA_ROOT/smoke/`, so a development URL becomes a
        relative path back out of that directory; an absolute CDN URL is already
        what a browser wants.
        """
        if url.startswith(("http://", "https://")):
            return url
        return "../" + url.removeprefix(settings.MEDIA_URL).lstrip("/")

    # --- plumbing ----------------------------------------------------------

    def run_check(self, key: str, title: str, body: Callable[[], str]) -> CheckResult:
        """Run one check, turning any failure into a reported FAIL.

        Checks are independent on purpose: a broken voice adapter should not hide
        the state of the image path.
        """
        self.stdout.write(f"{key}  {title} ... ", ending="")
        self.stdout.flush()
        try:
            detail = body()
        except SmokeFailure as exc:
            self.stdout.write(self.style.ERROR("FAIL"))
            return CheckResult(key, title, passed=False, detail=scrub(str(exc)))
        except Exception as exc:  # a smoke test reports failures, it never crashes
            self.stdout.write(self.style.ERROR("FAIL"))
            return CheckResult(
                key, title, passed=False, detail=scrub(f"{type(exc).__name__}: {exc}")
            )

        self.stdout.write(self.style.SUCCESS("PASS"))
        return CheckResult(key, title, passed=True, detail=detail)

    def print_summary(
        self,
        results: list[CheckResult],
        *,
        run: Run,
        image_model: str,
        sheet: Path,
    ) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"  image={image_model} voice={settings.ELEVENLABS_MODEL_ID}")
        self.stdout.write(f"  generated this run: {run.generated_this_run}")
        self.stdout.write("")
        for result in results:
            status = self.style.SUCCESS("PASS") if result.passed else self.style.ERROR("FAIL")
            self.stdout.write(f"  {result.key}  {result.title:<32} {status}  {result.detail}")

        self.stdout.write("")
        if all(result.passed for result in results):
            self.stdout.write(self.style.SUCCESS("  OVERALL PASS"))
        else:
            self.stdout.write(self.style.ERROR("  OVERALL FAIL"))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Now the part a script cannot judge"))
        self.stdout.write(f"  open: {sheet.resolve()}")
        self.stdout.write(
            "  1. Is it recognisably the SAME character in every picture? Some drift is\n"
            "     expected on a free text-only model - judge 'good enough for dev'.\n"
            "  2. Play the greeting. Does it sound warm and right for a small child?\n"
            "     If not, change ELEVENLABS_VOICE_ID and re-run with --fresh."
        )
        # The CommandError that follows a failure goes to stderr. Flush first so
        # the table still reads in order when the run is piped to a file.
        self.stdout.flush()


_CONTACT_SHEET = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} - media smoke contact sheet</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    margin: 0; padding: 2rem; background: #fdf6e9; color: #4a3524;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1e2138; color: #e8e4f2; }}
    figure, section {{ background: #2a2e4a !important; }}
  }}
  header {{ max-width: 60rem; margin: 0 auto 1.5rem; }}
  h1 {{ font-size: 1.75rem; margin: 0 0 .25rem; }}
  .meta {{ opacity: .75; font-size: .875rem; margin: 0; }}
  .descriptor {{
    max-width: 60rem; margin: 1rem auto; padding: .75rem 1rem;
    border-left: 3px solid #e8a33d; font-style: italic; opacity: .9;
  }}
  section {{
    max-width: 60rem; margin: 0 auto 1.5rem; padding: 1rem 1.25rem;
    background: #fffdf8; border-radius: 12px;
  }}
  .grid {{
    max-width: 60rem; margin: 0 auto;
    display: grid; gap: 1rem;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  }}
  figure {{ margin: 0; background: #fffdf8; border-radius: 12px; overflow: hidden; }}
  img {{ display: block; width: 100%; height: auto; aspect-ratio: 4/3; object-fit: cover; }}
  figcaption {{ padding: .5rem .75rem; font-size: .875rem; font-weight: 600; }}
  figcaption span {{ display: block; font-weight: 400; opacity: .65; font-size: .75rem; }}
  audio {{ width: 100%; }}
  .line {{ font-size: 1.125rem; margin: .75rem 0 0; }}
</style>
</head>
<body>
<header>
  <h1>{name} &mdash; {archetype}, for {child}</h1>
  <p class="meta">{count} images &middot; {model} &middot; {voice} &middot; {generated}</p>
</header>

<p class="descriptor">{descriptor}</p>

<section>
  <h2 style="font-size:1rem;margin:0 0 .5rem">Listen: does this sound right for a small child?</h2>
{player}
</section>

<div class="grid">
{cards}
</div>
</body>
</html>
"""
