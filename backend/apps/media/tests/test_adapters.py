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
    GoogleTTSProvider,
    LocalToneTTSProvider,
    OpenAITTSProvider,
    configured_voice,
    select_tts_provider,
    select_tts_providers,
    synthesize_with_failover,
    tts_provider_order,
)
from apps.media.exceptions import MediaConfigurationError, MediaProviderError
from apps.media.placeholder import CHARACTER_PART, SCENE_PART, part
from apps.media.prompts import CharacterRef, build_illustration_prompt
from apps.media.safety import sniff_content_type

PIP = CharacterRef("companion:pip", "Pip", "a small amber dragon with one crooked horn")
OTHER = CharacterRef("companion:nim", "Nim", "a tall silver heron with a long beak")

#: A minimally valid WAV, so the bytes a failover returns are the *playable* kind
#: `media.safety` will let through rather than an anonymous blob.
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32


class _StubTTS:
    """A voice provider that either speaks or fails, on demand."""

    def __init__(self, name: str, *, error: Exception | None = None) -> None:
        self.name = name
        self.calls: list[str] = []
        self._error = error

    def is_configured(self) -> bool:
        return True

    def resolve_voice(self, voice_id: str) -> str:
        return voice_id

    def synthesize(self, text: str, voice_id: str) -> bytes:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return WAV + self.name.encode()


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


# ---------------------------------------------------------------------------
# Voice failover (§4.9) — the image side has none, and that asymmetry is the point
# ---------------------------------------------------------------------------
QUOTA_EXCEEDED = MediaProviderError(
    'Voice provider returned 401 for voice \'v\': {"code":"quota_exceeded",'
    '"message":"This request exceeds your quota of 10000. You have 62 credits '
    "remaining, while 188 credits are required for this request.\"}"
)


def test_a_voice_outage_fails_over_instead_of_leaving_a_child_a_silent_screen(caplog) -> None:
    """The measured failure this exists for.

    ElevenLabs really did answer `quota_exceeded` on the founder's keys, and the
    child got 21 seconds of narration they cannot read with nothing to tap. A
    second provider makes that one vendor's bad day instead of the child's.
    """
    down = _StubTTS("elevenlabs", error=QUOTA_EXCEEDED)
    up = _StubTTS("google")

    with caplog.at_level("WARNING"):
        audio, label = synthesize_with_failover("Which way shall we go?", "warm_low", [down, up])

    assert audio == WAV + b"google"
    # The row must say who really spoke, or a voice that changed mid-story is
    # unexplainable afterwards.
    assert label == "google"
    assert down.calls and up.calls
    assert "media.tts_failover failed=elevenlabs" in caplog.text
    assert "next=google" in caplog.text
    # How long the refusal took, because a provider that says no *slowly* spends
    # the turn budget before the fallback speaks — ElevenLabs took ~22s to answer
    # at all when this was measured against its live quota error.
    assert "after_ms=" in caplog.text


def test_a_primary_that_cannot_name_a_voice_also_fails_over() -> None:
    # ElevenLabs raises MediaConfigurationError from *inside* synthesize when no
    # voice resolves. That is a primary which cannot speak, which is exactly when
    # the fallback should — not a reason to give the beat no voice at all.
    misconfigured = _StubTTS("elevenlabs", error=MediaConfigurationError("no voice"))
    up = _StubTTS("google")

    audio, label = synthesize_with_failover("Hi Aarav!", "warm_low", [misconfigured, up])

    assert label == "google" and audio == WAV + b"google"


def test_the_first_provider_that_works_is_the_only_one_called() -> None:
    first = _StubTTS("elevenlabs")
    second = _StubTTS("google")

    _, label = synthesize_with_failover("Hi Aarav!", "warm_low", [first, second])

    assert label == "elevenlabs"
    # Not "cheaper" — a fallback that is called anyway is a fallback that has
    # quietly become the voice.
    assert second.calls == []


def test_the_whole_chain_failing_still_raises_so_the_turn_budget_is_the_backstop() -> None:
    """Every provider down is not a special case — it is the old behaviour.

    `MEDIA_TURN_BUDGET_S` is what gives the child their turn when no voice can be
    had, so this path must keep raising rather than inventing silence.
    """
    down = _StubTTS("elevenlabs", error=QUOTA_EXCEEDED)
    also_down = _StubTTS("google", error=MediaProviderError("503"))

    with pytest.raises(MediaProviderError, match="503"):
        synthesize_with_failover("Hi Aarav!", "warm_low", [down, also_down])


def test_the_chain_is_the_primary_then_the_fallbacks_in_order(settings) -> None:
    settings.TTS_PROVIDER = "elevenlabs"
    settings.TTS_FALLBACK_PROVIDERS = ["google", "elevenlabs", "local"]

    # Deduplicated, primary first: naming the primary again must not make it get
    # two attempts at the same outage.
    assert tts_provider_order() == ["elevenlabs", "google", "local"]


def test_a_fallback_with_no_key_is_skipped_rather_than_breaking_the_voice(settings) -> None:
    settings.TTS_PROVIDER = "local"
    settings.TTS_FALLBACK_PROVIDERS = ["google"]
    settings.GOOGLE_TTS_API_KEY = ""

    # A fallback nobody has configured is absent, not a misconfiguration — the
    # founder adding GOOGLE_TTS_API_KEY is all it should take to turn it on.
    assert [p.name for p in select_tts_providers()] == ["local"]

    settings.GOOGLE_TTS_API_KEY = "key"
    assert [p.name for p in select_tts_providers()] == ["local", "google"]


def test_a_typo_in_the_fallback_list_is_an_error_not_a_missing_fallback(settings) -> None:
    settings.TTS_PROVIDER = "local"
    settings.TTS_FALLBACK_PROVIDERS = ["gooogle"]

    # The one thing a fallback must never be is silently absent.
    with pytest.raises(MediaConfigurationError, match="Unknown TTS provider 'gooogle'"):
        select_tts_providers()


def test_no_provider_at_all_reports_the_primary_s_own_error(settings) -> None:
    settings.TTS_PROVIDER = "elevenlabs"
    settings.ELEVENLABS_API_KEY = ""
    settings.TTS_FALLBACK_PROVIDERS = ["google"]
    settings.GOOGLE_TTS_API_KEY = ""

    with pytest.raises(MediaConfigurationError, match="ELEVENLABS_API_KEY"):
        select_tts_providers()


# ---------------------------------------------------------------------------
# Google Cloud TTS — the implemented fallback
# ---------------------------------------------------------------------------
def test_the_google_voice_needs_only_a_key(settings) -> None:
    settings.GOOGLE_TTS_API_KEY = ""
    assert GoogleTTSProvider().is_configured() is False

    # Unlike ElevenLabs, Google will pick a voice for a language code, so there is
    # no way to hold a key here and still fail at the URL.
    settings.GOOGLE_TTS_API_KEY = "key"
    assert GoogleTTSProvider().is_configured() is True


def test_a_google_voice_key_maps_to_a_vendor_voice(settings) -> None:
    settings.GOOGLE_TTS_VOICE = "en-US-Neural2-F"
    settings.GOOGLE_TTS_VOICE_MAP = {"calm_deep": "en-US-Neural2-D"}

    provider = GoogleTTSProvider()
    assert provider.resolve_voice("calm_deep") == "en-US-Neural2-D"
    assert provider.resolve_voice("warm_low") == "en-US-Neural2-F"


def test_google_sends_the_language_its_voice_belongs_to(monkeypatch, settings) -> None:
    """Derived from the voice name, because Google rejects a mismatch.

    Two settings that must agree are two settings that eventually will not, and
    the failure lands on the beat the fallback was supposed to rescue.
    """
    import base64

    import httpx

    settings.GOOGLE_TTS_API_KEY = "key"
    settings.GOOGLE_TTS_VOICE = "en-GB-Neural2-A"
    settings.GOOGLE_TTS_LANGUAGE_CODE = "de-DE"  # deliberately wrong; must be ignored
    sent: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        sent.update({"url": url, "json": json, "headers": headers})
        return httpx.Response(
            200,
            json={"audioContent": base64.b64encode(WAV).decode()},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    audio = GoogleTTSProvider().synthesize("Hi Aarav!", "warm_low")

    assert audio == WAV
    assert sniff_content_type(audio) == "audio/wav"
    voice = sent["json"]["voice"]  # type: ignore[index]
    assert voice == {"languageCode": "en-GB", "name": "en-GB-Neural2-A"}
    # LINEAR16 carries a WAV header, which is what makes the bytes recognisable to
    # the safety scan — an unrecognised asset is FLAGGED, i.e. no voice at all.
    assert sent["json"]["audioConfig"] == {"audioEncoding": "LINEAR16"}  # type: ignore[index]
    # The key travels in a header, never in a URL that ends up in logs and traces.
    assert sent["headers"] == {"X-goog-api-key": "key"}
    assert "key" not in str(sent["url"])


def test_a_google_outage_is_a_provider_error_the_chain_can_move_past(
    monkeypatch, settings
) -> None:
    import httpx

    settings.GOOGLE_TTS_API_KEY = "key"

    def fake_post(url, *, json, headers, timeout):
        return httpx.Response(429, text="quota exceeded", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(MediaProviderError, match="429"):
        GoogleTTSProvider().synthesize("Hi Aarav!", "warm_low")


def test_google_returning_no_audio_is_not_silently_stored(monkeypatch, settings) -> None:
    import httpx

    settings.GOOGLE_TTS_API_KEY = "key"

    def fake_post(url, *, json, headers, timeout):
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(MediaProviderError, match="no audio"):
        GoogleTTSProvider().synthesize("Hi Aarav!", "warm_low")
