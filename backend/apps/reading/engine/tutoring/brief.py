"""
What the tutor knows when it opens its mouth.

A Brief is everything relevant about this moment: the word, the sound, the rule,
and how the session has been going. Templates render it today; a language model
will render it later. Either way the *facts* are decided here, never by the
renderer.

That split is deliberate. Phonics has right and wrong answers - which sound a
digraph makes, whether an e is silent - and a model asked to invent instruction
will occasionally invent it wrongly, invisibly, to a child who can't tell.
Boring instruction is survivable; false instruction is not. So the model gets
handed the facts and writes only the delivery.
"""

from dataclasses import asdict, dataclass
from typing import Optional

from ..content import skills
from .session import MASTERED, Session


@dataclass
class Brief:
    """The facts behind one piece of tutor speech."""

    moment: str  # coach | coach_again | retry_success | praise | reread | give_up
    word: Optional[str] = None
    sound: Optional[str] = None  # how the target sound is said aloud, e.g. "sh"
    rule: Optional[str] = None  # what the child should notice
    skill_name: Optional[str] = None
    heard: Optional[str] = None  # what the child actually said
    missed_sound: Optional[str] = None  # the exact phoneme that didn't come out
    omitted: bool = False
    try_number: int = 0

    # Session context - the part templates can't use but a model can.
    times_missed_today: int = 0
    doing_well: bool = False  # reading cleanly apart from this
    mastered_this_sound_before: bool = False

    def to_dict(self):
        return asdict(self)


def build(
    session: Session,
    moment: str,
    *,
    word: Optional[str] = None,
    skill_id: Optional[str] = None,
    heard: Optional[str] = None,
    missed_sound: Optional[str] = None,
    omitted: bool = False,
    try_number: int = 0,
) -> Brief:
    skill = skills.get(skill_id) if skill_id else None
    state = session.skill_states.get(skill_id) if skill_id else None

    other_skills = [s for sid, s in session.skill_states.items() if sid != skill_id]
    doing_well = bool(other_skills) and all(s.errors == 0 for s in other_skills)

    return Brief(
        moment=moment,
        word=word,
        sound=skill["sound"] if skill and skill["sound"] else None,
        rule=skill["hint"] if skill else None,
        skill_name=skill["name"] if skill else None,
        heard=heard,
        missed_sound=missed_sound,
        omitted=omitted,
        try_number=try_number,
        times_missed_today=state.errors if state else 0,
        doing_well=doing_well,
        mastered_this_sound_before=bool(state and state.status == MASTERED),
    )
