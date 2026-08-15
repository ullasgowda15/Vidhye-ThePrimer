"""
Session state.

One session is one child, one sitting. Everything here lives in memory and dies
with the process - adaptivity needs memory across attempts within a sitting, but
nothing yet needs to survive a restart. Cross-session progress is the point at
which this moves to a real database.
"""

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..content import skills
from .rapport import Rapport
from .mastery import (  # noqa: F401  - re-exported; callers import these from here
    CLEAN_READS_TO_MASTER,
    MASTERED,
    PRACTICING,
    SHAKY,
    UNSEEN,
    status as mastery_status,
    urgency,
)

MAX_TRIES_PER_WORD = 2


@dataclass
class SkillState:
    skill_id: str
    attempts: int = 0
    errors: int = 0
    consecutive_clean: int = 0
    # Which different words they've actually read correctly. Ten clean reads of
    # the same two words is memorisation, not decoding.
    clean_words: Set[str] = field(default_factory=set)

    @property
    def status(self) -> str:
        return mastery_status(
            self.attempts, self.errors, self.consecutive_clean, len(self.clean_words)
        )

    def record(self, correct: bool, word: str = "") -> None:
        self.attempts += 1
        if correct:
            self.consecutive_clean += 1
            if word:
                self.clean_words.add(word.lower())
        else:
            self.errors += 1
            self.consecutive_clean = 0


@dataclass
class Focus:
    """The word currently being worked on, if the child is mid-correction."""

    word: str
    word_index: int
    skill_id: Optional[str]
    passage_id: str
    tries: int = 0
    # Which rung of the teaching ladder we're on - see teaching.py.
    rung: str = "nudge"


@dataclass
class Session:
    id: str
    skill_states: Dict[str, SkillState] = field(default_factory=dict)
    current_passage_id: Optional[str] = None
    focus: Optional[Focus] = None
    passages_read: List[str] = field(default_factory=list)
    # How the last few minutes have gone, which drives whether to model a
    # passage before asking for it and when to stop.
    rapport: Rapport = field(default_factory=Rapport)
    last_fluency: object = None
    # The answer we're waiting to hear, while a comprehension question is open.
    pending_answer: str = ""
    last_answer: object = None
    # Called as (skill_id, word, correct) whenever a skill is credited or
    # faulted.
    # The engine keeps its own in-memory tally either way - this exists so a
    # caller with a database can persist the same decision without
    # reimplementing the rules about what counts as correct.
    on_record: object = None

    def state_for(self, skill_id: str) -> SkillState:
        if skill_id not in self.skill_states:
            self.skill_states[skill_id] = SkillState(skill_id=skill_id)
        return self.skill_states[skill_id]

    def record_word(self, word: str, correct: bool) -> Optional[str]:
        """Credit or fault the skill this word exercises. Returns the skill id,
        or None for words that aren't a teaching target."""
        skill_id = skills.skill_for_word(word)
        if skill_id is None:
            return None
        self.state_for(skill_id).record(correct, word)
        if self.on_record is not None:
            self.on_record(skill_id, word, correct)
        return skill_id

    def report(self) -> List[dict]:
        """Skill-level summary for the parent panel. Patterns, not raw errors -
        "the sh sound, 3 of 4" is actionable in a way a list of words isn't."""
        rows = []
        for skill_id, state in self.skill_states.items():
            if state.attempts == 0:
                continue
            rows.append(
                {
                    "skill_id": skill_id,
                    "name": skills.name_of(skill_id),
                    "attempts": state.attempts,
                    "errors": state.errors,
                    "status": state.status,
                }
            )
        # Weakest first - that's what a parent needs to see.
        rows.sort(key=lambda r: (-r["errors"], r["name"]))
        return rows


def create() -> Session:
    """A blank session, unattached to any store.

    The engine has no memory of its own - callers hydrate one of these from
    wherever they keep state, run the loop, and write back what changed.
    """
    return Session(id=uuid.uuid4().hex)
