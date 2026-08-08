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

What is deliberately *not* here yet: memory retrieval (Days 8-9), the objective
selector (Day 10), the runtime safety guard (Day 7), and the choice loop (Day 5).
Each has a named seam and a placeholder, so those days are wiring rather than
redesign.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db import connection as db_connection
from django.utils import timezone

from apps.companion import service as companion_service
from apps.media import service as media_service
from apps.media.exceptions import MediaError
from apps.profiles.models import Child
from apps.safety.prompting import assembler
from apps.safety.prompting.layers import render as render_layers
from apps.story.ai import router as ai_router
from apps.story.ai.prompts import story as story_prompts
from apps.story.ai.schemas import Beat as BeatContract
from apps.story.ai.schemas import StoryPlan, beat_json_schema, json_schema_for
from apps.story.models import Adventure, Beat

if TYPE_CHECKING:
    from apps.media.models import Media
    from apps.story.models import PlaySession

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

    "In the middle of" is `status=in_progress` and nothing more subtle. Completion
    arrives with the choice loop on Day 5; until then an adventure stays open until
    something explicitly closes it, which is the conservative direction — the cost
    of resuming a story the child had drifted away from is that they see the same
    forest, and the cost of the opposite mistake is a second paid-for adventure on
    every reload.
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
def build_beat_context(
    adventure: Adventure,
    *,
    seq: int,
    prev_choice: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """The assembled prompt for one beat (§19.1). Pure — no model, no I/O but the
    active prompt row."""
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
    caller that means a *specific* position has to say so: `ensure_first_beat`
    means beat one, and if it asked for "next" instead then two streams racing on
    a fresh adventure would produce a beat 1 and a beat 2 rather than colliding on
    the constraint — the child would be told the opening, then told a second
    opening as though it were what came next.

    Raises `BeatValidationError` when the provider cannot produce a schema-valid
    beat even after the router's one repair attempt (§19.6). The caller — the
    stream — is what turns that into an ending the child can live with; Day 7
    turns it into a safe fallback beat instead.
    """
    seq = _next_seq(adventure) if seq is None else seq
    context = build_beat_context(adventure, seq=seq, prev_choice=prev_choice)

    started = time.monotonic()
    contract = ai_router.generate_beat(context)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    try:
        beat = _persist_beat(adventure, seq, contract)
    except IntegrityError:
        # Another stream on the same adventure wrote this position while we were
        # generating. The unique constraint settles it and we adopt their beat
        # rather than telling the child the opening twice. We have still paid for
        # a generation, which is why `first_beat_state` checks *before* we get
        # here — this catch is for the narrow window that check cannot close.
        winner = adventure.beats.filter(seq=seq).first()
        if winner is None:
            raise
        logger.info(
            "story.beat_race_lost adventure=%s seq=%d kept=%s", adventure.id, seq, winner.id
        )
        return winner

    logger.info(
        "story.beat adventure=%s beat=%s seq=%d gen_ms=%d emotion=%s interaction=%s",
        adventure.id,
        beat.id,
        seq,
        elapsed_ms,
        contract.emotion,
        contract.interaction.type,
    )
    # The picture and the voice are a worker's job from here (§16.3). Handing them
    # over at the moment the beat is persisted is what stops a client that
    # disconnects mid-generation from leaving a paid-for beat with no media.
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


@transaction.atomic
def _persist_beat(adventure: Adventure, seq: int, contract: BeatContract) -> Beat:
    """Write the beat and extend the adventure's emotion arc, together.

    One transaction because the arc (§8.8) is a projection of the beats: an arc
    that has recorded a beat which does not exist would make the wellbeing
    down-shift fire on a story that was never told.
    """
    # Normalised here, at the boundary where a model's output becomes our data, so
    # that everything downstream — the voice clip, the caption, the transcript a
    # parent reads, Day 5's memory — trusts one clean record instead of each
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

    beat = Beat.objects.create(
        adventure=adventure,
        seq=seq,
        narration=contract.narration,
        scene=contract.scene.model_dump(mode="json"),
        interaction=interaction,
        learning=contract.learning.model_dump(mode="json"),
        emotion=contract.emotion,
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


def first_beat_state(adventure: Adventure) -> tuple[Beat, str | None, str | None] | None:
    """The adventure's opening beat and its media URLs, if it has been generated.

    `select_related` because the caller is an async generator: following
    `beat.media` lazily from there raises, and the two URLs are the only thing it
    wants from those rows.
    """
    beat = adventure.beats.select_related("media", "audio").filter(seq=FIRST_BEAT_SEQ).first()
    if beat is None:
        return None
    return beat, _servable_url(beat.media), _servable_url(beat.audio)


def _servable_url(media: Media | None) -> str | None:
    return media.url if media is not None and media.is_servable else None


def resolve_first_beat(adventure: Adventure) -> tuple[Beat, str | None, str | None]:
    """The opening beat and its media URLs: replayed if it exists, generated once
    if it does not. Synchronous core of `ensure_first_beat`.

    Replay is not a nicety. A child who reloads mid-story — which the founder test
    does deliberately, and which a tablet does by itself when the OS evicts a tab —
    must land back in the *same* forest, and must not cost a second plan, beat,
    illustration and voice line to get there.
    """
    beat, image_url, audio_url = _first_beat_or_generate(adventure)

    # An explicit repair rather than an accident. The old code re-ran the media
    # stages whenever a URL was missing, which happened to heal an orphaned beat
    # and happened to double-pay when two clients did it at once. Saying "this
    # beat's media never finished — make sure something is producing it" out loud
    # is what makes the difference visible in the log and testable.
    if not (image_url and audio_url):
        ensure_media_scheduled(beat)
    return beat, image_url, audio_url


def _first_beat_or_generate(adventure: Adventure) -> tuple[Beat, str | None, str | None]:
    """Beat one, generating it at most once across every concurrent stream.

    The unique constraint on `(adventure, seq)` is the backstop and stays; it
    cannot stop the thing that actually costs money, because two callers can both
    check, both find nothing, and both call the provider before either writes. The
    lock closes that window: the second caller waits, then finds the first's beat.

    Taken only when there is nothing to replay, so the ordinary reload — the case
    this endpoint serves most often — never touches it.
    """
    if state := first_beat_state(adventure):
        logger.info("story.beat_replayed adventure=%s beat=%s", adventure.id, state[0].id)
        return state

    with transaction.atomic():
        Adventure.objects.select_for_update().get(pk=adventure.pk)

        if state := first_beat_state(adventure):
            # Someone else generated it while we were waiting for the lock. This
            # is the double-pay that used to happen here, now costing one query.
            logger.info(
                "story.beat_replayed adventure=%s beat=%s waited_for_lock=1",
                adventure.id,
                state[0].id,
            )
            return state

        # `seq=1` explicitly, not "the next one": see `create_beat`.
        return create_beat(adventure, seq=FIRST_BEAT_SEQ), None, None


async def ensure_first_beat(adventure: Adventure) -> tuple[Beat, str | None, str | None]:
    """`resolve_first_beat` for an async caller, off the event loop (§16.4)."""
    return await _offload(resolve_first_beat, adventure)


# ---------------------------------------------------------------------------
# Media: illustration and voice, together
# ---------------------------------------------------------------------------
def spoken_line(beat: Beat) -> str:
    """What the companion says aloud for this beat.

    Narration plus the interaction's invitation, in one clip and therefore one
    TTS call. Splitting them would double the spend and introduce a silence
    exactly where the child is being asked a question. The text *is* the cache
    key (§16.6), so this concatenation has to be built in one place — here.
    """
    line = beat.narration.strip()
    prompt_audio = (beat.interaction or {}).get("prompt_audio")
    if prompt_audio:
        line = f"{line} {str(prompt_audio).strip()}"
    return line


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

#: The two media stages of a beat, as `(label, field, producer)`. Named in one
#: place so the stream, the timing log and the `Beat` columns cannot disagree
#: about what "illustration" means.
MEDIA_STAGES: tuple[tuple[str, str, Callable[[Beat], Media | None]], ...] = (
    (ILLUSTRATION_STAGE, "media", illustrate_beat),
    (VOICE_STAGE, "audio", narrate_beat),
)


def pending_media_stages(beat: Beat) -> tuple[str, ...]:
    """Which of this beat's assets are still missing.

    Reads the FK id columns rather than following the relations, so it is safe to
    call on a beat loaded without `select_related` and from inside an async
    generator.
    """
    return tuple(label for label, field, _ in MEDIA_STAGES if getattr(beat, f"{field}_id") is None)


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
    """This beat's servable asset URLs, by stage label. One query."""
    row = Beat.objects.select_related("media", "audio").filter(pk=beat_id).first()
    if row is None:
        return {}
    return {label: _servable_url(getattr(row, field)) for label, field, _ in MEDIA_STAGES}


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
    image_url: str | None,
    audio_url: str | None,
) -> AsyncIterator[tuple[str, str]]:
    """Yield each of the beat's assets as it lands, wherever it was produced.

    The stream used to *run* the media stages inside the response coroutine, which
    is why a client that vanished mid-generation left a paid-for beat with no
    picture and no voice: the generation finished on its offloaded thread, but the
    coroutine that would have started the media never got that far. The work now
    belongs to a Celery task keyed on the beat, and this only watches for the
    result — so a disconnect costs the *view*, never the beat.

    `image_url`/`audio_url` are what the `beat` frame already carried, so a replay
    of a finished beat does no work at all.
    """
    emitted = {
        label for label, url in ((ILLUSTRATION_STAGE, image_url), (VOICE_STAGE, audio_url)) if url
    }
    expected = {label for label, _, _ in MEDIA_STAGES}
    if emitted >= expected:
        return

    deadline = time.monotonic() + MEDIA_FOLLOW_TIMEOUT_S
    attempt = 0
    while True:
        urls = await _offload(media_urls, beat.pk)
        for label, _, _ in MEDIA_STAGES:
            url = urls.get(label)
            if url and label not in emitted:
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
    specs = MEDIA_STAGES if stages is None else tuple(s for s in MEDIA_STAGES if s[0] in stages)
    base = time.monotonic()
    timings: list[tuple[str, float, float]] = []

    async def stage(
        label: str,
        field: str,
        produce: Callable[[Beat], Media | None],
    ) -> tuple[str, Media | None]:
        start = time.monotonic() - base
        try:
            # Generation and the write happen in the same offloaded call, so the
            # asset is durable before the client is told about it — a child who
            # closes the tab mid-beat comes back to a beat that has its picture.
            media = await _offload(_produce_and_attach, beat, field, produce)
        except MediaError:
            logger.exception("story.media_failed beat=%s stage=%s", beat.id, label)
            media = None
        timings.append((label, start, time.monotonic() - base))
        return label, media

    tasks = [asyncio.create_task(stage(*spec)) for spec in specs]
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


def _produce_and_attach(
    beat: Beat,
    field: str,
    produce: Callable[[Beat], Media | None],
) -> Media | None:
    """Generate one asset and point the beat's column at it.

    `is_servable` is checked rather than trusted: the media pipeline only ever
    stores scanned bytes, but a `flagged` row would still have a URL, and a field
    a child's client reads is the one place that must never happen.

    Scoped `update_fields` is what makes the two stages safe to run at once —
    each writes its own column, so neither can clobber the other's result with a
    stale in-memory copy of the row.
    """
    media = produce(beat)
    if media is None or not media.is_servable:
        return None
    setattr(beat, field, media)
    beat.save(update_fields=[field, "updated_at"])
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
