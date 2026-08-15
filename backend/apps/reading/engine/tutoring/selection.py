"""
Choosing what to read next.

This is the "adaptive" part, and for now it is deliberately simple: work on the
weakest skill the child has shown, and if there's nothing weak, move forward
through the UFLI sequence. Simple, but genuinely responsive - two children who
made different mistakes get different next passages.
"""

from typing import List, Optional

from ..content import passages, skills
from .session import MASTERED, Session, urgency


def next_skill(session: Session) -> Optional[str]:
    """What should this child practise next?

    The adaptive decision, and the only part of selection the engine should
    own. Which *passage* satisfies it depends on what's in the library and what
    they've already read, which the engine can't see.

    Shore up the weakest unmastered skill; if nothing is weak, move forward
    through the teaching sequence.
    """
    weak = _weakest_skill(session)
    if weak:
        return weak

    for skill_id in skills.in_sequence():
        state = session.skill_states.get(skill_id)
        if state is None or state.status != MASTERED:
            return skill_id
    return None


def next_passage(session: Session) -> Optional[passages.Passage]:
    """Pick from the built-in bank. Used when there's no library to draw on."""
    unread = [p for p in passages.all_passages() if p["id"] not in session.passages_read]
    if not unread:
        # Nothing new left - revisit rather than stopping.
        unread = passages.all_passages()

    target = next_skill(session)
    if target:
        targeted = [p for p in unread if target in p["skills"]]
        if targeted:
            return targeted[0]

    return _next_in_sequence(session, unread)


def _weakest_skill(session: Session) -> Optional[str]:
    """The skill most in need of practice.

    Any skill the child has got wrong and not yet mastered counts - waiting for
    a skill to look statistically bad before revisiting it would mean ignoring
    the mistake they just made, which is the opposite of what a tutor does.

    Ranked by how often they get it wrong rather than how many times, so a
    sound they've never once managed outranks one they mostly get right but
    have simply seen more often. Ties go to the earlier skill in the sequence,
    since later ones build on it.
    """
    struggling = [
        s
        for s in session.skill_states.values()
        if s.errors > 0 and s.status != MASTERED
    ]
    if not struggling:
        return None

    def rank(state):
        skill = skills.get(state.skill_id)
        return (
            -urgency(state.attempts, state.errors),
            skill["ufli_order"] if skill else 99,
        )

    return min(struggling, key=rank).skill_id


def _next_in_sequence(session: Session, candidates: List[passages.Passage]) -> Optional[passages.Passage]:
    """Earliest UFLI skill the child hasn't mastered yet."""
    for skill_id in skills.in_sequence():
        state = session.skill_states.get(skill_id)
        if state and state.status == MASTERED:
            continue
        for passage in candidates:
            if skill_id in passage["skills"]:
                return passage
    return candidates[0] if candidates else None
