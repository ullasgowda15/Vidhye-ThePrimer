"""Text-to-speech adapters (PRODUCT.md §16.6, §9.1).

The companion's voice is part of its identity, not a rendering detail: the same
voice must speak every line it ever says, or the friend changes character
between sentences.

Same contract as the image side — `synthesize(text, voice_id) -> bytes`, one
provider name in configuration, and typed stubs for the vendors that are
declared but not built.

`voice_id` on a companion is a *semantic* key ("warm_low"), not a vendor id.
The adapter resolves it, which is what keeps a companion's voice stable across a
vendor change: swapping ElevenLabs for another service is a new class here plus
a mapping, not a migration over every companion row.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import struct
import wave
from typing import Protocol, runtime_checkable

from django.conf import settings

from apps.media.exceptions import MediaConfigurationError, MediaProviderError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60.0


@runtime_checkable
class TTSProvider(Protocol):
    """One voice model, reduced to what the media service needs."""

    name: str

    def is_configured(self) -> bool: ...

    def resolve_voice(self, voice_id: str) -> str:
        """The vendor voice a semantic key will actually be spoken in.

        Part of the contract because it is part of the *cache key*: two
        companions whose keys resolve to one vendor voice share a recording, and
        editing the map must invalidate the recordings it changes.
        """
        ...

    def synthesize(self, text: str, voice_id: str) -> bytes:
        """Speak `text` in `voice_id` and return the raw audio bytes."""
        ...


class ElevenLabsTTSProvider:
    """ElevenLabs — the implemented real voice provider."""

    name = "elevenlabs"
    credential_env_var = "ELEVENLABS_API_KEY"
    base_url = "https://api.elevenlabs.io/v1"

    def __init__(self, api_key: str = "", model_id: str = "", default_voice: str = "") -> None:
        self._api_key = api_key or settings.ELEVENLABS_API_KEY
        self._model_id = model_id or settings.ELEVENLABS_MODEL_ID
        self._default_voice = default_voice or settings.ELEVENLABS_VOICE_ID

    def is_configured(self) -> bool:
        # A voice is as required as the key: ElevenLabs has no default voice, so
        # a key alone would fail at the URL rather than here.
        return bool(self._api_key and (self._default_voice or settings.ELEVENLABS_VOICE_MAP))

    def resolve_voice(self, voice_id: str) -> str:
        """Turn a companion's semantic voice key into an ElevenLabs voice id."""
        mapped = settings.ELEVENLABS_VOICE_MAP.get(voice_id)
        if mapped:
            return str(mapped)
        if voice_id and voice_id not in settings.ELEVENLABS_VOICE_MAP:
            logger.debug(
                "Voice key %r is not in ELEVENLABS_VOICE_MAP; using the default voice.",
                voice_id,
            )
        return self._default_voice

    def synthesize(self, text: str, voice_id: str) -> bytes:
        import httpx

        voice = self.resolve_voice(voice_id)
        if not voice:
            raise MediaConfigurationError(
                "No ElevenLabs voice is configured. Set ELEVENLABS_VOICE_ID (or add "
                f"'{voice_id}' to ELEVENLABS_VOICE_MAP)."
            )

        try:
            response = httpx.post(
                f"{self.base_url}/text-to-speech/{voice}",
                json={"text": text, "model_id": self._model_id},
                headers={"xi-api-key": self._api_key, "Accept": "audio/mpeg"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise MediaProviderError(f"Could not reach the voice provider: {exc}") from exc

        if response.status_code >= 400:
            raise MediaProviderError(
                f"Voice provider returned {response.status_code} for voice "
                f"'{voice}': {response.text[:300]}"
            )

        audio = response.content
        if not audio:
            raise MediaProviderError("Voice provider returned no audio.")
        return audio


class LocalToneTTSProvider:
    """A deterministic offline chime. Development and CI only.

    It is not speech and never pretends to be — it is two soft notes, derived
    from the voice key and the line, so the audio half of the pipeline (cache
    key, scan, storage, playback in the browser) can be exercised end to end
    without a key. Every row it writes is labelled `local-chime`.
    """

    name = "local"
    credential_env_var = ""
    provider_label = "local-chime"

    SAMPLE_RATE = 22050
    DURATION_SECONDS = 1.2

    def is_configured(self) -> bool:
        return True

    def resolve_voice(self, voice_id: str) -> str:
        """The chime has no vendor voices — the semantic key is the voice."""
        return voice_id

    def synthesize(self, text: str, voice_id: str) -> bytes:
        # sha256, not `hash()`: Python's string hashing is salted per process, so
        # the same voice would chime differently after a restart and the cached
        # audio would stop matching freshly generated audio.
        note = int(hashlib.sha256(f"{voice_id}:{text}".encode()).hexdigest()[:8], 16)
        # A wide pitch range rather than a handful of notes, so two different
        # voices are audibly (and byte-wise) different rather than colliding on
        # the same chime.
        base = 180.0 + (note % 220)
        frames = int(self.SAMPLE_RATE * self.DURATION_SECONDS)
        samples = bytearray()

        for index in range(frames):
            position = index / frames
            # Two notes, the second a fifth above, with a gentle fade so it
            # sounds like a chime rather than a click.
            frequency = base if position < 0.5 else base * 1.5
            envelope = math.sin(math.pi * position) ** 2
            wave_at = math.sin(2 * math.pi * frequency * index / self.SAMPLE_RATE)
            samples += struct.pack("<h", int(12000 * envelope * wave_at))

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.SAMPLE_RATE)
            handle.writeframes(bytes(samples))
        return buffer.getvalue()


class _UnimplementedTTSProvider:
    """Typed seam for a voice vendor that is declared but not built."""

    name = "unimplemented"
    credential_env_var = ""

    def is_configured(self) -> bool:
        return False

    def resolve_voice(self, voice_id: str) -> str:
        return voice_id

    def synthesize(self, text: str, voice_id: str) -> bytes:
        raise NotImplementedError(f"TTS provider '{self.name}' is not implemented yet.")


class OpenAITTSProvider(_UnimplementedTTSProvider):
    """Fallback seam — OpenAI speech. Not implemented."""

    name = "openai"


class GoogleTTSProvider(_UnimplementedTTSProvider):
    """Fallback seam — Google Cloud TTS. Not implemented."""

    name = "google"


TTS_PROVIDERS: dict[str, type] = {
    ElevenLabsTTSProvider.name: ElevenLabsTTSProvider,
    LocalToneTTSProvider.name: LocalToneTTSProvider,
    OpenAITTSProvider.name: OpenAITTSProvider,
    GoogleTTSProvider.name: GoogleTTSProvider,
}


def provider_label(provider: TTSProvider) -> str:
    return getattr(provider, "provider_label", provider.name)


def configured_voice(voice_id: str, provider: TTSProvider | None = None) -> str:
    """What `voice_id` resolves to on the configured vendor — for the cache key.

    The semantic key alone is not enough to identify a recording: every
    unmapped key currently resolves to `ELEVENLABS_VOICE_ID`, so editing that
    variable (or `ELEVENLABS_VOICE_MAP`) would otherwise leave every companion
    speaking in the old vendor voice forever.

    Deliberately total and credential-free. This is called *before* the cache
    lookup, on the path that serves a child their companion's greeting: an
    unknown provider name or a resolver that cannot answer falls back to the
    semantic key rather than raising. A real misconfiguration is still reported
    by `select_tts_provider`, on the cache miss that actually needs a key.
    """
    if provider is None:
        factory = TTS_PROVIDERS.get(settings.TTS_PROVIDER)
        if factory is None:
            return voice_id
        provider = factory()
    # `getattr`, because a caller may inject a test double that only synthesises.
    resolve = getattr(provider, "resolve_voice", None)
    return str(resolve(voice_id)) if resolve else voice_id


def select_tts_provider() -> TTSProvider:
    """The provider named by `TTS_PROVIDER`, if it can actually be called."""
    name = settings.TTS_PROVIDER
    factory = TTS_PROVIDERS.get(name)
    if factory is None:
        raise MediaConfigurationError(
            f"Unknown TTS provider '{name}' in TTS_PROVIDER. "
            f"Known providers: {', '.join(sorted(TTS_PROVIDERS))}."
        )

    provider = factory()
    if not provider.is_configured():
        raise MediaConfigurationError(
            f"TTS provider '{name}' has no credentials. Set ELEVENLABS_API_KEY and "
            "ELEVENLABS_VOICE_ID in your .env, or set TTS_PROVIDER=local for an "
            "offline chime while developing."
        )
    return provider
