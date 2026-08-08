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


class Scene(_Strict):
    setting: str = Field(description="Where this beat happens, in one line.")
    illustration_prompt: str = Field(
        description="Full prompt for the illustration model, including style cues.",
    )
    characters_present: list[str] = Field(
        description="Stable character keys visible in this beat, e.g. ['pip', 'frogs'].",
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
    choices: list[str] | None = Field(
        description="2-3 concrete choices when type is 'choose'; null otherwise.",
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
    schema we send, not only in the beat's.
    """
    return cast("dict[str, Any]", strip_unsupported_keywords(model.model_json_schema()))
