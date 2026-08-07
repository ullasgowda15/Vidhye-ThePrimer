"""The cache-first media pipeline (PRODUCT.md §16.6, §16.7, §13.8).

The claim being tested is the one the day's acceptance criteria name: generating
the same `(style, character, scene)` twice calls the image provider **once**.
Every provider here counts its calls, so the claim is checked rather than
assumed.
"""

import pytest

from apps.media.exceptions import MediaBlocked
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.service import STATS, get_or_generate_audio, get_or_generate_image

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"
WAV_HEADER = b"RIFF\x00\x00\x00\x00WAVE"


class CountingImageProvider:
    """A provider that records every call it is asked to make."""

    name = "counting"

    def __init__(self, data: bytes = PNG) -> None:
        self.calls: list[str] = []
        self._data = data

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, *, seed: int | None = None) -> bytes:
        self.calls.append(prompt)
        return self._data


class CountingTTSProvider:
    name = "counting-tts"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def is_configured(self) -> bool:
        return True

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.calls.append((text, voice_id))
        return WAV_HEADER + b"\x00" * 64


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    STATS.reset()


def _generate(child, provider, *, scene="scene-1", prompt="a prompt"):
    return get_or_generate_image(
        child=child,
        style_hash="style-1",
        character_hash="character-1",
        scene_hash=scene,
        prompt=prompt,
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
    # Content-addressed: the child's id is in the path, so "delete everything
    # about this child" is a prefix delete.
    assert str(child.id) in media.url


@pytest.mark.django_db
def test_identical_bytes_are_stored_once(child, offline_media) -> None:
    provider = CountingImageProvider()
    _generate(child, provider, scene="scene-1", prompt="one")
    _generate(child, provider, scene="scene-2", prompt="two")

    assert len(provider.calls) == 2
    assert len(list(offline_media.rglob("*.png"))) == 1


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
