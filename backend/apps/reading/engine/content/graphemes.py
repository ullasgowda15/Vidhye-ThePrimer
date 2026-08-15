"""
Breaking a word into the chunks a child is taught to see.

Sounding out is the central skill of early reading, and doing it properly means
treating "sh" as one thing, not two letters. A child taught to say "ess-aitch-i-p"
has been taught something actively wrong.

This is grapheme segmentation - letters as they map to sounds - not phonemes.
It's what a tutor points at on the page.
"""

import re
from typing import List

# Letter groups that make a single sound. Longest first, so "tch" is found
# before "ch" and "igh" before "gh".
_DIGRAPHS = [
    "tch", "dge", "igh",
    "sh", "ch", "th", "wh", "ph", "ck", "ng", "qu",
    "ai", "ay", "ee", "ea", "oa", "oo", "ou", "ow", "oi", "oy", "ie", "ue",
    "ar", "er", "ir", "or", "ur",
]


def segment(word: str) -> List[str]:
    """The chunks to sound out, in order.

    "ship"  -> sh, i, p
    "cat"   -> c, a, t
    "flash" -> f, l, a, sh      (blends are separate sounds; digraphs aren't)
    "cake"  -> c, a, k, e       (silent e is its own chunk - see silent_e_in)
    """
    w = word.lower().strip()
    chunks: List[str] = []
    i = 0

    while i < len(w):
        for group in _DIGRAPHS:
            if w.startswith(group, i):
                chunks.append(group)
                i += len(group)
                break
        else:
            chunks.append(w[i])
            i += 1

    return chunks


def silent_e_in(word: str) -> bool:
    """Does this word end in the silent-e pattern? ("cake", "grape", "slide")

    Worth knowing separately because the teaching move is different: you don't
    sound the e, you explain what it does to the vowel before it.
    """
    return bool(re.search(r"[a-z][aeiou][^aeiou]e$", word.lower()))


def sound_out(word: str) -> str:
    """The chunks as something a text-to-speech voice will read sensibly.

    Full stops make the voice pause between chunks, which is exactly the
    deliberate rhythm you want a child to copy. "sh. i. p."
    """
    if silent_e_in(word):
        # Sounding out the silent e teaches the opposite of the rule.
        chunks = segment(word[:-1])
        return ". ".join(chunks) + f". The e is quiet. {word}"
    return ". ".join(segment(word))
