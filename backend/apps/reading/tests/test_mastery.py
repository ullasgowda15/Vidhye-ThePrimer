"""When a child may be said to have a phonics skill.

The rule is deliberately hard to satisfy, and the reason is written into the
engine: ten clean reads of the same two words is memorisation, not mastery of a
pattern. These tests pin the four conditions separately, because each one was
added after the previous version claimed mastery it had not earned.
"""

from __future__ import annotations

from apps.reading.engine.tutoring import mastery


def test_a_skill_never_attempted_is_unseen() -> None:
    assert mastery.status(attempts=0, errors=0, consecutive_clean=0) == mastery.UNSEEN


def test_wrong_more_often_than_right_is_shaky() -> None:
    assert mastery.status(attempts=4, errors=3, consecutive_clean=0) == mastery.SHAKY


def test_a_streak_cannot_paper_over_a_bad_record() -> None:
    """The poor-record check runs first, and that ordering is the point.

    A child who has missed this sound two thirds of the time is shaky whatever
    the last few readings looked like. Checked the other way round, a lucky run
    at the end of a session would promote them out of the state that gets them
    the practice they need.
    """
    status = mastery.status(attempts=12, errors=9, consecutive_clean=3, distinct_clean_words=5)

    assert status == mastery.SHAKY


def test_mastery_needs_all_four_conditions() -> None:
    assert (
        mastery.status(attempts=10, errors=1, consecutive_clean=2, distinct_clean_words=4)
        == mastery.MASTERED
    )


def test_ten_clean_reads_of_two_words_is_not_mastery() -> None:
    """The distinct-word requirement, which is the one that matters most.

    A single sentence can supply a streak on its own — "She will wash the dish"
    holds three sh words — so before this rule existed one clean read marked the
    pattern mastered. sh at the start of a word and sh at the end are genuinely
    different difficulties.
    """
    status = mastery.status(attempts=10, errors=0, consecutive_clean=10, distinct_clean_words=2)

    assert status == mastery.PRACTICING


def test_thin_evidence_is_not_mastery_however_clean() -> None:
    """Three perfect readings is not enough to have measured anything."""
    status = mastery.status(attempts=3, errors=0, consecutive_clean=3, distinct_clean_words=3)

    assert status == mastery.PRACTICING


def test_a_recent_error_costs_the_streak_and_the_claim() -> None:
    status = mastery.status(attempts=12, errors=2, consecutive_clean=0, distinct_clean_words=6)

    assert status == mastery.PRACTICING


def test_a_caller_that_forgets_distinct_words_cannot_reach_mastery() -> None:
    """The default fails safe.

    `distinct_clean_words` defaults to 0, so a caller that does not track it
    under-claims rather than over-claims. Telling a child they have mastered
    something they have not is the more expensive direction to be wrong in.
    """
    status = mastery.status(attempts=50, errors=0, consecutive_clean=50)

    assert status == mastery.PRACTICING


# ---------------------------------------------------------------------------
# Urgency — which weak skill to practise first
# ---------------------------------------------------------------------------
def test_a_total_failure_outranks_more_errors_spread_thinner() -> None:
    """4 wrong out of 4 is more urgent than 5 wrong out of 16.

    Raw error count favours whatever the child has seen most, which is the
    opposite of what a tutor does. The second child can read the sound most of
    the time; the first cannot read it at all.
    """
    cannot_do_it = mastery.urgency(attempts=4, errors=4)
    mostly_fine = mastery.urgency(attempts=16, errors=5)

    assert cannot_do_it > mostly_fine


def test_one_slip_on_a_new_skill_is_not_total_failure() -> None:
    """Smoothing keeps a single mistake in proportion.

    Unsmoothed, one-wrong-out-of-one is a 100% error rate and would outrank a
    child who has never got the sound right in three tries. The smoothed rate
    puts them the correct way round.
    """
    one_slip = mastery.urgency(attempts=1, errors=1)
    never_right = mastery.urgency(attempts=3, errors=3)

    assert one_slip < never_right


def test_an_unattempted_skill_is_not_urgent() -> None:
    """Zero attempts is not zero urgency by accident — it is not weak, it is new.

    Selection handles never-seen skills by walking the sequence, not by ranking
    them here.
    """
    assert mastery.urgency(attempts=0, errors=0) == 0.0
