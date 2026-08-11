"""The day-two greeting (PRODUCT.md §4.5, §8.7, §9.3).

    retrieve ──> guard_input ──> assemble (memory as DATA) ──> generate
                                                                  │
                                    guard_output <────────────────┘
                                         │
                              speak ─────┴───── first-meeting fallback

> *"Aarav! Yesterday you were so brave with the shadow. Today the map leads to
> the Whispering Woods…"*

§4.5 calls day two "the whole business", and the sentence above is what it means:
the child comes back and is **recognised** — by name, about something specific
they actually did. Everything else in this file exists to make that sentence
either true or absent, never approximated.

**It references a real memory or it is not a returning greeting.** A warm
"welcome back!" with nothing after it is worse than the first-meeting hello,
because it makes a promise about being remembered and then does not keep it. So a
child with no memories does not get a degraded version of this — they get the
Day-3 first meeting, unchanged, which is a scene that works (`scenes.greeting_text`).
The same fallback catches a provider outage, a schema failure, and a greeting the
output guard refused: three different faults with one correct answer.

**The memories arrive as DATA and are guarded on the way in.** This is a new
surface for §13.5 and the most tempting one to cut a corner on, because the text
being generated is short and friendly. It is also the surface where the untrusted
string has been sitting in our own database since last week with our name on it,
which is exactly the shape of injection this product is most likely to meet: a
child who said something odd into the microphone, consolidated into a memory,
retrieved into a greeting. The bundle goes through `pipeline.guard_input` and into
the `memory_bundle` layer like every other bundle. Nothing here assembles its own
prompt.

**It is graded on the way out like a beat.** The greeting is the first thing a
returning child hears, generated from the least-trusted input in the system, and
it is spoken aloud before any story has started. `pipeline.guard_output` reads it
exactly as it reads a beat's narration; anything short of a pass falls back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from apps.companion import service as companion_service
from apps.companion.models import GREETING_AUDIO, Companion
from apps.media import service as media_service
from apps.media.exceptions import MediaError
from apps.memory import retrieval
from apps.profiles.models import Child
from apps.safety import pipeline as safety
from apps.safety.prompting import assembler
from apps.safety.prompting.layers import render as render_layers
from apps.story.ai import router as ai_router
from apps.story.ai.schemas import json_schema_for

logger = logging.getLogger(__name__)

#: The two kinds of arrival. `RETURNING` is §4.5; `FIRST_MEETING` is §4.3's imprint,
#: which is also the answer whenever the returning path cannot produce something
#: true.
RETURNING = "returning"
FIRST_MEETING = "first_meeting"

#: What produced the text, for the log and the parent transparency view (§12.6).
#: A greeting that says `fallback` is one where something went wrong and the child
#: noticed nothing, which is the only pairing this file accepts.
SOURCE_GENERATED = "generated"
SOURCE_FIXED = "fixed"
SOURCE_FALLBACK = "fallback"

#: The context retrieval is asked to rank against. Not the empty string: it is what
#: the moment *is*, and it is deliberately about coming back rather than about any
#: particular story, so the vector half surfaces what the child would want said
#: rather than whatever today's plan happens to mention (there is no plan yet).
RETURNING_CONTEXT = (
    "The child has come back for another story. What did they do last time, what "
    "did they love, what did they name, and what were they proud of?"
)

#: How long the spoken greeting may be. Two or three sentences: it is said before
#: the story starts, to a four-year-old who is waiting to play, and a paragraph of
#: reminiscence is the companion talking about itself.
MAX_GREETING_CHARS = 400

#: The cap on a name rendered into the greeting request. The assembler's own
#: number, imported rather than repeated so the two cannot drift — a name is a
#: name in both places, and a 400-character one is not a first name.
NAME_CHARS = assembler.NAME_CHARS


class ReturningGreetingText(BaseModel):
    """The generated greeting, as the model returns it.

    `remembered` is asked for **separately from the greeting itself**, and it is
    not decoration. It is the model naming which memory it used, which gives three
    things nothing else does: a check that the greeting is about something in the
    bundle rather than something invented, a line for the parent's transparency log
    (§12.6), and a failure that is *visible* — a model with nothing to say returns
    an empty `remembered` rather than a fluent greeting about a day that never
    happened.
    """

    greeting: str = Field(
        description=(
            "Two or three short sentences, spoken aloud by the companion to the "
            "child by name, referring to one specific thing from the memories "
            "block, and ending by opening today's story. No questions the child "
            "must answer, no reading required."
        ),
    )
    remembered: str = Field(
        description=(
            "The one memory this greeting refers to, copied from the memories "
            "block. Empty string if the memories contain nothing specific enough "
            "to name."
        ),
    )


GREETING_REQUEST = """\
{name} has come back for another story. You are {companion}, and you are the one \
who greets them.

Say hello the way a friend does after a few days apart:
- Use {name}'s name in the first few words.
- Refer to ONE specific thing from the memories block — something they named, \
chose, made, counted, or loved. Name it exactly as the memory names it.
- If they gave you your name or something else of yours, it is warm to say so.
- Then open today's adventure with one line of anticipation. Do not begin the \
story, do not describe a place, and do not ask a question they have to answer.

Two or three short sentences. It is spoken out loud, so no reading, no lists, and \
nothing a {age}-year-old would not say themselves.

If the memories block holds nothing specific about {name} — only their interests \
or their companion's name — return an empty `remembered` and a greeting that \
simply welcomes them back. Never invent a memory.\
"""


@dataclass(frozen=True, slots=True)
class Greeting:
    """What the child's arrival scene is built from.

    `memory_ids` rides along so the caller can bump recall bookkeeping on exactly
    what was said — the same contract `retrieval.MemoryBundle` carries, kept on the
    way out so the view does not have to hold the bundle open.
    """

    kind: str
    text: str
    audio_url: str | None = None
    companion_name: str = ""
    remembered: str = ""
    source: str = SOURCE_FIXED
    memory_ids: tuple[Any, ...] = ()

    @property
    def returning(self) -> bool:
        return self.kind == RETURNING


def build(child: Child, *, speak: bool = True) -> Greeting:
    """The greeting this child should hear right now (§4.5).

    Returns the first-meeting scene for a child with no memories, and for any
    failure on the returning path. **Never raises**: this sits between a child and
    the moment the product is supposed to feel like a friendship, so every
    degradation here is a warm hello that happens to be the one they heard last
    time (§4.9, §13).

    `speak=False` skips the voice call, for callers that want the text alone (the
    tests, and a future prefetch).
    """
    companion = _companion(child)
    if companion is None:
        # No companion means onboarding is unfinished, and the scene that belongs
        # to that state is the picker, not a greeting. There is nobody to say it.
        return Greeting(kind=FIRST_MEETING, text="", source=SOURCE_FIXED)

    bundle = retrieval.retrieve(child, RETURNING_CONTEXT)
    if not bundle.remembered:
        logger.info(
            "companion.greeting child=%s kind=%s reason=no_memories", child.pk, FIRST_MEETING
        )
        return _first_meeting(companion)

    generated = _generate(child, companion, bundle)
    if generated is None:
        return _first_meeting(companion, source=SOURCE_FALLBACK)

    text, remembered = generated
    # Bookkeeping on what was actually said, not on what was queried (§11.9).
    retrieval.mark_recalled(bundle)

    logger.info(
        "companion.greeting child=%s kind=%s memories=%d referenced=%d chars=%d",
        child.pk,
        RETURNING,
        len(bundle.remembered),
        int(bool(remembered)),
        len(text),
    )
    return Greeting(
        kind=RETURNING,
        text=text,
        audio_url=_speak(companion, text) if speak else None,
        companion_name=companion.name,
        remembered=remembered,
        source=SOURCE_GENERATED,
        memory_ids=bundle.memory_ids,
    )


# ---------------------------------------------------------------------------
# The generation
# ---------------------------------------------------------------------------
def _generate(
    child: Child, companion: Companion, bundle: retrieval.MemoryBundle
) -> tuple[str, str] | None:
    """Ask for the greeting, guarded both ways. None means "use the fallback".

    The breadth of the `except` is the same judgement `memory.prompts.propose`
    makes and for a stronger reason: the caller is a child looking at a screen, and
    there is exactly one thing to do about any failure at all — say the hello we
    already know is safe. **Assembly is inside it**, not only the model call: a
    missing `prompt_version` row raises `PromptVersionUnavailable`, and a
    misconfigured install must lose the day-two greeting rather than return a 500
    to a scene that has no way to render one.

    The two names are scrubbed on the way into the request. They are already
    scrubbed where the *layers* render them (`assembler.safety_policy_layer`), and
    this string bypasses that entirely: `first_name` is a text box a parent filled
    in, `companion.name` is another, and the request is the one part of this prompt
    that reads as an instruction. A greeting request built from raw profile text
    would be a §13.5 hole opened in the one file whose docstring is about §13.5.
    """
    try:
        guarded = safety.guard_input({"memory": bundle.as_prompt()}, child)
        layers = assembler.assemble(
            child,
            memory=guarded.get("memory") or [],
            output_schema=json_schema_for(ReturningGreetingText),
        )
        context = {
            "system": render_layers(layers),
            "user": GREETING_REQUEST.format(
                name=safety.scrub_profile_field(child.first_name, cap=NAME_CHARS) or "the child",
                companion=safety.scrub_profile_field(companion.name, cap=NAME_CHARS)
                or "the companion",
                age=child.age,
            ),
        }
        answer = ai_router.generate_structured(context, model=ReturningGreetingText)
    except Exception:
        logger.exception("companion.greeting_generation_failed child=%s", child.pk)
        return None

    text = " ".join(str(answer.greeting or "").split())[:MAX_GREETING_CHARS]
    if not text:
        logger.warning("companion.greeting_empty child=%s", child.pk)
        return None

    decision = safety.guard_output(_as_gradeable(text), child)
    if not decision.passed:
        # No rewrite loop, unlike a beat. A beat is the story and is worth one
        # constrained retry; a greeting has a pre-vetted alternative sitting right
        # there, and the child hears a hello either way.
        logger.error(
            "companion.greeting_refused child=%s action=%s category=%s",
            child.pk,
            decision.action,
            decision.category,
        )
        safety.report(decision, child=child)
        return None

    return text, str(answer.remembered or "").strip()[:MAX_GREETING_CHARS]


@dataclass(frozen=True, slots=True)
class _Gradeable:
    """The greeting in the shape `pipeline.guard_output` reads a beat in.

    `child_facing_text` walks `narration`, the spoken prompt and the illustration —
    duck-typed, because §13.3's question is about *text a child will hear* and a
    greeting is exactly that with fewer parts. A wrapper rather than a fake `Beat`,
    so nothing can mistake this for something persistable.
    """

    narration: str


def _as_gradeable(text: str) -> _Gradeable:
    return _Gradeable(narration=text)


# ---------------------------------------------------------------------------
# The fallback and the voice
# ---------------------------------------------------------------------------
def _first_meeting(companion: Companion, *, source: str = SOURCE_FIXED) -> Greeting:
    """§4.3's imprint hello, reused verbatim.

    Its audio is already synthesised and attached to the companion (Day 3's
    `service.ensure_greeting_audio`), so this path costs nothing at all — no model
    call, no TTS call, no wait. That is what makes it a safe answer to any failure
    rather than a second thing that can fail.
    """
    return Greeting(
        kind=FIRST_MEETING,
        text=companion_service.greeting_line(companion),
        audio_url=companion_service.reference_url(companion, GREETING_AUDIO),
        companion_name=companion.name,
        source=source,
    )


def _speak(companion: Companion, text: str) -> str | None:
    """Synthesise the greeting. None on any failure — the caption still carries it.

    Cached on `(voice, text)` by `media.service`, so a child who reloads the scene
    hears the same recording rather than paying for a second one, and a greeting
    that happens to come back identical costs nothing twice.
    """
    try:
        media = media_service.get_or_generate_audio(
            child=companion.child, text=text, voice_id=companion.voice_id or ""
        )
    except MediaError:
        # The scene is designed to work in silence (`ReturningGreeting.tsx`): the
        # words are on screen and the companion still arrives. A missing voice is
        # not something a child may be told about (§13).
        logger.exception("companion.greeting_voice_failed companion=%s", companion.id)
        return None
    # `is_servable`, not the raw column: a clip the scan flagged is withheld here
    # exactly as it is on a beat (`orchestrator._servable_url`).
    return media.url if media.is_servable else None


def _companion(child: Child) -> Companion | None:
    return Companion.objects.filter(child=child).select_related("child").first()
