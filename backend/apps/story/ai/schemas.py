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

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class Interaction(_Strict):
    type: str = Field(
        description="What the child is asked to do: count, choose, say, tap, none.",
    )
    prompt_audio: str | None = Field(
        description="What the companion says aloud to invite the interaction; null if none.",
    )
    expected: int | str | None = Field(
        description="The expected answer, if there is a single one; null otherwise.",
    )
    choices: list[str] | None = Field(
        description="2-3 concrete choices when type is 'choose'; null otherwise.",
    )


class Learning(_Strict):
    objective_id: str = Field(description="Objective catalog code, e.g. 'count_to_5'.")
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


def beat_json_schema() -> dict[str, Any]:
    """JSON Schema for `Beat`, shaped for provider structured-output APIs."""
    return Beat.model_json_schema()
