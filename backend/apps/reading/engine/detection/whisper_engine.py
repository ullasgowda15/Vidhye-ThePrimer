"""
Whisper-based detection engine.

Reliable for: omissions, substitutions, hesitations, self-corrections.
Weak for: mispronunciations, because Whisper's language model quietly corrects
them back to the expected word ("Then" read as "Den" came back as "Then" at 0.92
confidence). Low confidence is therefore reported as "unclear", never as a
verdict. See research/asr_spikes/README.md for the full findings.
"""

import os
from typing import List

import numpy as np
from faster_whisper import WhisperModel

from . import audio as audio_io
from .alignment import DetectedWord, align
from .types import WordResult

# "base", not "small". Measured on the fixtures: 3x faster (3.6s vs 10.9s on a
# 12s clip) *and* more accurate for us. A bigger model's language prior quietly
# corrects mispronunciations back to the expected word - "sip" came back as
# "ship" from small, but base transcribed what was actually said. We already
# know the text, so the language model is a liability, not a help.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")

# CPU int8 is the laptop default, not the production one. On a GPU box set
# ASR_DEVICE=cuda and ASR_COMPUTE_TYPE=float16 - same code, roughly an order of
# magnitude faster. Nothing about the engine assumes CPU.
DEVICE = os.environ.get("ASR_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "int8")

# Tuned against the fixtures in research/asr_spikes - see that README before changing.
LOW_CONFIDENCE_THRESHOLD = 0.65
HESITATION_GAP_SECONDS = 1.2
DURATION_MULTIPLIER = 3.0
DURATION_MIN_SECONDS = 0.9

_model = None


def _get_model() -> WhisperModel:
    """Loading takes a few seconds, so keep one instance around between requests."""
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    return _model


def warm_up() -> None:
    """Load weights and run one throwaway inference.

    Called at worker startup. Without it the first child to speak waits through
    the whole cold start, which is the worst possible moment for it.
    """
    list(_get_model().transcribe(audio_io.silence())[0])


def _transcribe(clip: np.ndarray) -> List[DetectedWord]:
    segments, _info = _get_model().transcribe(
        clip,
        word_timestamps=True,
        vad_filter=True,  # trims silence that would otherwise inflate word durations
    )
    return [
        {
            # Whisper attaches sentence punctuation to words ("dish."). This field
            # is shown to people, so strip it - the alignment works off tokens.
            "word": w.word.strip().strip(".,!?;:\"'"),
            "start": w.start,
            "end": w.end,
            "prob": w.probability,
        }
        for segment in segments
        for w in segment.words
    ]


def _duration_threshold(detected: List[DetectedWord]) -> float:
    durations = sorted(w["end"] - w["start"] for w in detected)
    if not durations:
        return DURATION_MIN_SECONDS
    median = durations[len(durations) // 2]
    return max(DURATION_MIN_SECONDS, median * DURATION_MULTIPLIER)


def analyze(clip: np.ndarray, expected_text: str) -> List[WordResult]:
    detected = _transcribe(clip)
    threshold = _duration_threshold(detected)
    last_index = len(detected) - 1

    def is_slow(word: DetectedWord, index: int) -> bool:
        # The first and last word of a clip absorb leading/trailing silence even with
        # VAD on. That's where the recording starts and stops, not the child hesitating.
        if index in (0, last_index):
            return False
        return (word["end"] - word["start"]) >= threshold

    return align(
        expected_text,
        detected,
        low_confidence=LOW_CONFIDENCE_THRESHOLD,
        hesitation_gap_seconds=HESITATION_GAP_SECONDS,
        is_slow=is_slow,
    )
