"""Inviting a child to read, and what to do with what comes back.

Phase 1 of Docs/Build/Reading-Integration.md. Three jobs, and the restraint in
each of them is the design:

**Choose a line.** From the curated decodable bank the reading engine already
carries, not from the story. A generated sentence has no decodability guarantee
— Vidhye's own architecture notes are blunt that a model asked for "a sentence
using only short-a and sh" will slip in words the child cannot read — and Phase 4
is where a generated line gets validated before it is served. Until then the
thirteen hand-written ones are the honest option, and they have the property the
corpus needs anyway: we know exactly what the child was looking at.

**Decide whether to ask at all.** Four gates, all of which must pass, listed in
`may_read`. The interesting one is `pre_reader`: it has been on the `Child` model
since Day 1, exposed in the API and the data export, and nothing has ever read
it. A parent has already told us whether this child can read. Asking a child who
cannot to read a sentence is the single worst thing this feature could do.

**Handle the audio.** By default, by not handling it — the bytes are counted and
dropped, and no file is created. Only a child whose parent has opted in has a
recording written, and only to a directory outside the repository.

Nothing here scores anything. There is no verdict to record and no model to load;
`apps.reading.engine`'s detection lane stays untouched until Phase 2, which is
the phase that has real child speech to tune it against.
"""

from __future__ import annotations

import logging
import random
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.profiles.models import Child
from apps.profiles.settings import settings_for
from apps.reading.engine.content import passages
from apps.reading.models import ReadingAttempt

logger = logging.getLogger(__name__)

#: The youngest we will ever invite. Below this the answer is no whatever else is
#: configured — `Child.age_band` splits at the same place, and §5.3 of the
#: programme brief states it as a rule about the product rather than a setting.
MIN_READING_AGE = 5

#: How many words a line may run to.
#:
#: Seven, which is the whole curated bank — every one of the thirteen is five to
#: seven words, because they were written for exactly this age. A first draft set
#: this to six on the reasoning that a first invitation should be the short end,
#: and the effect was to throw away eight of the thirteen: a child reading on
#: consecutive evenings would have met a repeat inside a week, which is the thing
#: `choose_line` exists to avoid. The cap stays as a guard against a longer
#: passage arriving in the bank later, not as a curation rule.
MAX_WORDS = 7


def may_read(child: Child) -> bool:
    """Should this child be offered a line to read?

    Every gate has to pass, and each closes a different way of getting it wrong:

    * **The feature is on.** `READING_ENABLED` is off by default so a deployment
      opts in rather than discovers this.
    * **They are old enough.** Five, matching `age_band`.
    * **A parent says they can read.** `pre_reader` defaults to True, so silence
      means no. Asking a pre-reader to read is the failure this whole feature
      most needs to avoid — it is not a small disappointment to a four-year-old,
      it is being asked to do a thing they cannot do by a friend who should know.
    * **Words are on screen.** A child cannot read a line that is not displayed,
      and `words_on_screen` is already the product's answer to "is text shown".
    """
    if not settings.READING_ENABLED:
        return False
    if child.age < MIN_READING_AGE:
        return False
    if child.pre_reader:
        return False
    return settings_for(child).words_on_screen


def choose_line(child: Child) -> passages.Passage | None:
    """A short decodable line this child has not read recently, or None.

    Prefers something they have not seen. A child handed the same sentence every
    evening learns that nothing they do changes anything, which is the lesson
    Vidhye's selection notes describe discovering the hard way — and it costs one
    query to avoid.
    """
    candidates = [
        item
        for item in passages.all_passages()
        if len(str(item.get("text", "")).split()) <= MAX_WORDS
    ]
    if not candidates:
        # The bank is code, so this is a programming error rather than a state.
        # Returning None keeps it out of the child's way regardless.
        logger.error("reading.no_short_passages max_words=%d", MAX_WORDS)
        return None

    seen = set(
        ReadingAttempt.objects.filter(child=child)
        .order_by("-created_at")
        .values_list("passage_id", flat=True)[:20]
    )
    unseen = [item for item in candidates if item["id"] not in seen]

    # Falling back to the whole set rather than to nothing: a child who has read
    # everything should be offered something, and a repeat beats an absence.
    pool = unseen or candidates
    return random.choice(pool)


def record(
    *,
    child: Child,
    passage_id: str,
    passage_text: str,
    session: Any = None,
    audio: Any = None,
    duration_ms: int | None = None,
) -> ReadingAttempt:
    """Write down that a child read, and keep the audio only if allowed.

    Returns the row. **Never raises on the audio path** — a corpus write failing
    is not a reason for a child's turn to fail, and the row is worth having
    without the recording.
    """
    attempt = ReadingAttempt(
        child=child,
        session=session,
        passage_id=passage_id,
        passage_text=passage_text,
        child_age=child.age,
        spoke=audio is not None,
        duration_ms=duration_ms,
    )

    if audio is not None and child.reading_audio_consent:
        path = _keep(child=child, passage_id=passage_id, audio=audio)
        if path:
            attempt.audio_kept = True
            attempt.audio_path = str(path)

    attempt.save()
    logger.info(
        "reading.attempt child=%s passage=%s spoke=%s kept=%s",
        child.pk,
        passage_id,
        attempt.spoke,
        attempt.audio_kept,
    )
    return attempt


def _keep(*, child: Child, passage_id: str, audio: Any) -> Path | None:
    """Write one consented recording to the corpus. None if it could not be.

    The filename carries the child's id, their age and the passage — the three
    things a tuning run needs to group by — and a uuid so two readings of the
    same sentence cannot collide. It carries no name.
    """
    try:
        root = Path(settings.READING_CORPUS_DIR)
        root.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        name = f"{child.pk}_{child.age}_{passage_id}_{stamp}_{uuid.uuid4().hex[:8]}.webm"
        path = root / name
        with open(path, "wb") as handle:
            for chunk in audio.chunks():
                handle.write(chunk)
        return path
    except Exception:
        # Deliberately swallowed. The alternative is a child whose turn failed
        # because a research corpus could not be written, which inverts whose
        # convenience matters (§13).
        logger.exception("reading.corpus_write_failed child=%s passage=%s", child.pk, passage_id)
        return None


def delete_corpus_for(child: Child) -> int:
    """Remove every kept recording for one child. Returns how many.

    Called when consent is withdrawn and when an account is deleted. The
    disclosure tells parents a deleted family is gone from everywhere, and a
    directory of `.webm` files outside the database is exactly the sort of place
    a promise like that quietly stops being true.
    """
    removed = 0
    rows = ReadingAttempt.objects.filter(child=child, audio_kept=True).exclude(audio_path="")
    for row in rows:
        try:
            Path(row.audio_path).unlink(missing_ok=True)
            removed += 1
        except OSError:
            logger.exception("reading.corpus_delete_failed attempt=%s", row.pk)
    rows.update(audio_kept=False, audio_path="")
    return removed
