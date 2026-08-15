"""
Phonics skills.

Ordering follows UFLI Foundations (University of Florida Literacy Institute's
free, science-of-reading-aligned scope and sequence), because Common Core names
the skills a child should have but not the order to teach them in. The Common
Core RF code is kept alongside as the assessment reference.

Each skill carries a spelling pattern so a missed word can be traced back to the
skill it exercises, without hand-tagging every word in every passage.
"""

import re
from typing import Dict, List, Optional, TypedDict


class Skill(TypedDict):
    name: str
    ufli_order: int
    common_core: str
    pattern: str
    sound: str  # how to say the target sound aloud, for spoken coaching
    hint: str  # what the child should notice


SKILLS: Dict[str, Skill] = {
    "cvc_short_a": {
        "name": "Short a",
        "ufli_order": 1,
        "common_core": "RF.K.3a",
        "pattern": r"^[^aeiou]a[^aeiou]$",
        "sound": "ah",
        "hint": "the a in the middle says ah",
    },
    "cvc_short_i": {
        "name": "Short i",
        "ufli_order": 2,
        "common_core": "RF.K.3a",
        "pattern": r"^[^aeiou]i[^aeiou]$",
        "sound": "ih",
        "hint": "the i in the middle says ih",
    },
    "cvc_short_o": {
        "name": "Short o",
        "ufli_order": 3,
        "common_core": "RF.K.3a",
        "pattern": r"^[^aeiou]o[^aeiou]$",
        "sound": "oh",
        "hint": "the o in the middle says oh",
    },
    "digraph_sh": {
        "name": "Digraph sh",
        "ufli_order": 4,
        "common_core": "RF.K.3a",
        "pattern": r"sh",
        "sound": "sh",
        "hint": "s and h together say sh",
    },
    "digraph_ch": {
        "name": "Digraph ch",
        "ufli_order": 5,
        "common_core": "RF.K.3a",
        "pattern": r"ch",
        "sound": "ch",
        "hint": "c and h together say ch",
    },
    "digraph_th": {
        "name": "Digraph th",
        "ufli_order": 6,
        "common_core": "RF.K.3a",
        "pattern": r"th",
        "sound": "th",
        "hint": "t and h together say th",
    },
    "blends_l": {
        "name": "L-blends",
        "ufli_order": 7,
        "common_core": "RF.1.3b",
        "pattern": r"^(bl|cl|fl|gl|pl|sl)",
        "sound": "",
        "hint": "both letters at the start blend together",
    },
    "blends_s": {
        "name": "S-blends",
        "ufli_order": 8,
        "common_core": "RF.1.3b",
        "pattern": r"^(sc|sk|sm|sn|sp|st|sw)",
        "sound": "",
        "hint": "both letters at the start blend together",
    },
    "vce_long_a": {
        "name": "Silent e",
        "ufli_order": 9,
        "common_core": "RF.1.3c",
        "pattern": r"a[^aeiou]e$",
        "sound": "ay",
        "hint": "the e at the end is silent, and it makes the a say its name",
    },
    "vowel_team_ai": {
        "name": "Vowel team ai",
        "ufli_order": 10,
        "common_core": "RF.1.3c",
        "pattern": r"ai",
        "sound": "ay",
        "hint": "a and i together say ay",
    },
}

# High-frequency words that don't follow the patterns they appear to. "the" looks
# like a th-digraph word but is taught by sight because its vowel is irregular -
# stopping a child to sound it out would be teaching the wrong thing. Decodable
# th words like "this", "then" and "path" are still fair teaching targets.
SIGHT_WORDS = {
    "the", "a", "of", "to", "was", "said", "are", "you", "your", "they",
    "some", "come", "one", "two", "do", "does", "what", "were", "there",
    "where", "who", "have", "give", "live", "been",
}

# Most specific first: "ship" should match the sh digraph, not short i.
_MATCH_ORDER = [
    "vowel_team_ai",
    "vce_long_a",
    "digraph_sh",
    "digraph_ch",
    "digraph_th",
    "blends_l",
    "blends_s",
    "cvc_short_a",
    "cvc_short_i",
    "cvc_short_o",
]


def get(skill_id: str) -> Optional[Skill]:
    return SKILLS.get(skill_id)


def name_of(skill_id: str) -> str:
    skill = SKILLS.get(skill_id)
    return skill["name"] if skill else skill_id


def skill_for_word(word: str) -> Optional[str]:
    """Which skill does this word exercise? None if it isn't a teaching target -
    sight words and plain function words like "on" aren't."""
    w = word.lower()
    if w in SIGHT_WORDS:
        return None
    for skill_id in _MATCH_ORDER:
        if re.search(SKILLS[skill_id]["pattern"], w):
            return skill_id
    return None


def in_sequence() -> List[str]:
    """Skill ids in UFLI teaching order."""
    return sorted(SKILLS, key=lambda s: SKILLS[s]["ufli_order"])
