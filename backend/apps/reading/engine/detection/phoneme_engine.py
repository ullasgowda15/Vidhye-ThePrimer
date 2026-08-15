"""
Sound-level scoring: which phoneme did the child actually miss?

Word-level transcription can tell us a word came out wrong. It cannot tell us
*why*, and it misses mispronunciations outright when the result isn't a real
word - the language model quietly repairs them. This lane asks a different
question: did the sounds this word is made of actually come out of the child's
mouth?

That precision is what makes the coaching specific. "You said watch instead of
wash" is a diagnosis; "the end of this word is sh" is teaching.

Runs in-process alongside Whisper - measured safe, and ~1.3s on a passage-length
clip. (Importing `av` directly in the same process does segfault; faster-whisper
decoding internally does not.)
"""

import difflib
import functools
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import phonemes

MODEL_NAME = os.environ.get("PHONEME_MODEL", "facebook/wav2vec2-lv-60-espeak-cv-ft")

# Same story as the whisper engine: CPU is the laptop default, not the
# production one. ASR_DEVICE=cuda moves this to a GPU without touching code.
DEVICE = os.environ.get("ASR_DEVICE", "cpu")

_model = None
_processor = None
_espeak_ready = False


def _ensure_espeak() -> None:
    global _espeak_ready
    if _espeak_ready:
        return
    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    EspeakWrapper.set_library(espeakng_loader.get_library_path())
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    _espeak_ready = True


def _get_model():
    global _model, _processor
    if _model is None:
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        _ensure_espeak()
        _processor = Wav2Vec2Processor.from_pretrained(MODEL_NAME)
        _model = Wav2Vec2ForCTC.from_pretrained(MODEL_NAME).to(DEVICE).eval()
        torch.set_grad_enabled(False)
    return _model, _processor


@functools.lru_cache(maxsize=2048)
def expected_by_word(text: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Expected phonemes for each word, one word at a time.

    Phonemizing the whole line gives more natural connected-speech forms, but
    espeak merges adjacent function words into single chunks ("on the" comes
    back as one unit), so phonemes can no longer be attributed to the right
    word. Word-by-word keeps the mapping exact.

    The cost of word-by-word is citation forms - "a" phonemizes as "eI", the
    letter's name, rather than the schwa it becomes in a sentence. That only
    affects unstressed function words, and `missed_sounds` scores teaching
    words only, so it never reaches a comparison. Content words like "fish" or
    "ship" sound the same alone as they do in a line.

    Cached: what a sentence *should* sound like never changes, so calling
    espeak once per word on every recording is waste in the request path. A
    passage read fifty times phonemizes once. Returns tuples because lru_cache
    values must be hashable to be safely shared.
    """
    _ensure_espeak()
    from phonemizer import phonemize
    from phonemizer.separator import Separator

    words = re.findall(r"[a-zA-Z']+", text)
    if not words:
        return ()

    sep = Separator(phone=" ", word="", syllable="")
    return tuple(
        (
            w.lower(),
            tuple(
                phonemize(
                    w, language="en-us", backend="espeak", separator=sep, strip=True
                ).split()
            ),
        )
        for w in words
    )


def heard_phonemes(clip: np.ndarray) -> List[str]:
    """The sounds actually produced, as the acoustic model heard them."""
    import torch

    model, processor = _get_model()
    inputs = processor(clip, sampling_rate=16000, return_tensors="pt")

    with torch.inference_mode():
        logits = model(inputs.input_values.to(DEVICE)).logits
    ids = torch.argmax(logits, dim=-1)
    return processor.batch_decode(ids)[0].split()


def missed_sounds(clip: np.ndarray, expected_text: str, scoreable=None) -> Dict[int, str]:
    """Map word index -> the expected sound the child didn't produce.

    `scoreable` decides which words are worth checking; by default, all of them.
    Callers pass a predicate to limit scoring to teaching words - function words
    reduce in connected speech and would otherwise generate noise.

    Only genuine decoding differences are reported; accent and dictionary-vs-
    acoustic vowel disagreements are filtered out by `phonemes.same_sound`.
    """
    per_word = expected_by_word(expected_text)
    if not per_word:
        return {}

    expected: List[str] = []
    owner: List[int] = []
    for index, (_word, sounds) in enumerate(per_word):
        expected.extend(sounds)
        owner.extend([index] * len(sounds))

    allowed = {
        i for i, (w, _s) in enumerate(per_word) if scoreable is None or scoreable(w)
    }

    heard = heard_phonemes(clip)
    matcher = difflib.SequenceMatcher(a=expected, b=heard, autojunk=False)

    missed: Dict[int, str] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            continue  # extra sounds are stutters, not a missed target

        for offset, i in enumerate(range(i1, i2)):
            if owner[i] not in allowed:
                continue

            want = expected[i]
            got = heard[j1 + offset] if tag == "replace" and j1 + offset < j2 else None

            if got is not None and phonemes.same_sound(want, got):
                continue
            # First miss in a word is the one worth teaching.
            missed.setdefault(owner[i], want)

    return missed


def warm_up() -> None:
    """Load weights and prime espeak at startup, not mid-conversation."""
    from . import audio as audio_io

    heard_phonemes(audio_io.silence())
    # First espeak call carries library setup; do it here too.
    expected_by_word("the cat sat")
