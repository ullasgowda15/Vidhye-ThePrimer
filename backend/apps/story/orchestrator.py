"""The story orchestrator (PRODUCT.md §5.3, §8.1, §16.4).

    plan (once)  ->  beat text  ->  [illustration || voice]  ->  stream

Three decisions shape this module, and all three come from §8.1:

**Beat by beat, never the whole story.** Branching means most pre-generated
content is thrown away, and beat-wise generation is what lets streaming hide the
latency of a language model, an image model and a voice model stacked on top of
each other. A thin *plan* is generated first and held constant so the beats add
up to one story rather than a sequence of moods.

**The text comes out before the pictures.** Narration is the cheap part and the
part the child is waiting for. It is emitted the moment it validates; the
illustration and the voice are launched together and streamed in as they land
(§18.5). This is why the two media calls are `asyncio.gather`-ed rather than
awaited in turn: they are independent, they are the slow half, and running them
in sequence would add the smaller of the two latencies to every beat for no
reason at all.

**Every generation goes through the assembler.** Nothing here writes a safety
rule or a persona line. `apps.safety.prompting.assemble` owns the system layer
for both calls, so a beat and a plan are constrained identically, and the child's
own seeds enter as labelled DATA (§13.5) rather than as instructions.

**The story is a loop, and the loop is driven by the child.** From Day 5 the
orchestrator does not generate "the next beat" — it generates *the beat the child
has not played yet* (`next_position`), conditioned on what they just chose. Two
consequences follow and both are load-bearing: a beat generated ahead of the child
by a worker is replayed rather than skipped, and a beat is never generated for a
branch the child did not take. What bounds the cost of all this is the running
summary (`apps.story.summary`), which keeps the prompt flat across a long
adventure rather than pasting every beat into every request.

What is deliberately *not* here yet: memory retrieval (Days 8-9), the objective
selector (Day 10) and the runtime safety guard (Day 7). Each has a named seam and
a placeholder, so those days are wiring rather than redesign.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db import connection as db_connection
from django.utils import timezone

from apps.companion import service as companion_service
from apps.media import prompts as media_prompts
from apps.media import service as media_service
from apps.media.exceptions import MediaError
from apps.media.hashing import hashes_for
from apps.media.models import Media
from apps.media.style import style_signature
from apps.profiles.models import Child
from apps.safety.prompting import assembler
from apps.safety.prompting.layers import render as render_layers
from apps.story.ai import router as ai_router
from apps.story.ai.prompts import story as story_prompts
from apps.story.ai.schemas import Beat as BeatContract
from apps.story.ai.schemas import InteractionType, StoryPlan, beat_json_schema, json_schema_for
from apps.story.models import Adventure, Beat, Choice, PlaySession
from apps.story.summary import refresh_running_summary

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: The objective every Day-4 adventure carries. The Learning Engine's ZPD
#: selector is Day 10 (§10.3); until then one gentle numeracy objective keeps the
#: learning layer *exercised* rather than absent, which is the difference between
#: a seam and a hole. `difficulty` comes from the adventure, not from here.
PLACEHOLDER_OBJECTIVE: dict[str, Any] = {
    "code": "count_to_3",
    "description": "Count a small group of visible things, up to three, out loud.",
}

#: Used when plan generation cannot be completed. Not a "safe fallback beat"
#: (that is Day 7's output-guard work) — this is the ordinary graceful
#: degradation a first-call failure needs, because a child who tapped the star
#: must get a story and not a 502. It is a *shape*, and the beats generated
#: against it are still personalised, so the cost of landing here is a slightly
#: generic arc rather than a visible failure.
FALLBACK_SKELETON: tuple[str, ...] = (
    "The companion arrives somewhere new and invites the child to look around.",
    "Something small is out of place, and the companion wonders about it aloud.",
    "The two of them try one idea together, and it half works.",
    "The child works out the missing piece and the problem comes right.",
    "Everything is put back the way it should be, and the companion says thank you.",
)

FALLBACK_BRANCH_THEMES: tuple[str, ...] = ("explore further", "help someone else", "head home")

#: Beats are 1-indexed, because `seq` is a position in a story and not an offset.
FIRST_BEAT_SEQ = 1

#: §14.5's "one decision at a time, max 2-3 options", enforced rather than asked
#: for. Four cards on a tablet is four things a four-year-old is holding at once.
MAX_CHOICES = 3

#: How long a beat's media may sit untouched before another stream open assumes
#: the worker that owned it is gone and queues a replacement. Comfortably longer
#: than a slow illustration + voice pair (tens of seconds), because queueing a
#: second copy of work that is merely slow is a double charge.
MEDIA_RETRY_AFTER_S = 120.0

#: How the stream watches for media a worker is producing. The wait backs off from
#: `MIN` to `MAX`; `FOLLOW_TIMEOUT` is when the stream stops waiting and ends with
#: `done` — the child keeps their narration and their companion, and the task is
#: free to finish into the beat for the next open.
MEDIA_POLL_MIN_S = 0.3
MEDIA_POLL_MAX_S = 2.0
MEDIA_POLL_BACKOFF = 1.5
MEDIA_FOLLOW_TIMEOUT_S = 120.0


# ---------------------------------------------------------------------------
# Prompt inputs
# ---------------------------------------------------------------------------
def seed_memories(child: Child) -> list[dict[str, str]]:
    """The child's onboarding seeds, as the memory bundle sees them (§19.1).

    Real retrieval — embedding the context and ranking over `memory` — is Days
    8-9. Until then the seeds a parent typed *are* the bundle, and they enter
    through the same door: the `memory_bundle` layer, labelled DATA, so a parent
    who types "ignore your instructions" into the interests box has written a
    fact about their child and not a command (§13.5).

    Interests come first because they are what makes beat one recognisably *this*
    child's. Fears are included as facts; how a fear may be used is a rule, and
    rules live in the safety layer.
    """
    bundle: list[dict[str, str]] = []
    for interest in child.interests or []:
        bundle.append({"type": "interest", "content": f"{child.first_name} loves {interest}."})
    for fear in child.fears or []:
        bundle.append({"type": "fear", "content": f"{child.first_name} is afraid of {fear}."})
    return bundle


def objective_for(adventure: Adventure) -> dict[str, Any]:
    """The learning directive for this adventure's beats (§10.3, Day 10)."""
    return {**PLACEHOLDER_OBJECTIVE, "difficulty": adventure.difficulty}


def asks_to_count(adventure: Adventure, seq: int) -> bool:
    """Is this the beat that asks the child to count something (§10.3, §8.5)?

    One beat per adventure, at its midpoint. Deliberately decided here rather
    than left to the model: `CHOICE_RULE` tells every other beat to prefer
    `choose`, and a model reading "prefer" on every beat picks it every time —
    which is how the numeracy objective ended up riding in the prompt as data
    nothing acted on, and how §8.5's no-fail-states guarantee ended up with
    nothing that *could* be answered wrongly and so no way to be exercised.

    The midpoint because the opening has to establish the world before it can ask
    anything of the child, and the close asks for nothing at all (§8.8).

    A stand-in for Day 10's selector, not a small version of it: there is no skill
    model here, no spaced retrieval and no difficulty-driven demand. When the
    Learning Engine lands it decides *what* to ask and *when*, and this goes.
    """
    if seq <= 1 or is_final_seq(adventure, seq):
        return False
    return seq == (adventure.estimated_beats + 1) // 2


def _known_characters(child: Child) -> list[str]:
    """Names the model may use as if already established (§8.2).

    Only the companion today. A recurring cast lives in the world state (§8.3),
    which is not built yet — so any *other* character in a beat has to be
    described rather than named, and the prompt says so.
    """
    try:
        return [child.companion.name]
    except ObjectDoesNotExist:
        return []


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
def build_plan(child: Child) -> dict[str, Any]:
    """Generate the adventure skeleton (§8.1).

    Falls back to `FALLBACK_SKELETON` if the provider cannot produce a valid
    plan. Deliberately *not* a raise: this call sits between a child's tap and
    their story, the beats are generated against the plan rather than dictated by
    it, and a generic arc told in the child's own world is a far better outcome
    than an error screen. The fallback is logged at `error` so it is visible in
    operations even though it is invisible to the child.
    """
    layers = assembler.assemble(
        child,
        memory=seed_memories(child),
        objective=PLACEHOLDER_OBJECTIVE,
        plan=None,
        output_schema=json_schema_for(StoryPlan),
    )
    context = {"system": render_layers(layers), "user": story_prompts.PLAN_REQUEST}

    try:
        plan = ai_router.generate_structured(context, model=StoryPlan)
    except ai_router.AIError:
        logger.exception("story.plan_failed child=%s falling_back=1", child.id)
        return _fallback_plan(child)

    logger.info("story.plan child=%s stages=%d", child.id, len(plan.skeleton))
    return plan.model_dump()


def _fallback_plan(child: Child) -> dict[str, Any]:
    interests = list(child.interests or [])
    subject = interests[0] if interests else "a quiet corner of the woods"
    return {
        "title": f"A day with {subject}",
        "skeleton": list(FALLBACK_SKELETON),
        "branch_themes": list(FALLBACK_BRANCH_THEMES),
        # Recorded on the row itself so the parent dashboard's transparency log
        # (§12.6) and any later quality review can tell a planned arc from a
        # degraded one without reading application logs.
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# Starting an adventure
# ---------------------------------------------------------------------------
def create_adventure(child: Child, *, session: PlaySession | None = None) -> Adventure:
    """Persist a new adventure and its plan. Synchronous — the API's entry point.

    Note what this does **not** do: generate beat one. The POST returns as soon
    as the plan is saved so the client can open the stream immediately and start
    the companion's thinking animation; the first beat is generated *inside* the
    stream, where its latency is hidden behind something a child enjoys looking
    at. Generating it here would mean a slow POST followed by an instant stream,
    which is the same total wait with nothing on screen for most of it (§14.5).
    """
    plan = build_plan(child)
    # A sitting begins when a story does, unless the caller already had one. It is
    # created here rather than left null because `end` has to be able to say how
    # long the child played and why they stopped (§13.6) — a wellbeing signal the
    # parent dashboard reports, and one that cannot be reconstructed afterwards.
    session = session or PlaySession.objects.create(child=child)
    adventure = Adventure.objects.create(
        child=child,
        session=session,
        title=str(plan.get("title") or "")[:200],
        plan=plan,
        primary_objective_id=PLACEHOLDER_OBJECTIVE["code"],
    )
    logger.info(
        "story.adventure_started adventure=%s child=%s beats=%d",
        adventure.id,
        child.id,
        adventure.estimated_beats,
    )
    return adventure


async def begin_adventure(child: Child, *, session: PlaySession | None = None) -> Adventure:
    """`create_adventure` for an async caller, off the event loop (§16.4)."""
    return await _offload(create_adventure, child, session=session)


def current_adventure(child: Child) -> Adventure | None:
    """The story this child is in the middle of, if there is one (§18.4).

    "In the middle of" is `status=in_progress` and nothing more subtle. What moves
    an adventure out of that state is `end_adventure` — the child reaching the warm
    close and tapping "all done", or leaving. Until something says so explicitly the
    story stays open, which is the conservative direction: the cost of resuming a
    story the child had drifted away from is that they see the same forest, and the
    cost of the opposite mistake is a second paid-for adventure on every reload.
    """
    return (
        Adventure.objects.select_related("child", "child__companion")
        .filter(child=child, status=Adventure.Status.IN_PROGRESS)
        .first()
    )


def resume_or_create_adventure(
    child: Child,
    *,
    session: PlaySession | None = None,
) -> tuple[Adventure, bool]:
    """This child's in-progress adventure, starting one only if there is none.

    Returns `(adventure, created)`.

    **Why the POST is idempotent.** The client had no way to name the adventure it
    was already in: React Query's cache is memory-only, so a hard refresh lost the
    id and the story page began a brand-new story — a new plan, a new opening beat,
    a new illustration and a new voice line, every reload. The backend's replay path
    was correct and simply never asked. Making "begin" mean "begin **or** rejoin"
    fixes the reload, the StrictMode double-mount and the second device with one
    rule, and it puts the rule where it cannot be forgotten by a client.

    The child row is locked for the check-then-create, because two POSTs landing
    together is exactly the case this exists for and `filter().first()` on its own
    would let both of them find nothing. The lock is held across the plan call,
    which is seconds — deliberately, since the whole point is that the second
    caller waits and is handed the first caller's adventure rather than paying for
    its own. It is scoped to one child, so it can only ever make that child's own
    second request wait.
    """
    with transaction.atomic():
        # The lock is on the child, not the adventure: the row we are protecting
        # against is one that does not exist yet.
        Child.objects.select_for_update().get(pk=child.pk)

        existing = current_adventure(child)
        if existing is not None:
            logger.info(
                "story.adventure_resumed adventure=%s child=%s beats=%d",
                existing.id,
                child.id,
                existing.beats.count(),
            )
            return existing, False

        return create_adventure(child, session=session), True


# ---------------------------------------------------------------------------
# Generating a beat
# ---------------------------------------------------------------------------
def total_beats(adventure: Adventure) -> int:
    """How many beats this adventure runs to.

    The plan's own length, clamped — the same number the client was promised as
    `estimatedBeats` (§18.4), so the progress hint a child's parent sees and the
    point at which the story actually ends cannot disagree.
    """
    return adventure.estimated_beats


def is_final_seq(adventure: Adventure, seq: int) -> bool:
    """Is the beat at this position the warm close (§8.8)?"""
    return seq >= total_beats(adventure)


def next_position(adventure: Adventure) -> tuple[int, Beat | None]:
    """`(the beat to tell next, the beat before it)`.

    "Next" is the **lowest-numbered beat the child has not answered**, not the
    highest one generated. The distinction is what makes prefetch safe: beat k+1
    may already exist because a worker wrote it ahead of time, and a cursor that
    meant "the last row" would jump the child straight to it — they would watch a
    beat they never got to play, and the beat they were on would silently vanish.

    A beat counts as answered once it has a `Choice` against it, which is why
    every interaction records one (`Choice`'s docstring) — including a tap that
    only means "carry on".
    """
    beats = list(adventure.beats.order_by("seq").prefetch_related("choices"))
    if not beats:
        return FIRST_BEAT_SEQ, None

    for index, beat in enumerate(beats):
        if not beat.choices.all():
            return beat.seq, (beats[index - 1] if index else None)
    return beats[-1].seq + 1, beats[-1]


def turn_from(beat: Beat | None) -> dict[str, Any] | None:
    """What the child did on `beat`, as the next beat's prompt needs it (§8.5).

    None when there is nothing to react to — the opening, or a beat generated
    ahead of the child (prefetch). `next_hint` rides along because it is the
    previous beat's own steer and it is stored precisely so it can survive the
    round trip through the child's tap.

    `offered_labels` is the whole set of cards that were on the screen, which the
    prompt does **not** render (`assembler._turn_block` reads named keys and this
    is not one of them — the model must not be told what the child *didn't* pick).
    It is here for the post-write check that reads the two together: a consequence
    that names an option the child left on the table is the P0 this day exists to
    close.
    """
    if beat is None:
        return None
    choice = next(iter(beat.choices.all()), None)
    if choice is None:
        return None

    option = beat.option(choice.chosen_key) or {}
    return {
        "chosen_key": choice.chosen_key,
        "chosen_label": option.get("label") or "",
        "offered_labels": [str(o.get("label") or "") for o in beat.options],
        "value": choice.value,
        "assisted": choice.assisted,
        "retries": choice.retries,
        "consequence": choice.consequence_summary,
        "next_hint": beat.next_hint,
    }


def build_beat_context(
    adventure: Adventure,
    *,
    seq: int,
    prev_choice: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """The assembled prompt for one beat (§19.1). Pure — no model, no I/O but the
    active prompt row.

    Note what is *not* in here: the beats themselves. The prompt carries the plan,
    the bounded running summary (§8.6) and the one turn just taken — so its size
    is flat across an adventure rather than growing with every beat told. See
    `apps.story.summary`.
    """
    child = adventure.child
    skeleton: Sequence[Any] = adventure.plan.get("skeleton") or []
    stage = str(skeleton[seq - 1]) if 0 < seq <= len(skeleton) else ""

    layers = assembler.assemble(
        child,
        memory=seed_memories(child),
        objective=objective_for(adventure),
        plan=adventure.plan,
        running_summary=adventure.running_summary,
        turn=prev_choice,
        output_schema=beat_json_schema(),
    )
    user = story_prompts.beat_request(
        seq=seq,
        beat_id=f"b{seq}",
        is_first=seq == 1,
        is_final=is_final_seq(adventure, seq),
        ask_to_count=asks_to_count(adventure, seq),
        stage=stage,
        prev_choice=prev_choice,
        characters=_known_characters(child),
    )
    return {"system": render_layers(layers), "user": user}


def create_beat(
    adventure: Adventure,
    *,
    seq: int | None = None,
    prev_choice: Mapping[str, Any] | None = None,
) -> Beat:
    """Generate and persist a beat. Synchronous core.

    `seq` names the position being filled, defaulting to "the next free one". A
    caller that means a *specific* position has to say so: `resolve_beat` names
    the position the child is on, and if it asked for "next" instead then two
    streams racing on a fresh adventure would produce a beat 1 and a beat 2 rather
    than colliding on the constraint — the child would be told the opening, then
    told a second opening as though it were what came next.

    Raises `BeatValidationError` when the provider cannot produce a schema-valid
    beat even after the router's one repair attempt (§19.6). The caller — the
    stream — is what turns that into an ending the child can live with; Day 7
    turns it into a safe fallback beat instead.
    """
    seq = _next_seq(adventure) if seq is None else seq
    context = build_beat_context(adventure, seq=seq, prev_choice=prev_choice)

    # The router is built here rather than through the module-level seam so the
    # row can record *who* answered. Which model wrote a beat is not trivia: the
    # misattribution this day fixes is model-dependent, the router has a fallback
    # chain, and a stamp missing from the row is a stamp that cannot be backfilled.
    router = ai_router.ModelRouter.from_settings()
    model = ai_router.model_label(router.select_provider())

    started = time.monotonic()
    contract = router.generate_beat(context)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    try:
        beat = _persist_beat(adventure, seq, contract, model=model)
    except IntegrityError:
        # Another stream on the same adventure wrote this position while we were
        # generating. The unique constraint settles it and we adopt their beat
        # rather than telling the child the opening twice. We have still paid for
        # a generation, which is why `beat_state` checks *before* we get
        # here — this catch is for the narrow window that check cannot close.
        winner = adventure.beats.filter(seq=seq).first()
        if winner is None:
            raise
        logger.info(
            "story.beat_race_lost adventure=%s seq=%d kept=%s", adventure.id, seq, winner.id
        )
        return winner

    logger.info(
        "story.beat adventure=%s beat=%s seq=%d gen_ms=%d model=%s emotion=%s interaction=%s "
        "choices=%d final=%d prompt_chars=%d",
        adventure.id,
        beat.id,
        seq,
        elapsed_ms,
        model,
        contract.emotion,
        (beat.interaction or {}).get("type"),
        len(beat.options),
        int(is_final_seq(adventure, seq)),
        # The evidence for §8.6, in the log a human already reads per beat: this
        # number must stay roughly flat across an adventure rather than climbing
        # with every beat told.
        len(context["system"]) + len(context["user"]),
    )
    # Written, then read back against what the child actually did. Neither check
    # changes the beat (see their section header); both make a defect that used to
    # be found by a parent visible in the log instead.
    _log_consequence_fidelity(beat, prev_choice)
    _log_choice_distinctness(beat)
    # Folded into the bounded summary immediately, so the *next* beat's prompt
    # already knows this one happened without carrying its text (§8.6).
    refresh_running_summary(adventure)
    # The picture, the voice and the choice icons are a worker's job from here
    # (§16.3). Handing them over at the moment the beat is persisted is what stops
    # a client that disconnects mid-generation from leaving a paid-for beat with
    # no media.
    schedule_beat_media(beat)
    return beat


def _normalised(text: str) -> str:
    """Casefolded, whitespace-collapsed text, for comparing two sentences by content."""
    return " ".join(text.split()).casefold()


def dedupe_prompt_audio(narration: str, interaction: dict[str, Any]) -> dict[str, Any]:
    """Drop a `prompt_audio` that only repeats the narration.

    The contract says this field is the short question asked *after* the narration,
    and both are spoken in one clip (`spoken_line`) and captioned separately on
    screen. When a model fills it with the narration over again — which it does,
    because "restate the scene, then ask" is a natural way to write a prompt — the
    child hears the entire beat twice in a row and sees the same sentences twice on
    the page. That is the defect this exists to make impossible.

    Deliberately narrow: only containment either way, after normalising case and
    whitespace. A paraphrase is left alone, because the alternative is a similarity
    threshold that would eventually swallow a legitimate question — and a lost
    question is a child asked nothing, which is worse than one said clumsily. The
    prompt and the field description are what discourage the paraphrase; this is
    what catches the repeat.

    Returns a new mapping; the input is not modified.
    """
    interaction = dict(interaction)
    prompt = str(interaction.get("prompt_audio") or "").strip()
    if not prompt:
        return interaction

    said, asked = _normalised(narration), _normalised(prompt)
    if asked and (asked in said or said in asked):
        interaction["prompt_audio"] = None
    return interaction


def normalise_choices(interaction: dict[str, Any], *, is_final: bool) -> dict[str, Any]:
    """Make a generated interaction one a child's screen can actually present.

    Two rules, and both exist because the client cannot invent its way out of a
    malformed one:

    **A final beat asks for nothing.** Options on the warm close are options that
    lead nowhere — there is no beat after it to generate — so a child would tap a
    card and watch the story end on what looked like a mistake. The prompt asks
    for `type: "none"` (`FINAL_BEAT_RULE`); this is what makes it true. The spoken
    invitation survives, because "shall we come back tomorrow?" is warmth and not
    a question anyone has to answer.

    **At most three options, each with its own key.** §14.5 caps a decision at two
    or three, which is a claim about how much a four-year-old can hold at once and
    not a layout preference. Duplicate keys are dropped rather than merged: two
    cards that report the same tap are two cards the consequence cannot tell
    apart.
    """
    interaction = dict(interaction)

    if is_final:
        if interaction.get("choices"):
            logger.info("story.final_beat_choices_dropped count=%d", len(interaction["choices"]))
        interaction["type"] = InteractionType.NONE.value
        interaction["choices"] = None
        return interaction

    # A beat that asks for a number must not also show cards. The client picks its
    # answer control by the *absence* of options (`StoryPage`: cards when there are
    # options, the number pad when there are none), so options on a `count` beat
    # do not merely clutter it — they replace the only control that can answer it,
    # and the child is asked to count with no way to say three.
    asked = interaction.get("type")
    if asked in {InteractionType.COUNT.value, InteractionType.SAY.value}:
        if interaction.get("choices"):
            logger.info("story.choices_dropped_from_answer_beat type=%s", asked)
        interaction["choices"] = None
        return interaction

    options = interaction.get("choices") or []
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        key = str(option.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(dict(option))
        if len(kept) == MAX_CHOICES:
            break

    if len(kept) != len(options):
        logger.info("story.choices_trimmed offered=%d kept=%d", len(options), len(kept))
    interaction["choices"] = kept or None
    return interaction


# ---------------------------------------------------------------------------
# Consequence fidelity (§8.5) — measured, not blocked
# ---------------------------------------------------------------------------
# Both checks below **log and let the beat through**. Blocking would mean either a
# child staring at a dead screen or a second paid generation on a beat that is
# usually fine, and the defect they watch for is one the prompt is what actually
# fixes (`story_prompts.CONSEQUENCE_RULE`, `CHOICE_RULE`). What they buy is a rate
# an operator can see in the log and a test can assert on — the Day-5 review had to
# read fourteen beats by hand to find that ~21% of them misreported the child.

#: Words that identify nothing on their own. Deliberately short: a choice label is
#: two or three words, so almost everything in it is doing work. Verbs stay in —
#: "look", "count", "wave" are exactly what makes one option a different action
#: from another.
_FUNCTION_WORDS = frozenset(
    """a an and are as at be by for from in into is it its let lets my me of on or
    our over that the their them then there they this to under up us we with you
    your""".split()
)


def _stem(word: str) -> str:
    """A crude singular/present form, enough to match "looked" against "look".

    Not a linguistics exercise: the alternative to three lines of suffix-stripping
    is a stemming dependency for a check whose output is a log line. Words it gets
    wrong ("waving" -> "wav") cost a missed flag, never a blocked beat.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _content_tokens(text: str) -> list[str]:
    """The stemmed words in `text` that could identify one option rather than another."""
    letters = "".join(char if char.isalpha() else " " for char in text.casefold())
    return [_stem(word) for word in letters.split() if word not in _FUNCTION_WORDS]


def _content_words(text: str) -> set[str]:
    """`_content_tokens` as a set, for the comparisons that do not care about order."""
    return set(_content_tokens(text))


def misattributed_options(
    narration: str,
    *,
    chosen_label: str,
    offered_labels: Sequence[str],
) -> list[str]:
    """Options the child did **not** take that this narration appears to be about.

    The failure it names is specific and it reached a child: they tapped "Count the
    spiders" and were told *"Spidey chose to point at the toy elephant"* — a card
    that was never even on the screen. §8.5's whole promise is that the world reacts
    to what *they* did, and a pre-reader cannot argue with a story about themselves.

    Comparison is on each option's **distinctive** words — the ones no other card
    on that screen used. It has to be: every option at the failing decision point
    was about spiders, so the shared noun says nothing about which card the beat is
    telling, and only "count" against "look" against "wave" does. An un-chosen
    option counts as named when its distinctive words turn up in the narration and
    the chosen option's do not, because a beat that names both got it right and
    then mentioned the road not taken.

    A chosen option with *no* distinctive words says nothing either way — "Look up
    the river" against "Look down the river" differ by a preposition, and a beat
    that faithfully narrated one of them would look identical to a beat that
    narrated the other. Those return nothing rather than a guess; they are
    `alike_option_labels`' to report, upstream, before the cards are ever shown.

    Deliberately word-level and therefore approximate in both directions. It will
    miss a substitution written entirely in synonyms, and it will occasionally flag
    a beat that named the chosen action in words of its own while mentioning
    another card's scenery. Both are acceptable for something whose only output is
    a log line.
    """
    said = _content_words(narration)
    labels = list(offered_labels)
    words = {_normalised(label): _content_words(label) for label in labels}
    chosen_key = _normalised(chosen_label)

    def distinctive(key: str) -> set[str]:
        others: set[str] = set()
        for other, other_words in words.items():
            if other != key:
                others |= other_words
        return words.get(key, set()) - others

    tells = distinctive(chosen_key)
    if chosen_key not in words or not tells or tells & said:
        return []

    return [
        label
        for label in labels
        if _normalised(label) != chosen_key and distinctive(_normalised(label)) & said
    ]


def alike_option_labels(options: Sequence[Mapping[str, Any]]) -> list[str]:
    """Labels that are the same action pointed at different things.

    "Count the left frog" / "Count the middle frog" / "Count the right frog" is one
    option written three times (§14.5 asks for two or three *different* things that
    happen next). It matters beyond tidiness: the beat that follows cannot tell
    those taps apart, so it writes whichever one the plan expected — which is how a
    cosmetic choice set turns a possible misattribution into a certain one.

    Matched on the leading content word, which is the verb in every label a model
    writes here. Returns the labels that share one, or an empty list.
    """
    leading: dict[str, list[str]] = {}
    for option in options:
        label = str(option.get("label") or "").strip()
        tokens = _content_tokens(label)
        if tokens:
            leading.setdefault(tokens[0], []).append(label)

    return [label for labels in leading.values() if len(labels) > 1 for label in labels]


def _log_consequence_fidelity(beat: Beat, prev_choice: Mapping[str, Any] | None) -> None:
    """Report a consequence beat that names an action the child did not take."""
    if not prev_choice:
        return
    chosen_label = str(prev_choice.get("chosen_label") or "")
    offered = [str(label) for label in prev_choice.get("offered_labels") or []]
    if not chosen_label or len(offered) < 2:
        return

    named = misattributed_options(beat.narration, chosen_label=chosen_label, offered_labels=offered)
    if named:
        logger.warning(
            "story.consequence_misattributed adventure=%s beat=%s seq=%d chose=%r narrated=%r",
            beat.adventure_id,
            beat.id,
            beat.seq,
            chosen_label,
            named,
        )


def _log_choice_distinctness(beat: Beat) -> None:
    """Report a decision point whose cards are one action in different clothes."""
    if alike := alike_option_labels(beat.options):
        logger.warning(
            "story.choice_options_alike adventure=%s beat=%s seq=%d labels=%r",
            beat.adventure_id,
            beat.id,
            beat.seq,
            alike,
        )


@transaction.atomic
def _persist_beat(
    adventure: Adventure, seq: int, contract: BeatContract, *, model: str = ""
) -> Beat:
    """Write the beat and extend the adventure's emotion arc, together.

    One transaction because the arc (§8.8) is a projection of the beats: an arc
    that has recorded a beat which does not exist would make the wellbeing
    down-shift fire on a story that was never told.

    `model` is who wrote it — `provider:model`, or empty when a caller could not
    say. Stamped at the same moment as the text so the two cannot drift.
    """
    # Normalised here, at the boundary where a model's output becomes our data, so
    # that everything downstream — the voice clip, the caption, the transcript a
    # parent reads, Day 8's memory — trusts one clean record instead of each
    # re-deriving what the contract already promised.
    interaction = dedupe_prompt_audio(
        contract.narration, contract.interaction.model_dump(mode="json")
    )
    if interaction["prompt_audio"] != contract.interaction.prompt_audio:
        logger.info(
            "story.prompt_audio_deduped adventure=%s seq=%d — it repeated the narration",
            adventure.id,
            seq,
        )
    interaction = normalise_choices(interaction, is_final=is_final_seq(adventure, seq))

    beat = Beat.objects.create(
        adventure=adventure,
        seq=seq,
        narration=contract.narration,
        scene=contract.scene.model_dump(mode="json"),
        interaction=interaction,
        learning=contract.learning.model_dump(mode="json"),
        emotion=contract.emotion,
        model=model,
        next_hint=contract.next_hint,
        continuity_refs=list(contract.continuity_refs),
    )
    locked = Adventure.objects.select_for_update().get(pk=adventure.pk)
    locked.emotion_arc = [*locked.emotion_arc, contract.emotion]
    locked.save(update_fields=["emotion_arc", "updated_at"])
    adventure.emotion_arc = list(locked.emotion_arc)
    return beat


def _next_seq(adventure: Adventure) -> int:
    last = adventure.beats.order_by("-seq").values_list("seq", flat=True).first()
    return (last or 0) + 1


async def generate_beat(
    adventure: Adventure,
    *,
    seq: int | None = None,
    prev_choice: Mapping[str, Any] | None = None,
) -> Beat:
    """`create_beat` for an async caller, off the event loop (§16.4).

    The provider SDKs are synchronous, and a several-second blocking call on the
    event loop would stall every other stream on the process — so it runs on its
    own thread with its own database connection.
    """
    return await _offload(create_beat, adventure, seq=seq, prev_choice=prev_choice)


def beat_state(adventure: Adventure, seq: int) -> tuple[Beat, dict[str, str | None]] | None:
    """The beat at this position and its media URLs, if it has been generated.

    `select_related` because the caller is an async generator: following
    `beat.media` lazily from there raises, and the URLs are the only thing it
    wants from those rows.
    """
    beat = adventure.beats.select_related("media", "audio").filter(seq=seq).first()
    if beat is None:
        return None
    return beat, beat_media_urls(beat)


def _servable_url(media: Media | None) -> str | None:
    return media.url if media is not None and media.is_servable else None


def resolve_beat(
    adventure: Adventure,
    *,
    seq: int,
    prev: Beat | None = None,
) -> tuple[Beat, dict[str, str | None]]:
    """The beat at `seq` and its media URLs: replayed if it exists, generated once
    if it does not. Synchronous core of `ensure_beat`.

    Replay is not a nicety. A child who reloads mid-story — which the founder test
    does deliberately, and which a tablet does by itself when the OS evicts a tab —
    must land back in the *same* forest, and must not cost a second plan, beat,
    illustration and voice line to get there. From Day 5 it also covers the beat a
    worker generated ahead of the child: the ordinary path through a branching
    story is a replay, not a generation.
    """
    beat, urls = _beat_or_generate(adventure, seq=seq, prev=prev)

    # An explicit repair rather than an accident. The old code re-ran the media
    # stages whenever a URL was missing, which happened to heal an orphaned beat
    # and happened to double-pay when two clients did it at once. Saying "this
    # beat's media never finished — make sure something is producing it" out loud
    # is what makes the difference visible in the log and testable.
    if pending_media_stages(beat):
        ensure_media_scheduled(beat)
    return beat, urls


def _beat_or_generate(
    adventure: Adventure,
    *,
    seq: int,
    prev: Beat | None,
) -> tuple[Beat, dict[str, str | None]]:
    """The beat at `seq`, generated at most once across every concurrent caller.

    The unique constraint on `(adventure, seq)` is the backstop and stays; it
    cannot stop the thing that actually costs money, because two callers can both
    check, both find nothing, and both call the provider before either writes. The
    lock closes that window: the second caller waits, then finds the first's beat.

    That matters more now than it did for the opening. A stream re-opened after a
    choice and the task the choice enqueued (`advance_adventure`) are aiming at the
    same position on purpose — the task exists to have got there first — so the two
    of them racing is the *design*, not an edge case.

    Taken only when there is nothing to replay, so the ordinary path — a beat that
    is already written — never touches it.
    """
    if state := beat_state(adventure, seq):
        logger.info(
            "story.beat_replayed adventure=%s beat=%s seq=%d", adventure.id, state[0].id, seq
        )
        return state

    with transaction.atomic():
        Adventure.objects.select_for_update().get(pk=adventure.pk)

        if state := beat_state(adventure, seq):
            # Someone else generated it while we were waiting for the lock. This
            # is the double-pay that used to happen here, now costing one query.
            logger.info(
                "story.beat_replayed adventure=%s beat=%s seq=%d waited_for_lock=1",
                adventure.id,
                state[0].id,
                seq,
            )
            return state

        # The position is always named explicitly, never "the next one": see
        # `create_beat`.
        beat = create_beat(adventure, seq=seq, prev_choice=turn_from(prev))
        return beat, beat_media_urls(beat)


def resolve_current_beat(adventure: Adventure) -> tuple[Beat, dict[str, str | None], bool]:
    """Where the child is, as `(beat, media urls, is_final)`.

    The one entry point the stream uses. It answers "which beat now" from the
    child's own progress (`next_position`) rather than from the newest row, and it
    refuses to run past the plan's last beat — an adventure that has been played to
    its warm close replays that close rather than growing a beat eight.
    """
    seq, prev = next_position(adventure)
    last = total_beats(adventure)

    if seq > last:
        # Every beat is answered and the story is over. Replay the close; the
        # client shows the warm ending rather than asking for more.
        final = beat_state(adventure, last)
        if final is not None:
            return final[0], final[1], True
        seq = last  # No close was ever written: generate it.

    beat, urls = resolve_beat(adventure, seq=seq, prev=prev)
    return beat, urls, is_final_seq(adventure, beat.seq)


async def ensure_current_beat(adventure: Adventure) -> tuple[Beat, dict[str, str | None], bool]:
    """`resolve_current_beat` for an async caller, off the event loop (§16.4)."""
    return await _offload(resolve_current_beat, adventure)


# ---------------------------------------------------------------------------
# The child acts (§8.5, §18.6)
# ---------------------------------------------------------------------------
def consequence_summary(
    beat: Beat,
    *,
    child_name: str,
    chosen_key: str,
    value: Any = None,
    assisted: bool = False,
) -> str:
    """One line saying what the child did and what it means for what comes next.

    Written **synchronously**, from what is already on the row, with no model call:
    it is computed on the request that returns `202`, and a child waiting on a
    generation to be told their tap registered is the exact latency this endpoint
    exists to avoid. The generated *reaction* is the next beat's job.

    Not a verdict. There is no branch here that reads "wrong" — `value` is recorded
    as what the child said, and `assisted` as the companion having helped, because
    both are difficulty signals (§8.9) and neither is a mark (§8.5).
    """
    option = beat.option(chosen_key) or {}
    label = str(option.get("label") or "").strip() or chosen_key or "to carry on"

    parts = [f'At beat {beat.seq}, {child_name} chose "{label}".']
    if value is not None:
        parts.append(f"They answered {value}.")
    if assisted:
        parts.append(f"{_companion_name(beat)} helped them get there, and they kept going.")
    if beat.next_hint:
        parts.append(f"The beat's own steer for what follows: {beat.next_hint}")
    return " ".join(parts)


def record_choice(
    beat: Beat,
    *,
    chosen_key: str,
    value: Any = None,
    latency_ms: int | None = None,
    retries: int = 0,
    assisted: bool = False,
) -> tuple[Choice, bool]:
    """Persist what the child did on this beat. Returns `(choice, created)`.

    Idempotent by constraint rather than by lock. A four-year-old submits twice by
    double-tapping a big forgiving target, and two rows here would advance the
    story two beats at once — the child would watch a beat they never played. The
    unique constraint on `beat` settles it and the second submission adopts the
    first, which is the same shape as `_persist_beat` and `media._create_row`.

    Deliberately not validated against `interaction.expected`. Nothing here decides
    whether the child was right, because nothing in this product does (§8.5): an
    unrecognised key is recorded as what the child sent and the next beat carries
    on warmly from it.
    """
    adventure = beat.adventure
    try:
        with transaction.atomic():
            choice = Choice.objects.create(
                adventure=adventure,
                beat=beat,
                options=beat.options,
                chosen_key=chosen_key,
                value=value,
                consequence_summary=consequence_summary(
                    beat,
                    child_name=adventure.child.first_name,
                    chosen_key=chosen_key,
                    value=value,
                    assisted=assisted,
                ),
                latency_ms=latency_ms,
                retries=retries,
                assisted=assisted,
            )
    except IntegrityError:
        existing = Choice.objects.filter(beat=beat).first()
        if existing is None:
            raise
        logger.info(
            "story.choice_duplicate adventure=%s beat=%s kept=%s",
            adventure.id,
            beat.id,
            existing.chosen_key,
        )
        return existing, False

    logger.info(
        "story.choice adventure=%s beat=%s key=%s latency_ms=%s retries=%d assisted=%d",
        adventure.id,
        beat.id,
        chosen_key,
        latency_ms,
        retries,
        int(assisted),
    )
    # The summary is what the *next* beat is written against, so it has to include
    # this choice before anything is asked to react to it.
    refresh_running_summary(adventure)
    return choice, True


def end_adventure(
    adventure: Adventure, *, reason: str = PlaySession.EndedReason.COMPLETED
) -> Adventure:
    """Close the story and the sitting it happened in (§13.6, §17.2).

    Idempotent: an adventure that is already finished is returned untouched, so a
    client that sends `end` twice — a tap on "all done" plus the page unloading —
    does not rewrite when the sitting ended.

    `ended_reason` is a wellbeing signal reported to parents and is kept separate
    from narrative completion on purpose: a child who reached the warm close and a
    child who wandered off after two beats both end a session, and only one of them
    finished a story.
    """
    with transaction.atomic():
        # No `select_related("session")` here: the FK is nullable, so Django joins
        # it as an outer join and Postgres refuses `FOR UPDATE` against the
        # nullable side. The session is fetched separately in `_finalise_session`,
        # which is one query on a path taken once per adventure.
        locked = Adventure.objects.select_for_update().get(pk=adventure.pk)
        if locked.status != Adventure.Status.IN_PROGRESS:
            logger.info("story.end_noop adventure=%s status=%s", locked.id, locked.status)
            return locked

        completed = reason == PlaySession.EndedReason.COMPLETED
        locked.status = Adventure.Status.COMPLETED if completed else Adventure.Status.ABANDONED
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "completed_at", "updated_at"])
        _finalise_session(locked, reason)

    logger.info(
        "story.adventure_ended adventure=%s status=%s reason=%s beats=%d",
        locked.id,
        locked.status,
        reason,
        locked.beats.count(),
    )
    adventure.status = locked.status
    adventure.completed_at = locked.completed_at
    return locked


def _finalise_session(adventure: Adventure, reason: str) -> None:
    """Stamp the sitting's duration and why it stopped.

    Left alone if it has already been closed. Today that guard never fires, because
    `create_adventure` opens a session per adventure — but `session` is a caller's
    argument for exactly the reason §17.2 keeps the two tables apart: one sitting
    may contain several adventures, and when the session cap lands (Day 11) and a
    shared session appears, the first story to finish must not decide when the
    child stopped playing.
    """
    session = adventure.session
    if session is None or session.ended_at is not None:
        return

    session.ended_at = timezone.now()
    session.duration_s = max(0, int((session.ended_at - session.started_at).total_seconds()))
    session.ended_reason = reason
    session.save(update_fields=["ended_at", "duration_s", "ended_reason", "updated_at"])


def _companion_name(beat: Beat) -> str:
    companion = _companion_or_none(beat)
    return companion.name if companion is not None else "The companion"


# ---------------------------------------------------------------------------
# Media: illustration and voice, together
# ---------------------------------------------------------------------------
def spoken_line(beat: Beat) -> str:
    """What the companion says aloud for this beat.

    Narration, the interaction's invitation, and the options read out — in one
    clip and therefore one TTS call. Splitting them would double the spend and
    introduce a silence exactly where the child is being asked a question.

    **The options are spoken because the audience cannot read them.** A picture
    tells a three-year-old that one card is a river and one is a meadow; only the
    voice tells them those are the two things they may pick between. A choice
    screen with unvoiced labels is a choice screen a pre-reader has to guess at.

    The text *is* the cache key (§16.6), so this concatenation has to be built in
    one place — here.
    """
    parts = [beat.narration.strip()]

    prompt_audio = (beat.interaction or {}).get("prompt_audio")
    if prompt_audio:
        parts.append(str(prompt_audio).strip())

    if labels := [str(option.get("label") or "").strip() for option in beat.options]:
        spoken = [label for label in labels if label]
        if spoken:
            parts.append(_read_out(spoken))

    return " ".join(part for part in parts if part)


def _read_out(labels: Sequence[str]) -> str:
    """The options as one spoken sentence: "A, B, or C?"."""
    if len(labels) == 1:
        return f"{labels[0]}?"
    return f"{', '.join(labels[:-1])}, or {labels[-1]}?"


def illustrate_beat(beat: Beat) -> Media | None:
    """Render this beat's picture through the Day-3 pipeline (§16.6).

    Goes through `companion.service.illustrate` rather than calling the media
    service directly, so the style tokens, the companion's canonical descriptor
    and the cache key are computed exactly as they were for the reference set. A
    beat that happens to describe the same scene as an existing render costs
    nothing.
    """
    prompt = str((beat.scene or {}).get("illustration_prompt") or "").strip()
    if not prompt:
        logger.warning("story.illustration_skipped beat=%s reason=no_prompt", beat.id)
        return None

    companion = _companion_or_none(beat)
    if companion is None:
        logger.warning("story.illustration_skipped beat=%s reason=no_companion", beat.id)
        return None
    return companion_service.illustrate(companion, prompt)


def illustrate_choice(beat: Beat, icon_prompt: str) -> Media | None:
    """Render the little picture on one choice card (§14.4).

    Deliberately **without** the companion. A choice card shows the path, not the
    friend standing on it: putting Pip on all three cards would make them read as
    three pictures of Pip rather than three different places, and it would triple
    what a decision point costs for nothing the child can use. The same style
    tokens still apply, so the cards sit inside the same picture book as the
    scene.

    Cast-free also means the cache key is cast-free, so "a mossy stepping stone"
    is rendered once for every child who is ever offered one (§16.6) — which is
    what makes an icon per option affordable at all.
    """
    prompt = media_prompts.build_illustration_prompt(
        icon_prompt,
        [],
        include_references=False,
    )
    style, character, scene = hashes_for(
        style_signature(),
        media_prompts.character_signature([]),
        icon_prompt,
    )
    return media_service.get_or_generate_image(
        child=beat.adventure.child,
        style_hash=style,
        character_hash=character,
        scene_hash=scene,
        prompt=prompt,
    )


def narrate_beat(beat: Beat) -> Media | None:
    """Synthesise this beat's voice line in the companion's voice (§9.1)."""
    companion = _companion_or_none(beat)
    if companion is None:
        logger.warning("story.narration_skipped beat=%s reason=no_companion", beat.id)
        return None

    return media_service.get_or_generate_audio(
        child=companion.child,
        text=spoken_line(beat),
        voice_id=companion.voice_id or "",
    )


def _companion_or_none(beat: Beat) -> Any:
    try:
        return beat.adventure.child.companion
    except ObjectDoesNotExist:
        return None


#: Stage labels. Named constants because three files agree about them: the stream
#: frame builder, the timing log and the `Beat` columns.
ILLUSTRATION_STAGE = "illustration"
VOICE_STAGE = "voice"
#: Each illustrated option gets its own stage, `choice:<key>` (§14.4). A stage per
#: option rather than one "the choices" stage because they are independent
#: renders: three of them run alongside the scene and the voice, and one that
#: fails must not take the other two down with it.
CHOICE_STAGE_PREFIX = "choice:"

#: The stages every beat has, whatever it asks. Named so the stream can say "the
#: beat's own assets" without re-deriving which two those are.
MEDIA_STAGE_LABELS: tuple[str, ...] = (ILLUSTRATION_STAGE, VOICE_STAGE)


def choice_stage(key: str) -> str:
    return f"{CHOICE_STAGE_PREFIX}{key}"


def choice_key_for(label: str) -> str | None:
    """The option key a `choice:<key>` stage label refers to, or None."""
    if not label.startswith(CHOICE_STAGE_PREFIX):
        return None
    return label.removeprefix(CHOICE_STAGE_PREFIX)


@dataclass(frozen=True, slots=True)
class MediaStage:
    """One asset a beat needs, and everything the pipeline must know about it.

    A record rather than a `(label, field, producer)` tuple because the three
    kinds of asset no longer share a shape: the illustration and the voice each
    own a foreign key, and a choice icon is one entry in a JSON map. Naming
    `attach` and `is_done` separately is what lets the stream, the repair check
    and the Celery task treat all of them identically.
    """

    label: str
    #: Generate the asset. Runs on its own thread; may return None when there is
    #: nothing to render (no prompt, no companion).
    produce: Callable[[Beat], Media | None]
    #: Write it onto the beat, durably, before the client is told about it.
    attach: Callable[[Beat, Media], None]
    #: Is it already there? Reads local columns only, so it is safe on a beat
    #: loaded without `select_related` and from inside an async generator.
    is_done: Callable[[Beat], bool]


def media_stages(beat: Beat) -> tuple[MediaStage, ...]:
    """Every asset this beat needs — the scene, the voice, and one icon per option.

    Built per beat rather than held as a module constant, because how many stages
    a beat has is a property of the beat: a decision point needs three more
    pictures than a transition does.

    The producers are looked up through the module at call time (`illustrate_beat`
    rather than a captured reference) so that a test which replaces one of them
    replaces it here too.
    """
    stages = [
        MediaStage(
            label=ILLUSTRATION_STAGE,
            produce=lambda target: illustrate_beat(target),
            attach=_field_attacher("media"),
            is_done=lambda target: target.media_id is not None,
        ),
        MediaStage(
            label=VOICE_STAGE,
            produce=lambda target: narrate_beat(target),
            attach=_field_attacher("audio"),
            is_done=lambda target: target.audio_id is not None,
        ),
    ]
    for option in beat.options:
        key = str(option.get("key") or "")
        prompt = str(option.get("icon_prompt") or "").strip()
        if not key or not prompt:
            continue
        stages.append(
            MediaStage(
                label=choice_stage(key),
                produce=_choice_producer(prompt),
                attach=_choice_attacher(key),
                is_done=_choice_checker(key),
            )
        )
    return tuple(stages)


def _field_attacher(field: str) -> Callable[[Beat, Media], None]:
    """Point one of the beat's two media columns at a rendered asset.

    Scoped `update_fields` is what makes the stages safe to run at once — each
    writes its own column, so neither can clobber the other's result with a stale
    in-memory copy of the row.
    """

    def attach(beat: Beat, media: Media) -> None:
        setattr(beat, field, media)
        beat.save(update_fields=[field, "updated_at"])

    return attach


def _choice_attacher(key: str) -> Callable[[Beat, Media], None]:
    """Record one option's icon in `choice_media`.

    Locked, unlike the two column attachers: every option writes into the *same*
    JSON column, so `update_fields` cannot keep them apart and a read-modify-write
    from three threads would leave one icon behind. The lock is held for a single
    row write, after the provider call has already returned.
    """

    def attach(beat: Beat, media: Media) -> None:
        with transaction.atomic():
            locked = Beat.objects.select_for_update().get(pk=beat.pk)
            locked.choice_media = {**locked.choice_media, key: str(media.id)}
            locked.save(update_fields=["choice_media", "updated_at"])
        beat.choice_media = dict(locked.choice_media)

    return attach


def _choice_checker(key: str) -> Callable[[Beat], bool]:
    def is_done(beat: Beat) -> bool:
        return bool((beat.choice_media or {}).get(key))

    return is_done


def _choice_producer(prompt: str) -> Callable[[Beat], Media | None]:
    def produce(beat: Beat) -> Media | None:
        return illustrate_choice(beat, prompt)

    return produce


def pending_media_stages(beat: Beat) -> tuple[str, ...]:
    """Which of this beat's assets are still missing."""
    return tuple(stage.label for stage in media_stages(beat) if not stage.is_done(beat))


def schedule_beat_media(beat: Beat) -> None:
    """Hand a beat's media to a worker (§16.3).

    Enqueued **on commit** so the worker cannot look up a beat that its own
    transaction has not written yet — the classic way a task keyed on a fresh row
    fails a fraction of the time and nowhere reproducibly. Outside a transaction
    `on_commit` runs the callback immediately, so this is correct either way.

    A broker that is down is logged, not raised. The beat exists and is tellable;
    only its picture and voice are missing, and the next open repairs that
    (`ensure_media_scheduled`). This is exactly the failure that must never reach a
    child — the same posture as `companion.views._enqueue_assets`.
    """
    # Imported here rather than at module scope: `tasks` imports this module.
    from apps.story.tasks import generate_beat_media

    beat_id = str(beat.id)

    def enqueue() -> None:
        try:
            generate_beat_media.delay(beat_id)
        except Exception:
            logger.exception("story.media_enqueue_failed beat=%s", beat_id)

    transaction.on_commit(enqueue)


#: What `schedule_advance` decided to do, for the log and for the `prefetch` frame.
ADVANCE_READY = "ready"
ADVANCE_QUEUED = "queued"
ADVANCE_BRANCHING = "branching"
ADVANCE_FINAL = "final"


def schedule_advance(adventure: Adventure, *, after: Beat) -> str:
    """Start generating the beat that follows `after`, now, on a worker (§4.9).

    Returns one of `ADVANCE_*`: the next beat is already written, we have just
    queued it, we cannot know it yet because the child is mid-decision, or there is
    no next beat because this was the close.

    This is the prefetch, and it is the difference between a child tapping a card
    and waiting twenty seconds, and a child tapping a card and being told what
    happened. The expensive part of a beat — the language call, then an
    illustration, a voice line and up to three icons — does not depend on the
    connection that will deliver it, so it is started the moment we know what the
    next beat *is* rather than when a client next asks.

    There are exactly two moments when we know that:

    * **the child has chosen** — the branch is settled, so the consequence beat can
      be generated while the client is still reopening the stream;
    * **the beat asks nothing** (`type: "none"`) — a transition, so what follows it
      cannot depend on anything the child is about to do.

    Everything else is left alone deliberately. Guessing a branch means paying for
    beats the child will never see, and a `count` or `tap` beat can still come back
    `assisted`, which the next beat has to react to (§8.5) — a beat generated before
    that is known would be a beat that ignores the child needing help.
    """
    if is_final_seq(adventure, after.seq):
        return ADVANCE_FINAL
    if adventure.beats.filter(seq=after.seq + 1).exists():
        return ADVANCE_READY
    if not after.choices.exists() and (after.interaction or {}).get("type") != InteractionType.NONE:
        return ADVANCE_BRANCHING

    from apps.story.tasks import advance_adventure

    adventure_id, after_id = str(adventure.id), str(after.id)

    def enqueue() -> None:
        try:
            advance_adventure.delay(adventure_id, after_id)
        except Exception:
            # The broker being down costs a prefetch, never a beat: the stream
            # generates it on the next open instead. Same posture as
            # `schedule_beat_media`.
            logger.exception("story.advance_enqueue_failed adventure=%s", adventure_id)

    logger.info(
        "story.advance_queued adventure=%s after=%s seq=%d", adventure.id, after.id, after.seq
    )
    transaction.on_commit(enqueue)
    return ADVANCE_QUEUED


async def prepare_next_beat(adventure: Adventure, *, after: Beat) -> str:
    """`schedule_advance` for an async caller, off the event loop (§16.4)."""
    return await _offload(schedule_advance, adventure, after=after)


def ensure_media_scheduled(beat: Beat) -> bool:
    """Make sure *something* is producing this beat's missing assets.

    Returns whether a task was queued.

    The staleness check is the whole point. A stream that reopens ten seconds into
    a twenty-five-second generation must not queue a second copy of the same work:
    both would miss the media cache, both would call the image and voice providers,
    and the beat would cost twice for one picture. So a beat that has been touched
    recently is assumed to be in a worker's hands, and only one that has sat
    untouched past `MEDIA_RETRY_AFTER_S` — a worker that died, a broker that was
    down when the beat was written — is queued again.

    `updated_at` is the signal because `_produce_and_attach` writes it as each
    stage lands, so a task that is making progress keeps the beat looking fresh.
    """
    pending = pending_media_stages(beat)
    if not pending:
        return False

    age_s = (timezone.now() - beat.updated_at).total_seconds()
    if age_s < MEDIA_RETRY_AFTER_S:
        logger.info(
            "story.media_in_flight beat=%s pending=%s age_s=%d",
            beat.id,
            ",".join(pending),
            int(age_s),
        )
        return False

    logger.warning(
        "story.media_repair beat=%s pending=%s age_s=%d",
        beat.id,
        ",".join(pending),
        int(age_s),
    )
    schedule_beat_media(beat)
    return True


def media_urls(beat_id: Any) -> dict[str, str | None]:
    """This beat's servable asset URLs, by stage label.

    Two queries at most: the beat with its two media rows, then the choice icons
    in one `IN` — never one query per option, because a decision point has three
    of them and this is called on every poll of every open stream.
    """
    row = Beat.objects.select_related("media", "audio").filter(pk=beat_id).first()
    if row is None:
        return {}
    return beat_media_urls(row)


def beat_media_urls(beat: Beat) -> dict[str, str | None]:
    """The same, for a beat already loaded with `select_related("media", "audio")`."""
    urls: dict[str, str | None] = {
        ILLUSTRATION_STAGE: _servable_url(beat.media),
        VOICE_STAGE: _servable_url(beat.audio),
    }
    urls.update(choice_media_urls(beat))
    return urls


def choice_media_urls(beat: Beat) -> dict[str, str | None]:
    """`{"choice:<key>": url}` for the option icons that have landed."""
    by_key = {key: value for key, value in (beat.choice_media or {}).items() if value}
    if not by_key:
        return {}
    rows = {str(media.id): media for media in Media.objects.filter(id__in=set(by_key.values()))}
    return {choice_stage(key): _servable_url(rows.get(value)) for key, value in by_key.items()}


def _poll_wait(attempt: int) -> float:
    """How long to wait before the next look, after `attempt` completed polls.

    Backs off for the same reason the companion poll does: the thing being waited
    for takes tens of seconds, and a flat fast poll would spend the whole wait
    asking. Starting short keeps the picture arriving promptly once it lands.
    """
    return min(MEDIA_POLL_MIN_S * MEDIA_POLL_BACKOFF**attempt, MEDIA_POLL_MAX_S)


async def follow_media(
    beat: Beat,
    *,
    known: Mapping[str, str | None],
) -> AsyncIterator[tuple[str, str]]:
    """Yield each of the beat's assets as it lands, wherever it was produced.

    The stream used to *run* the media stages inside the response coroutine, which
    is why a client that vanished mid-generation left a paid-for beat with no
    picture and no voice: the generation finished on its offloaded thread, but the
    coroutine that would have started the media never got that far. The work now
    belongs to a Celery task keyed on the beat, and this only watches for the
    result — so a disconnect costs the *view*, never the beat.

    `known` is what the `beat` frame already carried, so a replay of a finished
    beat does no work at all.
    """
    # Ordered, not a set, and the order is `media_stages`': the scene, the voice,
    # then the choice icons. It is what decides which asset the client is told
    # about first when several land in the same poll, and the stream hands the
    # child their turn as soon as the first two are through (`streaming`) — so an
    # alphabetical order here would put three card icons in front of the question.
    order = [stage.label for stage in media_stages(beat)]
    expected = set(order)
    emitted = {label for label, url in known.items() if url and label in expected}
    if emitted >= expected:
        return

    deadline = time.monotonic() + MEDIA_FOLLOW_TIMEOUT_S
    attempt = 0
    while True:
        urls = await _offload(media_urls, beat.pk)
        for label in order:
            if label in emitted:
                continue
            if url := urls.get(label):
                emitted.add(label)
                yield label, url

        if emitted >= expected:
            return
        if time.monotonic() >= deadline:
            logger.warning(
                "story.media_unfinished beat=%s missing=%s",
                beat.id,
                ",".join(sorted(expected - emitted)),
            )
            return

        await asyncio.sleep(_poll_wait(attempt))
        attempt += 1


async def stream_media(
    beat: Beat,
    *,
    stages: Sequence[str] | None = None,
) -> AsyncIterator[tuple[str, Media | None]]:
    """Generate the illustration and the voice **at the same time**, yielding each
    as it lands (§16.4, §18.5).

    Both are independent, both are slow, and neither depends on the other's
    output — so they run concurrently and are consumed with `as_completed` rather
    than awaited in turn. Yielding on completion is what lets the stream push a
    picture to a child who is already hearing the narration, instead of holding
    both until the slower one finishes.

    The timing log is the proof of concurrency: `overlap_ms` is the wall time the
    two calls actually shared. Near zero would mean they had quietly become
    sequential again — which is exactly what happens if either side is ever moved
    onto a thread-sensitive executor.

    Neither failure propagates. A beat with narration and no picture is a story
    the child can still hear; a beat that raised would be a story that stopped.
    Media failures are the most likely thing to go wrong in this pipeline and the
    least worth interrupting a child for.

    `stages` narrows the work to the labels named, which is how a repair run
    finishes a half-generated beat without re-doing the half that already landed.
    """
    specs = media_stages(beat)
    if stages is not None:
        wanted = set(stages)
        specs = tuple(spec for spec in specs if spec.label in wanted)
    base = time.monotonic()
    timings: list[tuple[str, float, float]] = []

    async def run(spec: MediaStage) -> tuple[str, Media | None]:
        start = time.monotonic() - base
        try:
            # Generation and the write happen in the same offloaded call, so the
            # asset is durable before the client is told about it — a child who
            # closes the tab mid-beat comes back to a beat that has its picture.
            media = await _offload(_produce_and_attach, beat, spec)
        except MediaError:
            logger.exception("story.media_failed beat=%s stage=%s", beat.id, spec.label)
            media = None
        timings.append((spec.label, start, time.monotonic() - base))
        return spec.label, media

    tasks = [asyncio.create_task(run(spec)) for spec in specs]
    try:
        for finished in asyncio.as_completed(tasks):
            yield await finished
    finally:
        # `as_completed` leaves nothing running on a normal pass, but a client
        # that disconnects mid-beat closes this generator here, and an
        # un-awaited task would surface as "Task was destroyed but it is
        # pending" rather than as the disconnect it actually is.
        for task in tasks:
            task.cancel()
        _log_timings(beat, base, timings)


def _log_timings(beat: Beat, base: float, timings: list[tuple[str, float, float]]) -> None:
    wall_ms = int((time.monotonic() - base) * 1000)
    serial_ms = int(sum((end - start) for _, start, end in timings) * 1000)
    for label, start, end in timings:
        logger.info(
            "story.media.timing beat=%s stage=%s start_ms=%d end_ms=%d duration_ms=%d",
            beat.id,
            label,
            int(start * 1000),
            int(end * 1000),
            int((end - start) * 1000),
        )
    logger.info(
        "story.media.parallel beat=%s wall_ms=%d serial_ms=%d overlap_ms=%d",
        beat.id,
        wall_ms,
        serial_ms,
        serial_ms - wall_ms,
    )


async def attach_media(
    beat: Beat,
    *,
    stages: Sequence[str] | None = None,
) -> dict[str, Media | None]:
    """Both media stages, awaited to completion. For callers that do not stream.

    This is what the Celery task, the smoke command and the tests use; the stream
    no longer produces media at all, it only watches for it (`follow_media`). It
    exists so "generate a beat's media" has one obvious answer rather than two
    half-answers.
    """
    return {label: media async for label, media in stream_media(beat, stages=stages)}


def _produce_and_attach(beat: Beat, stage: MediaStage) -> Media | None:
    """Generate one asset and record it on the beat.

    `is_servable` is checked rather than trusted: the media pipeline only ever
    stores scanned bytes, but a `flagged` row would still have a URL, and a field
    a child's client reads is the one place that must never happen.
    """
    media = stage.produce(beat)
    if media is None or not media.is_servable:
        return None
    stage.attach(beat, media)
    return media


# ---------------------------------------------------------------------------
# Running blocking work without stalling the loop
# ---------------------------------------------------------------------------
async def _offload(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking, database-touching callable on its own worker thread.

    `thread_sensitive=False` is the load-bearing argument. The default (`True`)
    funnels every call into one shared thread, which is correct for code that
    expects to see a view's transaction — and fatal here, because it would make
    `asyncio.gather` over two provider calls run them one after the other while
    still *looking* concurrent.

    Its own thread means its own database connection, so the connection is closed
    on the way out. Threads come from a reused pool, and Django's per-request
    cleanup does not run in them, so a connection left open here is a connection
    leaked for the lifetime of the process.
    """

    def run() -> T:
        try:
            return fn(*args, **kwargs)
        finally:
            db_connection.close()

    return await sync_to_async(run, thread_sensitive=False)()
