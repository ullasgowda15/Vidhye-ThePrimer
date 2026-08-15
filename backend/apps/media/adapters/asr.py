"""Speech recognition adapters (Docs/Build/Reading-Integration.md §7).

The fourth adapter, same shape as image, TTS and embedding: a Protocol, one
provider name in configuration, and typed stubs for what is declared but not
built. Nothing here is wired into a request path in Phase 0 — this file exists so
Phase 2 has a socket to fill rather than a pipeline to restructure.

**What this adapter is for, and what it is not.** It answers one question: *given
audio and the text we already know the child was asked to read, which words came
out and how?* That is not general transcription and the difference is the whole
reason this seam is narrow. Vidhye measured the alternative and it does not work:
a general recogniser's language prior repairs the very errors we exist to catch —
Whisper turned a spoken "Den" back into "Then" at 0.92 confidence, and the larger
`small` model turned "sip" back into "ship". A provider with a *stronger* prior is
worse at this job, not better, which is why "just call a cloud speech API" is
ruled out in §5.6 of the programme brief on technical grounds rather than
budgetary ones.

**Why there is no cloud provider class below.** A vendor that only returns words
cannot serve the phoneme lane at all, and the phoneme lane is what lets the
companion name the sound rather than the word. Anything implemented here has to
be able to answer both questions, which in practice means running our own models
somewhere off the app box (§2.1).

**Why the local provider raises rather than degrading.** Every other adapter in
this package has a graceful fallback, because a missing picture is a playable
beat and a missing voice has a second vendor behind it. This one has neither
shape. If we cannot analyse a reading there is nothing to say about it, and the
correct behaviour is for the caller to not have asked — Phase 1 ships the whole
reading interaction with this adapter unconfigured and never calls it, which is
the arrangement the phasing exists to make possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ReadingAnalysis:
    """What came back from analysing one utterance.

    Deliberately not the engine's own `WordResult` list. The engine's types live
    in `apps.reading.engine.detection.types` and know nothing about providers;
    this is the transport across the adapter boundary, so a provider that is not
    our own engine can still be described in it.

    `words` carries the per-word verdicts as plain dicts in the engine's own
    vocabulary (`index`, `expected`, `detected`, `status`, `missed_sound`,
    `slow`, `paused_before`, `confidence`). `wcpm` is words correct per minute
    where the clip was long enough for the number to mean anything, and `None`
    where it was not — a rate from a two-word clip is noise dressed as a measure.
    """

    words: tuple[dict[str, object], ...]
    wcpm: float | None = None
    duration_s: float | None = None


@runtime_checkable
class ASRProvider(Protocol):
    """One speech analyser, reduced to what the reading pipeline needs."""

    name: str

    def is_configured(self) -> bool: ...

    def analyse(self, audio_path: str, expected_text: str) -> ReadingAnalysis:
        """Per-word verdict for a reading of `expected_text`.

        `expected_text` is not a hint and not a prompt — it is the text the child
        was looking at, and the analysis is a *comparison* against it rather than
        open transcription. A provider that ignores it is not implementing this
        interface.
        """
        ...


class _UnimplementedASRProvider:
    """Typed seam for an analyser that is declared but not built."""

    name = "unimplemented"

    def is_configured(self) -> bool:
        return False

    def analyse(self, audio_path: str, expected_text: str) -> ReadingAnalysis:
        raise NotImplementedError(f"ASR provider '{self.name}' is not implemented yet.")


class NullASRProvider(_UnimplementedASRProvider):
    """The configured default, and the only provider that exists in Phase 0-1.

    Named `null` rather than `local` because there is no degraded local mode to
    fall back to — see the module docstring. Its job is to make "reading analysis
    is not available" a configuration a deployment can hold honestly, instead of
    an import error or a silently absent feature.
    """

    name = "null"


class VidhyeASRProvider(_UnimplementedASRProvider):
    """Our own engine, running off the app box. Phase 2.

    Will wrap `apps.reading.engine.detection.analyze`, which already returns the
    two-lane verdict this interface describes. It is a stub rather than a call
    because §2.1 forbids the models from this box, so the thing to build in
    Phase 2 is the *host* and a client for it — not this method in-process.
    """

    name = "vidhye"


ASR_PROVIDERS: dict[str, type] = {
    NullASRProvider.name: NullASRProvider,
    VidhyeASRProvider.name: VidhyeASRProvider,
}
