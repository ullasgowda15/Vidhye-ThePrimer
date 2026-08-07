"""Adapter selection and the offline providers (PRODUCT.md §16.6, §19.3)."""

import pytest

from apps.media.adapters.image import (
    CloudflareImageProvider,
    GoogleImageProvider,
    LocalPlaceholderImageProvider,
    OpenAIImageProvider,
    TogetherImageProvider,
    configured_supports_image_reference,
    provider_label,
    select_image_provider,
    supports,
)
from apps.media.adapters.tts import (
    ElevenLabsTTSProvider,
    LocalToneTTSProvider,
    OpenAITTSProvider,
    configured_voice,
    select_tts_provider,
)
from apps.media.exceptions import MediaConfigurationError
from apps.media.placeholder import CHARACTER_PART, SCENE_PART, part
from apps.media.prompts import CharacterRef, build_illustration_prompt
from apps.media.safety import sniff_content_type

PIP = CharacterRef("companion:pip", "Pip", "a small amber dragon with one crooked horn")
OTHER = CharacterRef("companion:nim", "Nim", "a tall silver heron with a long beak")


def test_the_configured_provider_is_the_one_selected(settings) -> None:
    settings.IMAGE_PROVIDER = "cloudflare"
    settings.CLOUDFLARE_ACCOUNT_ID = "acct"
    settings.CLOUDFLARE_API_TOKEN = "token"

    assert isinstance(select_image_provider(), CloudflareImageProvider)


def test_the_offline_provider_is_chosen_explicitly(settings) -> None:
    settings.IMAGE_PROVIDER = "local"

    provider = select_image_provider()
    assert isinstance(provider, LocalPlaceholderImageProvider)
    # The row must say what really drew it, so placeholder art is never mistaken
    # for the shipping look.
    assert provider_label(provider) == "local-placeholder"


def test_an_unknown_provider_name_fails_loudly(settings) -> None:
    settings.IMAGE_PROVIDER = "stable-unicorn"

    with pytest.raises(MediaConfigurationError, match="Unknown image provider"):
        select_image_provider()


def test_half_configured_credentials_are_an_error_not_a_silent_downgrade(settings) -> None:
    # Shipping placeholder art because a key was missing is the kind of failure
    # nobody notices until a child is looking at it.
    settings.IMAGE_PROVIDER = "cloudflare"
    settings.CLOUDFLARE_ACCOUNT_ID = "acct"
    settings.CLOUDFLARE_API_TOKEN = ""

    with pytest.raises(MediaConfigurationError, match="no credentials"):
        select_image_provider()


def test_the_shipping_image_provider_claims_no_capability_it_lacks() -> None:
    """flux-1-schnell is text-only, and the code has to say so.

    The Day-3 failure was a `REFERENCES:` block and an `AVOID:` block sent to a
    model that can act on neither. These flags are what stop that from being
    rebuilt: if someone flips one, the prompt changes and the smoke check fails.
    """
    cloudflare = CloudflareImageProvider(account_id="acct", api_token="token")

    assert cloudflare.supports_image_reference is False
    assert cloudflare.supports_negative_prompt is False
    assert cloudflare.supports_seed is False
    # Same for the offline adapter: it must not exercise a branch the real
    # provider never takes.
    assert LocalPlaceholderImageProvider().supports_image_reference is False


def test_an_undeclared_capability_is_no_capability() -> None:
    class Bare:
        name = "bare"

    # Not an ImageProvider, which is exactly the case under test: something that
    # never thought about capabilities must not be sent one.
    assert supports(Bare(), "supports_negative_prompt") is False  # type: ignore[arg-type]
    assert supports(GoogleImageProvider(), "supports_negative_prompt") is True


def test_reference_capability_follows_the_configured_provider(settings) -> None:
    settings.IMAGE_PROVIDER = "cloudflare"
    assert configured_supports_image_reference() is False

    # The image-edit seam is the one a real §8.2 mechanism would land on.
    settings.IMAGE_PROVIDER = "openai"
    assert configured_supports_image_reference() is True

    # Prompt assembly runs before the cache lookup, so a bad name must not raise
    # there — `select_image_provider` reports it, on the miss that needs a key.
    settings.IMAGE_PROVIDER = "stable-unicorn"
    assert configured_supports_image_reference() is False


def test_the_resolved_vendor_voice_is_what_the_cache_key_can_see(settings) -> None:
    settings.TTS_PROVIDER = "elevenlabs"
    settings.ELEVENLABS_API_KEY = "key"
    settings.ELEVENLABS_VOICE_ID = "default-voice"
    settings.ELEVENLABS_VOICE_MAP = {"warm_low": "mapped-voice"}

    assert configured_voice("warm_low") == "mapped-voice"
    assert configured_voice("calm_deep") == "default-voice"

    # Editing the map must be visible to the cache, or every companion keeps
    # speaking in the voice it was first synthesised with.
    settings.ELEVENLABS_VOICE_MAP = {"warm_low": "another-voice"}
    assert configured_voice("warm_low") == "another-voice"


def test_voice_resolution_never_raises_on_the_greeting_path(settings) -> None:
    """A cache *hit* must not need a key, or a config slip silences a companion."""
    settings.TTS_PROVIDER = "not-a-vendor"
    assert configured_voice("warm_low") == "warm_low"

    class SynthesiseOnly:
        name = "double"

    # A caller-injected test double need not implement `resolve_voice`.
    assert configured_voice("warm_low", SynthesiseOnly()) == "warm_low"  # type: ignore[arg-type]


def test_a_voice_key_maps_to_a_vendor_voice(settings) -> None:
    settings.ELEVENLABS_API_KEY = "key"
    settings.ELEVENLABS_VOICE_ID = "default-voice"
    settings.ELEVENLABS_VOICE_MAP = {"warm_low": "mapped-voice"}

    provider = ElevenLabsTTSProvider()
    assert provider.resolve_voice("warm_low") == "mapped-voice"
    # An unmapped key still speaks, in the configured default voice, rather than
    # leaving a child's companion silent.
    assert provider.resolve_voice("calm_deep") == "default-voice"


def test_declared_but_unbuilt_providers_raise_rather_than_vanish() -> None:
    for provider in (TogetherImageProvider(), OpenAIImageProvider(), GoogleImageProvider()):
        assert provider.is_configured() is False
        with pytest.raises(NotImplementedError):
            provider.generate("anything")

    with pytest.raises(NotImplementedError):
        OpenAITTSProvider().synthesize("hello", "warm_low")


def test_the_offline_image_provider_is_deterministic() -> None:
    prompt = build_illustration_prompt("a mossy clearing at dawn", [PIP])
    provider = LocalPlaceholderImageProvider()

    assert provider.generate(prompt) == provider.generate(prompt)
    assert sniff_content_type(provider.generate(prompt)) == "image/svg+xml"


def test_the_offline_provider_keeps_the_character_across_scenes() -> None:
    """The consistency mechanism, made visible offline.

    Same companion, two scenes: the character half of the drawing is identical
    and only the background differs. That is the property §8.2 asks a real image
    model to hold, checked here on the one provider where it is checkable.
    """
    provider = LocalPlaceholderImageProvider()
    dawn = provider.generate(build_illustration_prompt("a mossy clearing at dawn", [PIP]))
    beach = provider.generate(build_illustration_prompt("a windy beach at sunset", [PIP]))
    stranger = provider.generate(build_illustration_prompt("a mossy clearing at dawn", [OTHER]))

    assert dawn != beach  # the scene changed
    assert part(dawn, CHARACTER_PART) == part(beach, CHARACTER_PART)  # the character did not
    assert part(dawn, SCENE_PART) != part(beach, SCENE_PART)
    assert part(dawn, CHARACTER_PART) != part(stranger, CHARACTER_PART)


def test_the_offline_voice_is_deterministic_and_playable() -> None:
    provider = LocalToneTTSProvider()
    audio = provider.synthesize("Hi Aarav!", "warm_low")

    assert audio == provider.synthesize("Hi Aarav!", "warm_low")
    assert audio != provider.synthesize("Hi Aarav!", "calm_deep")
    assert sniff_content_type(audio) == "audio/wav"


def test_a_voice_key_without_a_voice_id_is_not_configured(settings) -> None:
    # ElevenLabs has no default voice of its own, so a key alone would fail at
    # the URL rather than at selection.
    settings.TTS_PROVIDER = "elevenlabs"
    settings.ELEVENLABS_API_KEY = "key"
    settings.ELEVENLABS_VOICE_ID = ""
    settings.ELEVENLABS_VOICE_MAP = {}

    with pytest.raises(MediaConfigurationError, match="no credentials"):
        select_tts_provider()

    settings.ELEVENLABS_VOICE_ID = "voice"
    assert isinstance(select_tts_provider(), ElevenLabsTTSProvider)
