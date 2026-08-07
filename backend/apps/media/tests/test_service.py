"""The cache-first media pipeline (PRODUCT.md §16.6, §16.7, §13.8).

The claim being tested is the one the day's acceptance criteria name: generating
the same `(style, character, scene)` twice calls the image provider **once**.
Every provider here counts its calls, so the claim is checked rather than
assumed.
"""

import pytest

from apps.media.adapters.tts import ElevenLabsTTSProvider
from apps.media.exceptions import MediaBlocked
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.service import (
    STATS,
    delete_child_media,
    get_or_generate_audio,
    get_or_generate_image,
)
from apps.media.storage import key_for_url

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"
WAV_HEADER = b"RIFF\x00\x00\x00\x00WAVE"


class CountingImageProvider:
    """A provider that records every call it is asked to make.

    Declares no capabilities, like any honest adapter that has not thought about
    them — so `sent_negatives` staying empty is the assertion, not the setup.
    """

    name = "counting"
    supports_image_reference = False
    supports_negative_prompt = False
    supports_seed = False

    def __init__(self, data: bytes = PNG) -> None:
        self.calls: list[str] = []
        self.sent_negatives: list[str] = []
        self.sent_seeds: list[int | None] = []
        self._data = data

    def is_configured(self) -> bool:
        return True

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        self.calls.append(prompt)
        self.sent_negatives.append(negative_prompt)
        self.sent_seeds.append(seed)
        return self._data


class CapableImageProvider(CountingImageProvider):
    """A provider whose API really does have the fields (Together, Imagen)."""

    name = "capable"
    supports_negative_prompt = True
    supports_seed = True


class CountingTTSProvider:
    name = "counting-tts"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def is_configured(self) -> bool:
        return True

    def resolve_voice(self, voice_id: str) -> str:
        """No vendor voices to map to — the semantic key is the voice."""
        return voice_id

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.calls.append((text, voice_id))
        return WAV_HEADER + b"\x00" * 64


class MappingTTSProvider(ElevenLabsTTSProvider):
    """The real semantic-key -> vendor-voice resolution, minus the network call.

    Subclassed rather than faked because the resolution *is* what is under test:
    a hand-written stub would only prove the stub agrees with itself.
    """

    name = "mapping-tts"

    def __init__(self) -> None:
        super().__init__(api_key="key", model_id="model", default_voice="default-voice")
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.calls.append((text, self.resolve_voice(voice_id)))
        return WAV_HEADER + b"\x00" * 64


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    STATS.reset()


def _generate(child, provider, *, scene="scene-1", prompt="a prompt", seed=None):
    return get_or_generate_image(
        child=child,
        style_hash="style-1",
        character_hash="character-1",
        scene_hash=scene,
        prompt=prompt,
        seed=seed,
        provider=provider,
    )


@pytest.mark.django_db
def test_the_same_tuple_calls_the_provider_once(child) -> None:
    provider = CountingImageProvider()

    first = _generate(child, provider)
    second = _generate(child, provider)

    assert len(provider.calls) == 1
    assert first.id == second.id
    assert Media.objects.count() == 1
    assert STATS.generated == 1
    assert STATS.cache_hits == 1


@pytest.mark.django_db
def test_a_different_scene_is_a_different_picture(child) -> None:
    provider = CountingImageProvider()

    _generate(child, provider, scene="scene-1")
    _generate(child, provider, scene="scene-2")

    assert len(provider.calls) == 2
    assert Media.objects.count() == 2


@pytest.mark.django_db
def test_another_child_reuses_the_render_but_gets_their_own_row(child) -> None:
    from apps.profiles.models import Child

    provider = CountingImageProvider()
    _generate(child, provider)

    sibling = Child.objects.create(
        parent=child.parent, first_name="Mira", birthdate=child.birthdate
    )
    reused = _generate(sibling, provider)

    # The expensive half — the provider call — is saved.
    assert len(provider.calls) == 1
    # The cheap half is not shared: deleting one child's data (§17.5) must not
    # reach into another child's storybook.
    assert reused.child_id == sibling.id
    assert Media.objects.count() == 2
    assert STATS.reused_across_children == 1


@pytest.mark.django_db
def test_every_row_records_a_safety_status_and_the_scan_runs_first(child) -> None:
    media = _generate(child, CountingImageProvider())

    assert media.safety_status == SafetyStatus.PASSED
    assert media.is_servable
    assert media.bytes == len(PNG)
    assert media.provider == "counting"


@pytest.mark.django_db
def test_a_blocked_asset_is_never_stored(child, offline_media) -> None:
    # An empty response is the cheapest thing the scan refuses.
    blocked = CountingImageProvider(data=b"")

    with pytest.raises(MediaBlocked):
        _generate(child, blocked)

    assert Media.objects.count() == 0
    # Nothing reached storage either — a blocked asset has no URL to leak.
    assert not any(offline_media.rglob("*.*"))


@pytest.mark.django_db
def test_an_oversize_asset_is_blocked(child, settings) -> None:
    settings.MEDIA_MAX_BYTES = 16
    huge = CountingImageProvider(data=PNG + b"x" * 1024)

    with pytest.raises(MediaBlocked):
        _generate(child, huge)

    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_the_stored_url_points_at_the_written_object(child, offline_media) -> None:
    media = _generate(child, CountingImageProvider())

    assert media.url.startswith("/media/")
    key = media.url.removeprefix("/media/")
    assert (offline_media / key).read_bytes() == PNG


@pytest.mark.django_db
def test_no_child_id_is_in_a_storage_path(child, offline_media) -> None:
    """Objects are content-addressed and child-agnostic (§13.8, §17.5).

    They used to carry the child id, which made per-child deletion a prefix
    delete — right up until the pipeline started reusing one child's render for
    another, at which point one family's storybook pointed inside another
    family's prefix and honouring a deletion request would have broken it.
    """
    media = _generate(child, CountingImageProvider())

    assert str(child.id) not in media.url
    assert media.url.startswith("/media/assets/")


@pytest.mark.django_db
def test_identical_bytes_are_stored_once(child, offline_media) -> None:
    provider = CountingImageProvider()
    _generate(child, provider, scene="scene-1", prompt="one")
    _generate(child, provider, scene="scene-2", prompt="two")

    assert len(provider.calls) == 2
    assert len(list(offline_media.rglob("*.png"))) == 1


@pytest.mark.django_db
def test_negatives_and_seed_reach_only_a_provider_that_declares_them(child) -> None:
    """§15.9's list travels as a parameter or not at all.

    A provider with no `negative_prompt` field gets nothing — folding negations
    into the positive prompt is what painted the companion's name into a render.
    """
    from apps.media.style import NEGATIVE_TERMS

    deaf = CountingImageProvider()
    _generate(child, deaf, seed=4242)

    assert deaf.sent_negatives == [""]
    assert deaf.sent_seeds == [None]
    assert not any(term in deaf.calls[0] for term in NEGATIVE_TERMS)

    capable = CapableImageProvider()
    _generate(child, capable, scene="scene-2", seed=4242)

    assert NEGATIVE_TERMS[0] in capable.sent_negatives[0]
    assert capable.sent_seeds == [4242]
    # Still not smuggled into the positive prompt, even where it is supported.
    assert NEGATIVE_TERMS[0] not in capable.calls[0]


@pytest.mark.django_db
def test_one_row_per_child_per_tuple_whatever_the_race(child) -> None:
    """The uniqueness guarantee behind the concurrency fix (§16.6).

    Two workers can both miss the cache and both arrive at the write. The
    constraint decides, and the loser adopts the winner's row rather than
    raising into a story.
    """
    from django.db import IntegrityError

    _generate(child, CountingImageProvider())

    with pytest.raises(IntegrityError):
        Media.objects.create(
            child=child,
            type=MediaType.IMAGE,
            url="/media/assets/duplicate.png",
            provider="counting",
            style_hash="style-1",
            character_hash="character-1",
            scene_hash="scene-1",
            safety_status=SafetyStatus.PASSED,
        )


@pytest.mark.django_db
def test_a_second_worker_adopts_the_winning_row_instead_of_failing(child) -> None:
    racer = CountingImageProvider()
    first = _generate(child, racer)

    # Exactly the state a losing worker is in: it missed the cache, paid the
    # provider, and is about to write a row that already exists. Simulated by
    # deleting the cache-visible marker the lookup uses, so the second call goes
    # all the way to the write.
    Media.objects.filter(pk=first.pk).update(safety_status=SafetyStatus.FLAGGED)
    second = _generate(child, racer)

    assert second.pk == first.pk
    assert Media.objects.count() == 1
    assert len(racer.calls) == 2  # it did pay; the row is what is protected


@pytest.mark.django_db
def test_deleting_one_child_leaves_another_childs_assets_fetchable(
    child, offline_media
) -> None:
    """The §17.5 promise, and the reason objects stopped carrying a child id."""
    from apps.profiles.models import Child

    provider = CountingImageProvider()
    mine = _generate(child, provider)

    sibling = Child.objects.create(
        parent=child.parent, first_name="Mira", birthdate=child.birthdate
    )
    theirs = _generate(sibling, provider)

    # One object, two rows: the saving that made the cache worth having.
    assert mine.url == theirs.url
    assert len(provider.calls) == 1

    rows, objects = delete_child_media(child)

    assert rows == 1
    assert objects == 0  # the sibling still wants it
    assert not Media.objects.filter(child=child).exists()
    assert Media.objects.filter(pk=theirs.pk).exists()
    # The URL a child's device would fetch still resolves to real bytes.
    assert (offline_media / key_for_url(theirs.url)).read_bytes() == PNG


@pytest.mark.django_db
def test_deleting_the_last_owner_removes_the_object(child, offline_media) -> None:
    """Row deletion and object deletion are one call, or the bucket only grows."""
    media = _generate(child, CountingImageProvider())
    stored = offline_media / key_for_url(media.url)
    assert stored.exists()

    rows, objects = delete_child_media(child)

    assert (rows, objects) == (1, 1)
    assert not stored.exists()
    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_the_same_line_is_synthesised_once(child) -> None:
    provider = CountingTTSProvider()

    first = get_or_generate_audio(
        child=child, text="Hi Aarav!", voice_id="fable", provider=provider
    )
    second = get_or_generate_audio(
        child=child, text="Hi Aarav!", voice_id="fable", provider=provider
    )

    assert len(provider.calls) == 1
    assert first.id == second.id
    assert first.type == MediaType.AUDIO


@pytest.mark.django_db
def test_a_different_voice_is_a_different_recording(child) -> None:
    provider = CountingTTSProvider()

    get_or_generate_audio(child=child, text="Hi Aarav!", voice_id="fable", provider=provider)
    get_or_generate_audio(child=child, text="Hi Aarav!", voice_id="nova", provider=provider)

    assert len(provider.calls) == 2


@pytest.mark.django_db
def test_remapping_a_voice_invalidates_the_recordings_it_changes(child, settings) -> None:
    """The audio cache key carries the *vendor* voice, not just the semantic one.

    Every unmapped key resolves to `ELEVENLABS_VOICE_ID` today, so without this
    the key cannot tell two vendor voices apart and editing the map would leave
    every companion speaking in the voice it was first synthesised with.
    """
    provider = MappingTTSProvider()

    settings.ELEVENLABS_VOICE_MAP = {"warm_low": "voice-a"}
    first = get_or_generate_audio(
        child=child, text="Hi Aarav!", voice_id="warm_low", provider=provider
    )

    settings.ELEVENLABS_VOICE_MAP = {"warm_low": "voice-b"}
    second = get_or_generate_audio(
        child=child, text="Hi Aarav!", voice_id="warm_low", provider=provider
    )

    assert second.id != first.id
    assert [voice for _, voice in provider.calls] == ["voice-a", "voice-b"]
