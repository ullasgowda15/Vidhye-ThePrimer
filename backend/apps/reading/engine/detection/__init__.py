"""
Reading-error detection.

This package is the one seam the rest of the app depends on: hand it audio plus
the passage the child was meant to read, get back a per-word verdict. Which
engines do the work is deliberately hidden.

Two lanes, answering different questions:

  whisper_engine  what was said, where, and how fast - omissions, substitutions,
                  hesitations, self-corrections
  phoneme_engine  how each word was pronounced - which sound didn't come out

Both are needed. Word-level transcription is structurally blind to
mispronunciation: its language model repairs "Den" back into "Then" at high
confidence. Phoneme scoring sees that, but is noisier about whole-word
tracking. See research/asr_spikes/README.md.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from .types import (
    ACCEPTABLE,
    STATUS_EXTRA,
    STATUS_OK,
    STATUS_OMISSION,
    STATUS_SUBSTITUTION,
    STATUS_UNCLEAR,
    WordResult,
    read_acceptably,
)

log = logging.getLogger(__name__)

# --- The one edit made when this engine was vendored into The Primer ----------
#
# Upstream imported `audio`, `whisper_engine` and `phoneme_engine` at module
# scope, which pulls faster-whisper, torch and transformers in the moment anyone
# touches this package. That was right in Vidhye, where the only process that
# imported it was the ML worker. It is wrong here: `Reading-Integration.md` §2.1
# forbids the speech models from the app box at all, and the pure halves of this
# engine - the phonics patterns, the mastery rule, the teaching ladder - are
# imported by web code and by tests that must not need a gigabyte of wheels.
#
# So the three model-backed modules are imported inside the functions that use
# them. Every public name this package exported still resolves to the same
# object; what changed is *when*. Nothing else in the engine was edited.
# -----------------------------------------------------------------------------

# Statuses where a sound-level explanation is worth attaching.
_WORTH_EXPLAINING = {STATUS_SUBSTITUTION, STATUS_UNCLEAR}


def analyze(
    audio_path: str,
    expected_text: str,
    scoreable: Optional[Callable[[str], bool]] = None,
    sound_level: bool = True,
) -> List[WordResult]:
    """Per-word verdict for a reading of `expected_text`.

    The phoneme lane only ever *refines* a word the word-level lane already
    flagged - it never raises an error of its own. It carries a real false
    positive rate (a hesitation mid-word can read as a dropped sound), and
    correcting a child who read correctly costs more than missing an error. Used
    this way, a false positive can at worst make an existing correction's
    wording less apt; it cannot invent a correction.

    `scoreable` limits which words get sound-scored - callers pass a predicate
    so function words, which reduce in connected speech, are skipped.

    `sound_level=False` skips the phoneme lane entirely. The tutor still works;
    it just can't name which sound was missed, falling back to the phonics rule
    for the word.

    The two lanes run concurrently. They're independent - one asks what was
    said, the other how it was pronounced - so running them in sequence made
    every child wait for the sum of two models when the wait should be the
    slower of the two. Both release the GIL during inference, so threads
    genuinely overlap here.
    """
    from . import audio
    from .whisper_engine import analyze as _analyze_words

    started = time.perf_counter()
    clip = audio.decode(audio_path)
    decoded_at = time.perf_counter()

    if not sound_level:
        words = _analyze_words(clip, expected_text)
        log.info(
            "analysed %.1fs clip in %.2fs (decode %.2fs, words %.2fs)",
            len(clip) / audio.SAMPLE_RATE,
            time.perf_counter() - started,
            decoded_at - started,
            time.perf_counter() - decoded_at,
        )
        return words

    # Start the sound-level pass immediately rather than after the word-level
    # one; whether we end up using it is decided below.
    with ThreadPoolExecutor(max_workers=2) as pool:
        sounds_future = pool.submit(_safe_missed_sounds, clip, expected_text, scoreable)
        words = _analyze_words(clip, expected_text)
        words_at = time.perf_counter()
        sounds = sounds_future.result()

    log.info(
        "analysed %.1fs clip in %.2fs (decode %.2fs, words %.2fs, sounds +%.2fs)",
        len(clip) / audio.SAMPLE_RATE,
        time.perf_counter() - started,
        decoded_at - started,
        words_at - decoded_at,
        time.perf_counter() - words_at,
    )

    if not any(w.status in _WORTH_EXPLAINING for w in words):
        return words

    for word in words:
        if word.status in _WORTH_EXPLAINING:
            word.missed_sound = sounds.get(word.index)

    return words


def _safe_missed_sounds(clip, expected_text: str, scoreable):
    """Sound-level detail is an enhancement, never a dependency.

    If it fails the child still gets coached from the word-level verdict, which
    is the whole reason this lane only ever refines and never triggers.
    """
    try:
        from .phoneme_engine import missed_sounds

        return missed_sounds(clip, expected_text, scoreable=scoreable)
    except Exception:
        log.exception("phoneme scoring failed; continuing with word-level only")
        return {}


def warm_up(sound_level: bool = True) -> None:
    """Load models before the first child speaks.

    Skips the phoneme model when the lane is off - it's 1.2GB and half the
    startup time for something that would never be called.
    """
    from .whisper_engine import warm_up as _warm_words

    _warm_words()
    if sound_level:
        from .phoneme_engine import warm_up as _warm_phonemes

        _warm_phonemes()


from . import fluency  # noqa: E402  - after the model imports it depends on

__all__ = [
    "fluency",
    "analyze",
    "warm_up",
    "WordResult",
    "read_acceptably",
    "ACCEPTABLE",
    "STATUS_OK",
    "STATUS_UNCLEAR",
    "STATUS_SUBSTITUTION",
    "STATUS_OMISSION",
    "STATUS_EXTRA",
]
