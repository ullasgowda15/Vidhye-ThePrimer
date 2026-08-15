"""
Which phoneme differences actually mean the child misread the word.

Not all of them do. An acoustic model and a pronunciation dictionary disagree
constantly about vowel quality - "has" is written as /hɐz/ in one and heard as
/hæz/ in the other, and both are the same word said the same way. On top of
that, real speakers differ by dialect: Indian English realises "th" as a dental
stop, many accents merge vowel pairs entirely. None of that is a reading error.

So the rule is: only differences that indicate the child decoded the *letters*
wrongly count. Everything else is how people talk.
"""

from typing import Dict, Set

# Vowels that routinely stand in for one another between dictionary and
# acoustic model, or across accents. Differences inside a group are ignored.
_VOWEL_GROUPS = [
    {"ə", "ɐ", "ʌ", "ɜ", "ɚ", "ɝ"},  # reduced / central
    {"æ", "a", "ɐ", "ɑ", "ɑː", "ɒ"},  # low
    {"ɛ", "e", "eɪ", "ɪ"},  # front mid
    {"ɪ", "i", "iː", "ɨ"},  # front high
    {"ʊ", "u", "uː", "ʉ"},  # back high
    {"ɔ", "ɔː", "o", "oʊ", "ɒ", "ɑ", "ʌ"},  # back mid, incl. the cot/cut variation
]

# Deliberate consequence of the groups above: vowel swaps are largely tolerated,
# so the phoneme lane will not catch "pat" read as "pot". That is accepted - the
# word-level lane already sees such a swap as a plain substitution, because it
# produces a different real word. This lane exists for what word-level misses:
# which *sound* broke, and mispronunciations that aren't real words. Vowels are
# too unreliable across accents to carry that load; consonants and digraphs are
# where the phonics signal actually lives, and where K-1 teaching concentrates.

# Consonant substitutions that are dialect, not misreading. The th pair is the
# big one: a dental stop for "th" is standard in Indian English and common in
# many other varieties. A child reading "path" that way has decoded it.
_CONSONANT_GROUPS = [
    {"θ", "t̪", "t", "s̪"},  # voiceless th
    {"ð", "d̪", "d", "z̪"},  # voiced th
    {"v", "w", "ʋ"},  # v/w merger
    {"ʈ", "t"},  # retroflex / alveolar t
    {"ɖ", "d"},
    {"ɭ", "l"},
    {"ɾ", "r", "ɹ", "ɽ"},  # rhotic variation
    {"ŋ", "n"},
    {"z", "s"},  # final devoicing
]

# Length and stress marks carry no decoding information.
_STRIP = "ːˈˌ̪̥̬͡ "


def _build(groups) -> Dict[str, Set[str]]:
    table: Dict[str, Set[str]] = {}
    for group in groups:
        for member in group:
            table.setdefault(member, set()).update(group)
    return table


_EQUIVALENT = _build(_VOWEL_GROUPS + _CONSONANT_GROUPS)


def normalise(phoneme: str) -> str:
    """Drop diacritics that don't change which letter-sound was produced."""
    return "".join(c for c in phoneme if c not in _STRIP) or phoneme


def same_sound(expected: str, heard: str) -> bool:
    """Is this difference a misreading, or just how the child speaks?"""
    e, h = normalise(expected), normalise(heard)
    if e == h:
        return True
    return h in _EQUIVALENT.get(e, set())


def is_vowel(phoneme: str) -> bool:
    p = normalise(phoneme)
    return bool(p) and p[0] in "aeiouæɑɐəɛɪɔʊʌʉɜɚɝ"
