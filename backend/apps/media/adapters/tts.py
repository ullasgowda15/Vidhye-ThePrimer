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

**Unlike the image side, this one fails over.** The two are not symmetrical, and
the asymmetry is deliberate. A missing illustration is a picture-less but
playable beat (`streaming.ESSENTIAL_STAGES`); a missing *voice* is a screen full
of words a four-year-old cannot read and nothing to tap, so a single vendor
having a bad day is a child-facing dead end (§4.9). `synthesize_with_failover`
walks `TTS_PROVIDER` and then `TTS_FALLBACK_PROVIDERS`, the same shape as the
model router. `MEDIA_TURN_BUDGET_S` stays as the final backstop for the case
where *every* provider is down; widening it was never the fix, because a longer
wait on unreadable text helps nobody.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import math
import struct
import time
import wave
from typing import Protocol, runtime_checkable

from django.conf import settings

from apps.media.exceptions import MediaConfigurationError, MediaError, MediaProviderError

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


class GoogleTTSProvider:
    """Google Cloud Text-to-Speech — the implemented fallback voice.

    Exists because ElevenLabs running out of credits was leaving a pre-reader
    with a silent, unreadable screen. Called over plain HTTP for the same reason
    the Cloudflare image adapter is: the request is one POST, and pulling in a
    Google client library (and its ADC machinery) to send it would be the larger
    change.

    `LINEAR16`, not MP3: Google documents LINEAR16 as carrying a WAV header, so
    the bytes sniff as `audio/wav` in `media.safety` rather than risking an MP3
    frame the signature table does not know — and an asset the scan cannot
    recognise is `FLAGGED`, which on this path means no voice at all. Losing the
    compression matters less than always being playable.
    """

    name = "google"
    credential_env_var = "GOOGLE_TTS_API_KEY"
    base_url = "https://texttospeech.googleapis.com/v1"

    def __init__(self, api_key: str = "", default_voice: str = "") -> None:
        self._api_key = api_key or settings.GOOGLE_TTS_API_KEY
        self._default_voice = default_voice or settings.GOOGLE_TTS_VOICE

    def is_configured(self) -> bool:
        # The key alone, unlike ElevenLabs: Google will pick a voice for a
        # language code if it is given no name, so there is no way to hold a key
        # here and still fail at the URL.
        return bool(self._api_key)

    def resolve_voice(self, voice_id: str) -> str:
        """Turn a companion's semantic voice key into a Google voice name."""
        mapped = settings.GOOGLE_TTS_VOICE_MAP.get(voice_id)
        if mapped:
            return str(mapped)
        return self._default_voice

    def _language_code(self, voice: str) -> str:
        """The language a voice name belongs to — `en-US-Neural2-F` -> `en-US`.

        Derived rather than configured alongside the voice, because Google
        rejects a request whose `languageCode` and `name` disagree, and two
        settings that must match are two settings that eventually will not.
        `GOOGLE_TTS_LANGUAGE_CODE` is the answer only when there is no name to
        derive from.
        """
        parts = voice.split("-")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}-{parts[1]}"
        return str(settings.GOOGLE_TTS_LANGUAGE_CODE)

    def synthesize(self, text: str, voice_id: str) -> bytes:
        import httpx

        voice = self.resolve_voice(voice_id)
        speaker: dict[str, str] = {"languageCode": self._language_code(voice)}
        if voice:
            speaker["name"] = voice

        try:
            response = httpx.post(
                f"{self.base_url}/text:synthesize",
                json={
                    "input": {"text": text},
                    "voice": speaker,
                    "audioConfig": {"audioEncoding": "LINEAR16"},
                },
                # The key goes in a header, not the query string: a URL ends up in
                # exception text, proxy logs and traces, and this one would be
                # carrying a credential.
                headers={"X-goog-api-key": self._api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise MediaProviderError(f"Could not reach the voice provider: {exc}") from exc

        if response.status_code >= 400:
            raise MediaProviderError(
                f"Voice provider returned {response.status_code} for voice "
                f"'{voice or self._language_code(voice)}': {response.text[:300]}"
            )

        encoded = (response.json() or {}).get("audioContent")
        if not encoded:
            raise MediaProviderError("Voice provider returned no audio.")
        try:
            return base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise MediaProviderError(f"Audio payload was not valid base64: {exc}") from exc


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
    """The provider named by `TTS_PROVIDER`, if it can actually be called.

    Still the *primary* specifically, not "whichever one works": it is what
    `configured_voice` hashes into the cache key, and what `smoke_media` reports
    as the voice under test. `select_tts_providers` is the chain a real
    synthesis walks.
    """
    name = settings.TTS_PROVIDER
    factory = _factory(name, setting="TTS_PROVIDER")

    provider = factory()
    if not provider.is_configured():
        raise MediaConfigurationError(
            f"TTS provider '{name}' has no credentials. Set ELEVENLABS_API_KEY and "
            "ELEVENLABS_VOICE_ID in your .env, or set TTS_PROVIDER=local for an "
            "offline chime while developing."
        )
    return provider


def _factory(name: str, *, setting: str) -> type:
    factory = TTS_PROVIDERS.get(name)
    if factory is None:
        raise MediaConfigurationError(
            f"Unknown TTS provider '{name}' in {setting}. "
            f"Known providers: {', '.join(sorted(TTS_PROVIDERS))}."
        )
    return factory


def tts_provider_order() -> list[str]:
    """`TTS_PROVIDER` then `TTS_FALLBACK_PROVIDERS`, deduplicated, in order.

    Names only, no credentials touched, so this is safe to log and safe to call
    from anywhere that wants to report the chain.
    """
    order: list[str] = []
    for name in [settings.TTS_PROVIDER, *settings.TTS_FALLBACK_PROVIDERS]:
        if name and name not in order:
            order.append(name)
    return order


def select_tts_providers() -> list[TTSProvider]:
    """Every provider in the chain that has credentials, primary first.

    An unknown *name* is still an error — a typo in `TTS_FALLBACK_PROVIDERS`
    would otherwise be a fallback that silently is not there, which is the one
    thing a fallback must never be. Missing credentials are not: a fallback
    nobody has configured is absent, not misconfigured, so it is skipped.

    Raises `MediaConfigurationError` when the chain is empty, with the same
    message `select_tts_provider` gives — that is the real "no voice at all"
    case and it should read the same however you arrived at it.
    """
    providers: list[TTSProvider] = []
    for name in tts_provider_order():
        provider = _factory(name, setting="TTS_PROVIDER/TTS_FALLBACK_PROVIDERS")()
        if provider.is_configured():
            providers.append(provider)
    if not providers:
        # Reuse the primary's error so the message names ELEVENLABS_API_KEY and
        # the `local` escape hatch rather than being a vaguer second version.
        select_tts_provider()
    return providers


def synthesize_with_failover(
    text: str,
    voice_id: str,
    providers: list[TTSProvider] | None = None,
) -> tuple[bytes, str]:
    """Speak `text`, trying each configured provider in turn. Returns
    `(audio, provider label)`.

    The loop is the whole point. ElevenLabs answering `quota_exceeded` used to
    mean the beat had no voice, and a beat with no voice is a pre-reader looking
    at narration they cannot read with nothing to tap until
    `MEDIA_TURN_BUDGET_S` lets them past — measured at 21 seconds during the Day
    6 review, on the founder's own live keys. One vendor's bad day should not be
    able to do that.

    Every failover is logged at warning level with the provider that failed and
    the one being tried, because "the voice sounds different today" is otherwise
    an unexplainable report. If the whole chain fails, the *last* error is
    raised: the caller (`media.service`) treats it exactly as it treated a
    single provider's failure, so `MEDIA_TURN_BUDGET_S` is still the backstop.
    """
    from apps.media import quota

    chain = providers if providers is not None else select_tts_providers()
    last_error: MediaError | None = None

    # A provider known to be out is moved to the back rather than dropped. Dropped
    # would be wrong twice: `quota` guesses the reset window, and a chain that
    # skipped its last member because Redis said so would raise "no provider
    # available" for a child whose primary was in fact fine. Reordered, the
    # cooldown costs a vendor its place in the queue and nothing more, and the
    # provider still gets tried if everything ahead of it fails.
    if providers is None:
        live = [p for p in chain if not quota.is_exhausted(p.name)]
        resting = [p for p in chain if quota.is_exhausted(p.name)]
        if resting:
            logger.info(
                "media.tts_quota_skip resting=%s trying=%s",
                ",".join(p.name for p in resting),
                ",".join(p.name for p in live) or "-",
            )
        chain = live + resting

    for index, provider in enumerate(chain):
        started = time.monotonic()
        try:
            return provider.synthesize(text, voice_id), provider_label(provider)
        except (MediaProviderError, MediaConfigurationError) as exc:
            # `MediaConfigurationError` too: ElevenLabs raises it from inside
            # `synthesize` when no voice resolves, and a primary that cannot name
            # a voice is exactly when the fallback should speak.
            last_error = exc
            quota.mark_exhausted(provider.name, exc)
            remaining = chain[index + 1 :]
            # `after_ms` matters as much as the error. A provider that *refuses*
            # slowly spends the turn budget before the fallback has said a word —
            # ElevenLabs took ~22s to answer at all when this was measured — so
            # "the failover worked but the voice was late" and "the failover
            # worked" have to be distinguishable in a log.
            logger.warning(
                "media.tts_failover failed=%s after_ms=%d next=%s error=%s",
                provider.name,
                (time.monotonic() - started) * 1000,
                remaining[0].name if remaining else "-",
                exc,
            )

    # Only reachable with a non-empty chain that failed end to end;
    # `select_tts_providers` raises when there is nothing to try.
    raise last_error or MediaConfigurationError("No TTS provider was available.")
