"""
Aligning what was read against what was expected.

The product always knows the target passage, so this is a comparison problem
rather than open transcription - see research/asr_spikes for why that matters.
Any word-level engine can reuse this by handing over detected words in the
shape described by DetectedWord.
"""

import difflib
import re
from typing import Callable, Dict, List, Optional

from .types import (
    STATUS_EXTRA,
    STATUS_OK,
    STATUS_OMISSION,
    STATUS_SUBSTITUTION,
    STATUS_UNCLEAR,
    WordResult,
)

# A detected word is a plain dict: {"word": str, "start": float, "end": float, "prob": float}
DetectedWord = Dict[str, object]


def tokenize(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z']+", text)]


def align(
    expected_text: str,
    detected: List[DetectedWord],
    *,
    low_confidence: float,
    hesitation_gap_seconds: float,
    is_slow: Callable[[DetectedWord, int], bool],
) -> List[WordResult]:
    """Walk the diff between expected and detected words, labelling each one."""
    expected = tokenize(expected_text)
    detected_tokens = [_first_token(d["word"]) for d in detected]

    results: List[WordResult] = []
    prev_end: Optional[float] = None
    matcher = difflib.SequenceMatcher(a=expected, b=detected_tokens, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                d = detected[j]
                gap = (d["start"] - prev_end) if prev_end is not None else 0.0
                results.append(
                    WordResult(
                        index=i,
                        expected=expected[i],
                        detected=d["word"],
                        status=STATUS_UNCLEAR if d["prob"] < low_confidence else STATUS_OK,
                        confidence=d["prob"],
                        slow=is_slow(d, j),
                        paused_before=gap >= hesitation_gap_seconds,
                        start=d["start"],
                        end=d["end"],
                    )
                )
                prev_end = d["end"]

        elif tag == "replace":
            # The two sides are often different lengths (an omission sitting next to a
            # substitution, say). Pair up what lines up and label the remainder by
            # whichever side ran out, rather than forcing it all into "substitution".
            exp_slice = expected[i1:i2]
            det_slice = detected[j1:j2]
            for k in range(max(len(exp_slice), len(det_slice))):
                if k < len(exp_slice) and k < len(det_slice):
                    d = det_slice[k]
                    results.append(
                        WordResult(
                            index=i1 + k,
                            expected=exp_slice[k],
                            detected=d["word"],
                            status=STATUS_SUBSTITUTION,
                            confidence=d["prob"],
                            slow=is_slow(d, j1 + k),
                            paused_before=False,
                            start=d["start"],
                            end=d["end"],
                        )
                    )
                elif k < len(exp_slice):
                    results.append(_omission(i1 + k, exp_slice[k]))
                else:
                    d = det_slice[k]
                    results.append(_extra(i2, d, is_slow(d, j1 + k)))
            if det_slice:
                prev_end = det_slice[-1]["end"]

        elif tag == "delete":
            for i in range(i1, i2):
                results.append(_omission(i, expected[i]))

        elif tag == "insert":
            for j in range(j1, j2):
                d = detected[j]
                results.append(_extra(i1, d, is_slow(d, j)))
                prev_end = d["end"]

    return results


def _first_token(word: str) -> str:
    tokens = tokenize(word)
    return tokens[0] if tokens else ""


def _omission(index: int, expected: str) -> WordResult:
    return WordResult(
        index=index,
        expected=expected,
        detected=None,
        status=STATUS_OMISSION,
        confidence=None,
        slow=False,
        paused_before=False,
    )


def _extra(index: int, detected: DetectedWord, slow: bool) -> WordResult:
    return WordResult(
        index=index,
        expected="",
        detected=detected["word"],
        status=STATUS_EXTRA,
        confidence=detected["prob"],
        slow=slow,
        paused_before=False,
        start=detected["start"],
        end=detected["end"],
    )
