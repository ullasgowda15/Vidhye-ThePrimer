"""
Getting audio into memory, once.

Both engines used to take a file path and decode it themselves - Whisper
internally, the phoneme model through its own call. Decoding the same clip
twice is pure waste, and it meant the audio libraries were being entered from
two places, which is where the `av`/torch segfaults came from.

Decode here, pass arrays around.
"""

import numpy as np

SAMPLE_RATE = 16_000


def decode(path: str) -> np.ndarray:
    """A clip as 16kHz mono float32, which is what both models expect."""
    from faster_whisper.audio import decode_audio

    return decode_audio(path, sampling_rate=SAMPLE_RATE)


def silence(seconds: float = 1.0) -> np.ndarray:
    """A throwaway clip for warming a model up."""
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)
