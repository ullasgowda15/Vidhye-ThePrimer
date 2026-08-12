"""The Beat contract (PRODUCT.md §8.1).

Four different consumers each need a different slice of a beat: the client
renders `narration` + `interaction`, the illustration pipeline reads
`scene.illustration_prompt`, the learning engine reads `learning`, and the
safety layer inspects the whole thing. A typed contract lets each take its
slice deterministically and lets us validate-and-repair a malformed generation
(§19.6) instead of shipping one to a child.

Every field is required. Optional-in-spirit fields (`prompt_audio`, `expected`,
`choices`) are required-but-nullable so the JSON schema stays strict: a beat
that silently omits `interaction.expected` is a bug we want to catch here, not
three layers downstream.
"""

from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

# JSON-Schema keywords that the structured-output subset does not accept. Pydantic
# emits them from ordinary field constraints (`Field(ge=…)` becomes `minimum`), and
# a provider will 400 on the whole request rather than ignore them. We keep the
# constraints on the model — they still validate what comes *back* — and strip them
# from the schema we send. See `strip_unsupported_keywords`.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


class _Strict(BaseModel):
    """Base that forbids unknown keys, which renders as `additionalProperties: false`."""

    model_config = ConfigDict(extra="forbid")


class CastMember(_Strict):
    """A character appearing for the first time, described well enough to redraw.

    This exists because §8.2 requires recurring characters to be persisted
    entities with a canonical descriptor, and the beat that *invents* one is the
    only place that descriptor can come from cheaply. Asking a second model call
    "what did that frog look like?" would pay twice and, worse, would be a guess
    at something already decided; deriving it from the illustration prompt would
    mean parsing prose for a noun.

    So the writer says it outright, in the same call, and
    `companion.cast.remember` persists it. The next beat this character appears
    in gets the descriptor back verbatim, which is what makes them the same
    creature on their second appearance rather than a new one wearing the same
    name (§23.1).
    """

    key: str = Field(
        description=("The same stable snake_case id used in characters_present, e.g. 'bramble'."),
    )
    name: str = Field(description="What the companion calls them out loud, e.g. 'Bramble'.")
    visual_descriptor: str = Field(
        description=(
            "Permanent appearance only, for every future picture of them: body shape, "
            "size, colours, markings, one or two distinctive details. Never a pose, an "
            "expression, an action, a background or an art style — those change per "
            "picture. Not a human child."
        ),
    )


class Scene(_Strict):
    setting: str = Field(description="Where this beat happens, in one line.")
    illustration_prompt: str = Field(
        description="Full prompt for the illustration model, including style cues.",
    )
    characters_present: list[str] = Field(
        description="Stable character keys visible in this beat, e.g. ['pip', 'frogs'].",
    )
    new_characters: list[CastMember] = Field(
        description=(
            "Every character in characters_present who is appearing for the first "
            "time and whose appearance we do not already hold. Empty list if there "
            "are none — which is the usual case."
        ),
    )


# A closed set rather than free text: `enum` *is* in the structured-output subset,
# so the provider is constrained at generation time instead of being asked nicely in
# the prompt, and Day 4's client dispatch can be exhaustive. Adding a member is the
# deliberate act of teaching the client a new interaction — it should never arrive as
# a surprise string. The docstring is one line on purpose: it ships in the schema.
class InteractionType(StrEnum):
    """What the child is asked to do."""

    COUNT = "count"
    CHOOSE = "choose"
    SAY = "say"
    TAP = "tap"
    NONE = "none"


class ChoiceOption(_Strict):
    """One thing the child can pick at a decision point (§8.5, §14.4).

    A structured option rather than a bare string, because a choice has three
    different jobs and a string can only do one of them. `key` is what the child's
    tap sends back and what the consequence beat is conditioned on, so it must
    survive rewording; `label` is *heard*, not read, since the audience cannot
    read; and `icon_prompt` is what gets drawn on the card, because §14.4 requires
    a picture of each path rather than a text list — a pre-reader cannot
    participate in a menu.

    There are no losing options (§8.5). Every one of these leads somewhere good.
    """

    key: str = Field(description="Stable snake_case id for this option, e.g. 'follow_river'.")
    label: str = Field(
        description="What the companion calls this option out loud — three to six plain words.",
    )
    icon_prompt: str = Field(
        description=(
            "What the little picture on this option's card shows: one concrete "
            "noun-phrase, no characters, no text, e.g. 'a sparkling river winding "
            "between mossy stones'."
        ),
    )


class Interaction(_Strict):
    # No Field(...) here: extra metadata would make Pydantic emit siblings next to
    # the `$ref`, which strict schema validators are inconsistent about accepting.
    type: InteractionType
    prompt_audio: str | None = Field(
        description=(
            "The single short question the companion asks to invite the interaction, "
            "spoken *after* the narration. One sentence. It must not repeat, restate "
            "or summarise the narration — the child has just heard that. Null if the "
            "narration already asks, or if type is 'none'."
        ),
    )
    expected: int | str | None = Field(
        description="The expected answer, if there is a single one; null otherwise.",
    )
    choices: list[ChoiceOption] | None = Field(
        description=(
            "2-3 illustrated options when type is 'choose'; null otherwise. Each one "
            "must change something the child can see in the next beat, and none of "
            "them may be a wrong or losing pick."
        ),
    )


class Learning(_Strict):
    objective_id: str = Field(description="Objective catalog code, e.g. 'count_to_5'.")
    # ge/le validate the *returned* beat. They render as `minimum`/`maximum`,
    # which the structured-output subset rejects, so `beat_json_schema()` strips
    # them before the schema reaches a provider.
    difficulty: float = Field(
        ge=0.0,
        le=1.0,
        description="Continuous difficulty 0-1 within this objective.",
    )
    skill_evidence_key: str = Field(
        description="Key under which this beat's evidence is recorded.",
    )


class Beat(_Strict):
    """One beat of a story — the atomic unit the whole engine trades in."""

    beat_id: str = Field(description="Short id unique within the adventure, e.g. 'b3'.")
    narration: str = Field(description="What the companion narrates, in the child's voice-age.")
    scene: Scene
    interaction: Interaction
    learning: Learning
    emotion: str = Field(description="Intended emotional tone, e.g. 'playful_curious'.")
    continuity_refs: list[str] = Field(
        description=(
            "References to known entities, e.g. ['companion:pip', 'world:whispering_woods']."
        ),
    )
    next_hint: str = Field(
        description="Branching hint for the next beat, e.g. 'if child succeeds -> cross river'.",
    )


class StoryPlan(_Strict):
    """The skeleton one adventure is generated against (§8.1).

    Generated **once**, before the first beat, and then held constant. It is
    deliberately thin — a title, an ordered list of stages, and a few candidate
    directions — because a plan detailed enough to be a script would make the
    child's choices cosmetic, and because most of a pre-generated story is
    thrown away the moment they choose something else.

    `skeleton` is a list of plain sentences rather than typed stages: it is
    rendered into the prompt as prose (`prompting.assembler._plan_block`), never
    dispatched on, so structure here would be structure nothing consumes.
    """

    title: str = Field(description="The adventure's name, in words a 4-year-old would use.")
    skeleton: list[str] = Field(
        description=(
            "5-7 one-line stages in order, covering hook -> challenge -> climax -> resolution."
        ),
    )
    branch_themes: list[str] = Field(
        description="2-3 candidate directions the child's choices could take the story.",
    )


def strip_unsupported_keywords(node: Any) -> Any:
    """Recursively drop keywords the structured-output subset rejects.

    Providers validate the schema strictly, so one stray `minimum` deep inside a
    `$defs` entry fails the whole request. Walking the tree — rather than popping
    known offenders by path — means a constraint added to any future field is
    handled without anyone remembering this file exists.
    """
    if isinstance(node, dict):
        return {
            key: strip_unsupported_keywords(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(node, list):
        return [strip_unsupported_keywords(item) for item in node]
    return node


#: A ceiling on one field's description, not a target. The paragraph rule below
#: does the real work; this only exists so a future docstring with a
#: twelve-sentence opening paragraph cannot quietly put it all back.
#:
#: 400 rather than something tighter because the longest legitimate opening
#: paragraph in `Beat` — `CastMember.visual_descriptor`, three sentences of what a
#: permanent appearance may and may not contain — is 330 characters and every one
#: of them is an instruction. The first version of this capped at 240 and cut that
#: field off at "Not a human c", which is a worse prompt than the one it shrank.
MAX_DESCRIPTION_CHARS = 400


def trim_descriptions(node: Any) -> Any:
    """Keep the first paragraph of every `description`, drop the rationale.

    Pydantic turns each model's Python docstring into a schema `description`, and
    the docstrings in this file are written for the person maintaining them: they
    open with what the field is and then spend three paragraphs on why it exists,
    what the alternative was, and which review found the bug. All of that was
    being sent to the model, on every generation, twice — once inside the
    assembled system prompt and again as the provider's structured-output schema.

    It is both a cost problem and a quality problem, and the second is the reason
    to fix it rather than merely shrink it. §19.1 keeps instruction layers and
    data layers apart precisely so a model knows what it is being *told*; a
    paragraph explaining that asking a second model "what did that frog look
    like?" would pay twice is engineering rationale, and a model reading it as
    guidance about a field is the layering breaking down inside the contract.

    So: the opening paragraph survives, whitespace-collapsed and capped. That is
    the sentence written *for* the field. Everything after the first blank line is
    for us.

    Measured on `Beat`, rendered compactly: **6490 characters to 5184**, a fifth
    of the largest layer in every beat prompt, removing no instruction a model was
    meant to follow. (The other reduction landing today is
    `assembler._output_contract` dropping `indent=2`, which takes the same schema
    from 8775 to 6490 — a separate change, and worth keeping the two numbers
    apart: one saves whitespace, this one removes text that was actively
    misleading.)

    Applied in `json_schema_for`, which every schema we send passes through, so a
    new contract gets the same treatment without anyone remembering it.
    """
    if isinstance(node, dict):
        trimmed: dict[str, Any] = {}
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                summary = _first_paragraph(value)
                # A field whose whole docstring is rationale ends up with nothing
                # useful; drop the key rather than send an empty string.
                if summary:
                    trimmed[key] = summary
                continue
            trimmed[key] = trim_descriptions(value)
        return trimmed
    if isinstance(node, list):
        return [trim_descriptions(item) for item in node]
    return node


def _first_paragraph(text: str) -> str:
    """The text up to the first blank line, as one whitespace-collapsed line.

    Over-long paragraphs are cut at the last **sentence** that fits, never at a
    character count. A description that stops mid-word is a worse instruction than
    the long one it replaced — the model reads a broken clause as the whole rule —
    so if not even one sentence fits, the truncation falls back to a word boundary
    and an ellipsis, which at least reads as incomplete rather than as wrong.
    """
    paragraph = " ".join(text.strip().split("\n\n", 1)[0].split())
    if len(paragraph) <= MAX_DESCRIPTION_CHARS:
        return paragraph

    head = paragraph[:MAX_DESCRIPTION_CHARS]
    if (stop := head.rfind(". ")) != -1:
        return head[: stop + 1]
    return head.rsplit(" ", 1)[0] + "…"


def beat_json_schema() -> dict[str, Any]:
    """JSON Schema for `Beat`, shaped for provider structured-output APIs.

    The returned schema is deliberately *looser* than the Pydantic model: the
    0-1 bound on `difficulty` is enforced when the response is validated
    (`Beat.model_validate`), not asked of the provider.
    """
    return json_schema_for(Beat)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for any generated contract, shaped for provider APIs.

    Same treatment as `beat_json_schema`, for the other things we ask a model to
    return in a fixed shape — today `StoryPlan`, tomorrow whatever Day 5 needs.
    Sharing one function means a constraint added anywhere is stripped in every
    schema we send, not only in the beat's — and, from Day 14, that a docstring
    written for a maintainer is trimmed to its first paragraph before a model sees
    it (`trim_descriptions`, §19.9's prompt-minimisation lever).
    """
    schema = strip_unsupported_keywords(model.model_json_schema())
    return cast("dict[str, Any]", trim_descriptions(schema))
