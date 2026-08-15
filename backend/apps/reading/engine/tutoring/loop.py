"""
The tutoring loop.

    (model it if they need it) -> read -> find the break -> teach it ->
    try again -> confirm -> pick what's next from what they missed

The change worth understanding: the loop used to only ever *test*. A child read,
we graded, we hinted, we moved on. A tutor spends most of their time doing the
opposite - reading to the child, breaking words apart, saying them together. So
there are now two things the loop can do that it couldn't before:

  - **Model first.** When a child is struggling, or meeting a sound for the
    first time, the tutor reads the sentence before asking them to.
  - **Climb down a ladder.** A failed word gets a different kind of help each
    time - name the sound, say the word, break it apart, say it in unison -
    rather than the same hint repeated.

The server decides what happens next and hands the client an action to carry
out, so the loop's logic lives in one readable place and the UI stays a thin
executor of instructions.
"""

from typing import Dict, List, Optional

from ..content import passages, skills
from ..detection import STATUS_OMISSION, WordResult, fluency, read_acceptably
from . import brief as briefs
from . import coaching, comprehension, selection, teaching
from .session import SHAKY, Focus, Session

# Action types the client knows how to perform.
PRAISE = "praise"
COACH = "coach"  # tutor asks for another go at one word
TELL = "tell"  # tutor says something; no attempt expected back
REREAD = "reread"
LISTEN_FIRST = "listen_first"  # tutor reads the passage, child then reads it
MOVE_ON = "move_on"
ASK = "ask"  # a question about what they just read
FINISHED = "finished"


def _action(kind: str, say: str, **extra) -> Dict:
    return {"type": kind, "say": say, **extra}


def begin_passage(session: Session, passage: passages.Passage) -> Dict:
    """Open a passage - by reading it aloud first, or by handing it straight over.

    This is the adaptive decision most worth having: not *what* to read, but
    whether the child is being taught or tested. A child who is flowing gets the
    sentence and no interference. A child who is struggling, or who is meeting a
    sound for the first time, hears it read properly before being asked to
    perform it.
    """
    targets = passage.get("skills", [])
    new_skill = any(session.state_for(s).attempts == 0 for s in targets)
    weak_skill = any(session.state_for(s).status == SHAKY for s in targets)

    if session.rapport.wants_modelling(new_skill=new_skill, weak_skill=weak_skill):
        return _action(LISTEN_FIRST, teaching.echo_prompt(passage["text"]))

    return _action(TELL, coaching.opening(), expects_attempt=True)


def on_passage_read(session: Session, passage: passages.Passage, words: List[WordResult]) -> Dict:
    """The child has read the passage. Praise it, or teach one word of it."""
    _record_reading(session, words)

    if passage["id"] not in session.passages_read:
        session.passages_read.append(passage["id"])

    reading = fluency.measure(words)
    focus_word = coaching.choose_focus(words)
    session.rapport.record(needed_help=focus_word is not None, laboured=reading.laboured)
    session.last_fluency = reading

    if focus_word is None:
        session.focus = None
        line = coaching.say(briefs.build(session, coaching.PRAISE))
        extra = teaching.encouragement(session.rapport.clean_streak)
        praise = f"{line} {extra}" if extra else line

        # They read it correctly - so now it's worth asking whether they took
        # anything from it. Only now: a child who just fought through the
        # decoding had no attention left for meaning, and testing that would
        # only teach them reading is a series of tests they lose.
        if comprehension.should_ask(
            has_question=bool(passage.get("question")),
            read_cleanly=True,
            struggling=session.rapport.struggling,
        ):
            session.pending_answer = passage["answer"]
            return _action(
                ASK,
                f"{praise} {passage['question']}",
                question=passage["question"],
                wcpm=reading.wcpm,
            )

        return _action(PRAISE, praise, wcpm=reading.wcpm)

    skill_id = skills.skill_for_word(focus_word.expected)
    rung = teaching.first_rung(
        knows_sound=_has_read_this_sound_before(session, skill_id),
        laboured=reading.laboured,
    )

    session.focus = Focus(
        word=focus_word.expected,
        word_index=focus_word.index,
        skill_id=skill_id,
        passage_id=passage["id"],
        tries=0,
        rung=rung,
    )

    return _teach(session, focus_word.status == STATUS_OMISSION,
                  heard=coaching.summary_for_focus(focus_word))


def on_retry(session: Session, correct: bool) -> Dict:
    """The child has had another go at the word being taught."""
    focus = session.focus
    if focus is None:
        return _action(PRAISE, coaching.say(briefs.build(session, coaching.PRAISE)))

    if correct:
        if focus.skill_id:
            session.state_for(focus.skill_id).record(True, focus.word)
            if session.on_record is not None:
                session.on_record(focus.skill_id, focus.word, True)
        praise_line = coaching.say(
            briefs.build(session, coaching.RETRY_SUCCESS, word=focus.word, skill_id=focus.skill_id)
        )
        # Focus stays set - the reread still needs to know which word to check.
        return _action(REREAD, f"{praise_line} {coaching.reread()}")

    # Not a repeat of the same hint: step down to a different kind of help.
    focus.tries += 1
    focus.rung = teaching.next_rung(focus.rung)
    return _teach(session, omitted=False)


def on_answer(session: Session, said: str) -> Dict:
    """The child has answered a question about what they read.

    Never drilled. A wrong answer is told the answer and moves on -
    comprehension isn't fixed by being asked again harder, it's fixed by
    reading more with someone pointing things out. Turning it into a second
    test would only add a way to fail.
    """
    expected = session.pending_answer
    session.pending_answer = ""

    if not expected:
        return _action(MOVE_ON, coaching.say(briefs.build(session, coaching.PRAISE)))

    result = comprehension.check(said, expected)
    session.last_answer = result
    return _action(MOVE_ON, comprehension.respond(result), understood=result.correct)


def on_told(session: Session) -> Dict:
    """The tutor has just said something that expected no answer.

    Reached after saying a word in unison, or letting a hard one go. Either way
    the teaching for that word is finished, and the sentence is worth one more
    read so it ends on the whole thing rather than a fragment.
    """
    focus = session.focus
    session.focus = None
    if focus is None:
        return _action(MOVE_ON, coaching.say(briefs.build(session, coaching.PRAISE)))
    return _action(REREAD, coaching.reread())


def on_reread(session: Session, words: List[WordResult]) -> Dict:
    """The child has read the whole sentence again after working on a word.

    Either way we move on - the point is to consolidate, not to keep testing.
    Nagging a child who has just succeeded is how you lose them.
    """
    focus = session.focus
    session.focus = None

    fixed = True
    if focus is not None:
        for w in words:
            if w.expected == focus.word:
                fixed = read_acceptably(w.status)
                break

    if session.rapport.should_wind_down:
        return _action(FINISHED, coaching.wind_down())

    line = coaching.confirmed() if fixed else coaching.say(briefs.build(session, coaching.PRAISE))
    return _action(MOVE_ON, line)


def next_passage(session: Session) -> Optional[passages.Passage]:
    passage = selection.next_passage(session)
    session.current_passage_id = passage["id"] if passage else None
    return passage


def _teach(session: Session, omitted: bool, heard: Optional[str] = None) -> Dict:
    """Deliver the current rung of help for the word in focus."""
    focus = session.focus
    move = teaching.move(focus.rung, focus.word, focus.skill_id, omitted=omitted)

    if not move.expects_attempt:
        # Unison, or letting it go - the client says this and comes back for
        # what's next rather than listening for an answer.
        if focus.rung == teaching.RELEASE and focus.skill_id:
            session.state_for(focus.skill_id).record(False, focus.word)
            if session.on_record is not None:
                session.on_record(focus.skill_id, focus.word, False)
        return _action(TELL, move.say, word=focus.word, word_index=focus.word_index)

    return _action(
        COACH,
        move.say,
        word=focus.word,
        word_index=focus.word_index,
        rung=focus.rung,
        **({"heard": heard} if heard else {}),
    )


def _has_read_this_sound_before(session: Session, skill_id: Optional[str]) -> bool:
    if not skill_id:
        return False
    state = session.skill_states.get(skill_id)
    return bool(state and state.attempts - state.errors > 0)


def _record_reading(session: Session, words: List[WordResult]) -> None:
    """Credit every teaching word in the passage, so mastery reflects the whole
    read and not just the one word we stopped on."""
    for w in words:
        if not w.expected:
            continue
        session.record_word(w.expected, correct=read_acceptably(w.status))
