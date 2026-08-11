"""The consolidation pass (PRODUCT.md §5.4, §11.4, §11.10).

    STM + persisted rows  ->  candidates  ->  salience  ->  typed, embedded LTM

This is the module the day's work is for. It runs once per sitting, after the
child has gone, and it decides what the companion will still know next year.

**It runs with nobody waiting, and that shapes every decision here.** Session-end
returns before this starts (`orchestrator.end_adventure` enqueues it on commit),
so it may take seconds, call a model, call an embedding provider, and fail parts
of itself without anyone noticing. The flip side is that nothing here may *assume*
it will be retried into success: every step degrades to a worse-but-correct
outcome rather than aborting, because a session consolidated badly is recoverable
and a session never consolidated is a hole in a child's history.

**Two candidate sources, one scorer.** `candidates` reads the columns; `prompts`
asks a model. Both produce `salience.Candidate`, both go through one `select`
pass, and the model half is optional — a provider outage costs the subtle
memories and keeps every factual one. That asymmetry is deliberate: the memories
§11.5 calls first-class are the ones rules find.

**Idempotent, because Celery is at-least-once.** A redelivered task must not
double a child's memories. `PlaySession.consolidated_at` is the gate, and the
near-duplicate filter in `salience.select` is the belt to its braces — a second
run that somehow got past the gate would find its own output in `existing` and
write nothing.

**Nothing here logs memory content.** The log line carries counts, types and
scores, which is everything needed to tune the threshold (§11.4 is not a solved
problem) and nothing that would make the operator log a second copy of a child's
session (§13.8).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.media.adapters import embedding as embedding_adapter
from apps.media.exceptions import MediaError
from apps.memory import candidates as rules
from apps.memory import entities, prompts, salience, stm
from apps.memory.models import Memory
from apps.memory.salience import Candidate, Signals
from apps.story.models import PlaySession

logger = logging.getLogger(__name__)

#: How many of the child's existing memories a candidate is compared against for
#: novelty and duplication. A window rather than the whole history, for two
#: reasons: it is the recent past that makes something feel like a repeat, and a
#: child three years in should not become progressively harder to remember
#: anything new about because their history keeps growing.
NOVELTY_WINDOW = 80

#: How strongly a model-proposed memory counts as child-initiated when it names
#: something the child actually said or picked, and when it does not. The second
#: number is low on purpose: a proposal about our own narration is our content
#: being remembered back at us, and it has to clear the threshold on feeling
#: alone.
MODEL_ECHOES_CHILD = rules.SPOKE
MODEL_ECHOES_US = 0.2

#: How much of a skill milestone a *model*-proposed `skill_moment` is taken to be.
#: Middling, and deliberately below what the rules half awards: the model can see
#: that something was attempted, but only the columns know whether it was unaided
#: and whether it matched what the beat expected.
MODEL_MILESTONE = 0.6


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one consolidation did. Returned for the Celery result row and tests."""

    status: str
    considered: int = 0
    written: int = 0
    embedded: int = 0

    def __str__(self) -> str:
        return f"{self.status} considered={self.considered} written={self.written}"


def consolidate_session(session: PlaySession) -> Outcome:
    """Score this sitting's candidates and write the salient ones as memories.

    Returns an `Outcome` rather than raising for the ordinary failure modes, so
    the task above can record what happened without a `try` around everything.
    """
    if session.consolidated_at is not None:
        logger.info("memory.consolidate_noop session=%s reason=already", session.pk)
        return Outcome("already")

    child = session.child
    log = stm.events(session.pk)

    # The log is read once and handed to both halves. Two calls would be two Redis
    # round trips for the same list, and — worse — could disagree if the TTL expired
    # between them, leaving the rules half richer than the digest the model saw.
    proposals = rules.from_session(session, log=log)
    proposals.extend(_model_candidates(session, log))

    if not proposals:
        # A sitting that produced nothing to consider. Still stamped and still
        # discarded: "we looked and there was nothing" is a completed
        # consolidation, and leaving it unstamped would make it a permanent retry.
        _finish(session)
        logger.info("memory.consolidate session=%s candidates=0 written=0", session.pk)
        return Outcome("empty")

    existing = list(
        Memory.objects.filter(child=child, deleted_at__isnull=True)
        .order_by("-created_at")
        .values_list("content", flat=True)[:NOVELTY_WINDOW]
    )
    selection = salience.select(proposals, existing=existing)

    written, embedded = _write(child, selection.kept)
    _finish(session)

    # One line, and the numbers in it are the only window anyone has on whether
    # the threshold is right. `top_dropped` is the honest tuning signal: a run
    # where the best rejected candidate scored 0.49 is a run to look at.
    logger.info(
        "memory.consolidate session=%s child=%s candidates=%d kept=%d duplicate=%d "
        "dropped=%d embedded=%d types=%s top_dropped=%.2f threshold=%.2f",
        session.pk,
        child.pk,
        selection.considered,
        written,
        len(selection.duplicates),
        len(selection.dropped),
        embedded,
        ",".join(sorted({candidate.type for candidate in selection.kept})) or "-",
        max((candidate.score for candidate in selection.dropped), default=0.0),
        salience.threshold(),
    )
    return Outcome("consolidated", selection.considered, written, embedded)


def _model_candidates(session: PlaySession, log: list[dict[str, Any]]) -> list[Candidate]:
    """The model half, as scored candidates. Empty when the provider is unreachable.

    The signals are derived here rather than asked for, because §11.4's scoring is
    ours (see `prompts.MemoryProposal`). Two come from the proposal itself — the
    emotional intensity from its valence, the milestone from its type — one from
    checking whether it names something the child actually said or picked, one from
    how much of the sitting its entities ran through, and novelty from `select`.
    """
    digest = prompts.session_digest(session, log)
    try:
        proposed = prompts.propose(session.child, digest)
    except prompts.ConsolidationUnavailable as exc:
        # Warning, not exception: this is an expected degradation with a known
        # cost (the subtle memories), and a stack trace every night in an
        # offline development environment would train everyone to ignore it.
        logger.warning("memory.consolidate_model_unavailable session=%s reason=%s", session.pk, exc)
        return []

    initiated = prompts.child_initiated_text(session)
    counts, beats = rules.recurrence(session)

    built: list[Candidate] = []
    for proposal in proposed:
        content = str(proposal.content).strip()
        # The second of the two write paths into §11.7's graph, and the one that
        # forked it: a model asked for "namespaced keys" answers `companion:Ruby`
        # when the companion is called Ruby. Same normaliser as the rules half, so
        # there is one definition of what a key is rather than two.
        entity_keys = entities.normalise(
            [str(entity) for entity in proposal.entities], context=f"session={session.pk}"
        )
        valence = max(-1.0, min(1.0, float(proposal.valence)))
        built.append(
            Candidate(
                type=str(proposal.type).strip().lower(),
                content=content,
                entities=entity_keys,
                valence=valence,
                signals=Signals(
                    child_initiated=(
                        MODEL_ECHOES_CHILD if _echoes_child(content, initiated) else MODEL_ECHOES_US
                    ),
                    # Intensity from the magnitude of the tone, direction kept
                    # separately on `valence`. A frightened memory and a delighted
                    # one are equally worth keeping and must be *used* very
                    # differently (§11.6), which is exactly what two fields buy.
                    emotional_intensity=abs(valence),
                    skill_milestone=(MODEL_MILESTONE if proposal.type == "skill_moment" else 0.0),
                    repetition=rules.repetition_of(entity_keys, counts, beats),
                ),
                origin="model",
                source_adventure_id=_first_adventure_id(session),
            )
        )
    return built


def _echoes_child(content: str, initiated: list[str]) -> bool:
    """Does this proposal name something the child said or chose?

    Substring matching on the child's own phrases, which is crude and right: the
    phrases are short (a name, a label from a card), and the question is only
    whether the model is talking about the child's contribution or about ours. A
    fuzzy match here would start crediting our narration to the child, which is
    the one direction this must not fail in.
    """
    haystack = content.lower()
    for phrase in initiated:
        # `said: sparkle` / `chose: Follow the sparkling river` -> the words after
        # the label, which is what could plausibly appear in a memory sentence.
        _, _, words = phrase.partition(": ")
        candidate = (words or phrase).strip()
        if len(candidate) > 2 and candidate in haystack:
            return True
    return False


def _write(child: Any, kept: list[Candidate]) -> tuple[int, int]:
    """Persist the chosen candidates. Returns `(written, embedded)`.

    Embedded one at a time and *before* the transaction opens, so a slow provider
    is not holding a write transaction open, and so a provider that fails halfway
    through still lets every memory be written — with the vectors it managed and
    nulls for the rest (see `media.adapters.embedding` on why a memory without a
    vector is still worth having).

    **We embed the gist, not the stored sentence, and the difference is deliberate.**
    `content` is `<what the child did> — <the beat's first clause>`, and the anchor
    after the dash is scenery we append so the sentence reads in context. Two of
    this module's questions already agree that the anchor is not what the memory is
    about: `salience._signature` strips it before asking "is this a duplicate", and
    strips it again before asking "is this novel". The vector is the third question
    — "what is this relevant to" (§19.4) — and it was the one still reading the
    whole sentence.

    That inconsistency is measurable rather than theoretical. In the founder's data
    the nearest neighbour of *counted 3 all by themselves* was *chose "Follow more
    tracks"* — a different act, retrieved as near-identical because both end with
    "Ruby rolls up beside you in the warm sand" — while the genuinely related
    counting memory sat three times further away. Day 9 ranks retrieval on this
    vector, so a handle that encodes the set dressing would surface the wrong
    memory and do it confidently.

    The alternative considered was embedding both — the gist for relevance, the
    whole sentence for colour — and rejected as two vectors, two columns and two
    ranking weights to buy nothing anyone asked for yet. The anchor is still
    *stored*, still shown to the parent, and still sent to the model at recall
    time; it is only not part of what the memory is indexed by.
    """
    if not kept:
        return 0, 0

    vectors: list[list[float] | None] = []
    provider: Any = None
    for candidate in kept:
        try:
            if provider is None:
                provider = embedding_adapter.select_embedding_provider()
            vectors.append(embedding_adapter.embed(salience.gist(candidate.content), provider))
        except MediaError as exc:
            # Once per candidate rather than once per run: the second failure is
            # usually the same cause, but "three of five memories have no vector"
            # is a different operational fact from "none of them do".
            logger.warning("memory.embed_failed reason=%s", exc)
            vectors.append(None)

    rows = [
        Memory(
            child=child,
            type=candidate.type,
            content=candidate.content,
            embedding=vector,
            salience=candidate.score,
            valence=candidate.valence,
            entities=candidate.entities,
            source_adventure_id=candidate.source_adventure_id,
        )
        for candidate, vector in zip(kept, vectors, strict=True)
    ]
    with transaction.atomic():
        Memory.objects.bulk_create(rows)

    if provider is not None:
        logger.info(
            "memory.embedded provider=%s count=%d",
            embedding_adapter.provider_label(provider),
            sum(1 for vector in vectors if vector is not None),
        )
    return len(rows), sum(1 for vector in vectors if vector is not None)


def _finish(session: PlaySession) -> None:
    """Stamp the sitting consolidated and throw the working memory away (§11.2).

    In that order, and both unconditionally. The stamp is what makes a redelivered
    task a no-op; the discard is what makes STM ephemeral rather than merely
    expiring. A discard that failed (Redis unreachable) leaves the TTL as the
    backstop, which is why `stm.discard` reports rather than raises.
    """
    session.consolidated_at = timezone.now()
    session.save(update_fields=["consolidated_at", "updated_at"])
    stm.discard(session.pk)


def _first_adventure_id(session: PlaySession) -> Any:
    """The adventure a model-proposed memory is attributed to.

    A sitting holds one adventure today (`orchestrator.create_adventure` opens a
    session per adventure), so this is exact. §17.2 permits several, and when the
    session cap lands and one sitting spans two stories a model proposal will not
    say which — the first is then a documented approximation rather than a silent
    one, and `source_adventure` is a provenance pointer rather than anything
    retrieval keys on.
    """
    return next((adventure.pk for adventure in session.adventures.all()), None)
