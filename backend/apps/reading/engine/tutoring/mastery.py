"""
How well does this child know a skill, and which one needs work most?

Two rules, kept here because they were previously written twice - once in the
engine and once in the Django model - and two copies of a rule is one copy too
many when they can drift apart silently.
"""

# Mastery states, in order of confidence.
UNSEEN = "unseen"
SHAKY = "shaky"
PRACTICING = "practicing"
MASTERED = "mastered"

CLEAN_READS_TO_MASTER = 2

# A skill wrong more often than right needs work before anything built on it.
SHAKY_ERROR_RATE = 0.5

# Mastery needs a decent overall record, not just a recent streak.
MASTERY_ERROR_RATE = 0.3

# Enough readings to mean anything. Two or three correct reads is noise - and a
# single sentence can supply that on its own, since "She will wash the dish"
# contains three sh words. Around ten items per pattern is what phonics
# screeners use, and it's reachable within a few sessions.
MIN_ATTEMPTS_TO_MASTER = 10

# ...spread across different words. Ten clean reads of the same two words says
# they've memorised those words, not that they can decode the pattern. A child
# can be fluent with sh at the start of a word and stuck on it at the end.
MIN_DISTINCT_WORDS_TO_MASTER = 4


def status(
    attempts: int,
    errors: int,
    consecutive_clean: int,
    distinct_clean_words: int = 0,
) -> str:
    """Where this child stands with one skill.

    Mastery is deliberately hard to claim. It needs four things at once:

      - enough readings to be evidence rather than noise
      - across enough different words to be the pattern, not memorised words
      - a good overall record
      - and recent success

    A poor record is checked first, so no streak can paper over it.

    `distinct_clean_words` defaults to 0 for callers that don't track it, which
    makes mastery unreachable rather than accidentally easy - the safer way for
    a missing argument to fail.
    """
    if attempts == 0:
        return UNSEEN

    error_rate = errors / attempts
    if error_rate > SHAKY_ERROR_RATE:
        return SHAKY

    mastered = (
        attempts >= MIN_ATTEMPTS_TO_MASTER
        and distinct_clean_words >= MIN_DISTINCT_WORDS_TO_MASTER
        and error_rate <= MASTERY_ERROR_RATE
        and consecutive_clean >= CLEAN_READS_TO_MASTER
    )
    return MASTERED if mastered else PRACTICING


def urgency(attempts: int, errors: int) -> float:
    """How badly this skill needs practice - higher is more urgent.

    Raw error count is the wrong signal: it favours whatever the child has seen
    most. Getting 5 of 16 wrong scores worse than getting 4 of 4 wrong, when the
    second child clearly can't read that sound at all.

    Rate alone overreacts to thin evidence - one slip on a brand new skill would
    read as total failure. Smoothing the rate (adding one imagined success and
    one imagined failure) keeps a single mistake in proportion while letting a
    consistent failure rise to the top.
    """
    if attempts == 0:
        return 0.0
    return (errors + 1) / (attempts + 2)
