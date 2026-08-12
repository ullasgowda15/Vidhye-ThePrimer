"""The schema we actually put on the wire (PRODUCT.md §19.6).

`test_ai_router.py` and `test_hello_beat_command.py` both stub `generate_json`, so
neither ever sees the request a provider would receive. That gap shipped a schema
carrying `minimum`/`maximum` — keywords the structured-output subset rejects — which
no test caught and which would have 400'd on the first real call.

These tests assert on `beat_json_schema()` itself: the exact dict handed to a
provider, checked recursively, with no network involved.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from apps.story.ai.schemas import Beat, InteractionType, beat_json_schema
from tests.test_ai_router import VALID_BEAT

# Keywords outside the structured-output JSON-Schema subset. A provider rejects the
# whole request over any one of them, at any depth.
UNSUPPORTED_KEYWORDS = {
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


def walk(node: Any, path: str = "$") -> list[tuple[str, str]]:
    """Every (path, key) pair in a JSON-Schema-shaped structure."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((path, key))
            found += walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += walk(item, f"{path}[{index}]")
    return found


def test_schema_sent_to_providers_has_no_unsupported_keywords() -> None:
    offenders = [
        f"{path}.{key}" for path, key in walk(beat_json_schema()) if key in UNSUPPORTED_KEYWORDS
    ]
    assert offenders == [], (
        "beat_json_schema() would be rejected by a structured-output API. "
        f"Unsupported keywords found at: {offenders}"
    )


def test_raw_pydantic_schema_still_carries_the_constraint() -> None:
    # The guard above must be doing real work. If Pydantic ever stops emitting
    # `minimum` for `Field(ge=...)`, this test fails and tells us the stripping in
    # `beat_json_schema()` has become a no-op rather than a safeguard.
    raw = Beat.model_json_schema()
    assert raw["$defs"]["Learning"]["properties"]["difficulty"]["minimum"] == 0.0
    assert raw["$defs"]["Learning"]["properties"]["difficulty"]["maximum"] == 1.0


@pytest.mark.parametrize("difficulty", [-0.1, 1.1])
def test_returned_beat_is_still_validated_against_the_0_1_range(difficulty: float) -> None:
    # Stripping the constraint from the *request* must not weaken validation of the
    # *response* — that is the whole reason we strip rather than drop `ge`/`le`.
    payload = {**VALID_BEAT, "learning": {**VALID_BEAT["learning"], "difficulty": difficulty}}

    with pytest.raises(ValidationError):
        Beat.model_validate(payload)


def test_interaction_type_is_a_constrained_enum() -> None:
    # An enum lets the provider constrain generation instead of us hoping the prompt
    # holds, and makes Day 4's client dispatch exhaustive.
    schema = beat_json_schema()
    assert schema["$defs"]["InteractionType"]["enum"] == [
        "count",
        "choose",
        "say",
        "tap",
        "none",
    ]
    assert [t.value for t in InteractionType] == ["count", "choose", "say", "tap", "none"]


def test_interaction_type_rejects_an_unknown_value() -> None:
    payload = {**VALID_BEAT, "interaction": {**VALID_BEAT["interaction"], "type": "sing"}}

    with pytest.raises(ValidationError):
        Beat.model_validate(payload)


# ---------------------------------------------------------------------------
# Prompt minimisation (§19.9, §22.4) — Day 14
# ---------------------------------------------------------------------------
# Pydantic turns each model's docstring into a schema `description`, and the
# docstrings in `schemas.py` are written for the person maintaining them: an
# opening sentence about the field, then paragraphs of rationale, alternatives and
# which review found the bug. All of it was going to the model on every
# generation — twice, since the assembled system prompt carries the schema and the
# provider is handed it again for structured output.
#
# It is a cost problem and a quality problem, and the second is the reason to fix
# it: §19.1 separates instruction layers from data layers so a model knows what it
# is being *told*, and a paragraph of engineering rationale inside a field
# description is that separation breaking down inside the contract.


def test_descriptions_are_trimmed_to_their_first_paragraph() -> None:
    """The rationale is gone; the instruction is intact.

    `CastMember.visual_descriptor` is the field to check, because it is the
    longest legitimate opening paragraph in `Beat` — three sentences of what a
    permanent appearance may and may not contain, every one of them a rule.
    """
    descriptor = beat_json_schema()["$defs"]["CastMember"]["properties"]["visual_descriptor"]

    assert descriptor["description"].startswith("Permanent appearance only")
    # The whole instruction survives, to its last clause.
    assert "Not a human child." in descriptor["description"]
    # And the paragraph after it, which explains to a *reader* why the field is
    # generated with the beat rather than by a second model call, does not.
    assert "pay twice" not in descriptor["description"]


def test_no_description_is_cut_off_mid_word() -> None:
    """A description that stops mid-word is a worse instruction than the long one
    it replaced — the model reads a broken clause as the whole rule. The first
    version of the trimmer capped at a character count and ended this schema's
    longest field at "Not a human c"."""
    for path, key in walk(beat_json_schema()):
        if key != "description":
            continue
        value = _at(beat_json_schema(), f"{path}.{key}")
        assert isinstance(value, str)
        assert value.endswith((".", "!", "?", "…", ";", ":")), (path, value[-40:])


def _at(node: Any, path: str) -> Any:
    """Follow a `$.a.b` path produced by `walk`."""
    current = node
    for step in path.split(".")[1:]:
        current = current[step]
    return current


def test_the_schema_a_provider_receives_is_materially_smaller() -> None:
    """The number this change exists for (§22.4).

    Both halves are compared **compactly**, so this measures the trimming alone
    and not the separate `indent=2` removal in `assembler._output_contract`. At
    the time of writing: 6490 characters to 5184. Asserted as a threshold rather
    than an exact size, because the schema grows whenever a field is added and
    this test should fail when the *trimming* stops working, not when `Beat` gains
    a property.
    """
    import json

    from apps.story.ai.schemas import Beat, strip_unsupported_keywords

    untrimmed = json.dumps(
        strip_unsupported_keywords(Beat.model_json_schema()), sort_keys=True, separators=(",", ":")
    )
    trimmed = json.dumps(beat_json_schema(), sort_keys=True, separators=(",", ":"))

    assert len(trimmed) < len(untrimmed) * 0.85


def test_trimming_removes_no_field_and_no_constraint() -> None:
    """Descriptions are the only thing that changes.

    The trimmer walks every dict in the schema, so the failure to be afraid of is
    that it drops a `required` entry or a `type` somewhere deep in `$defs` and the
    provider starts accepting a shape we cannot validate.
    """
    from apps.story.ai.schemas import Beat, strip_unsupported_keywords

    def skeleton(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: skeleton(v) for k, v in node.items() if k != "description"}
        if isinstance(node, list):
            return [skeleton(item) for item in node]
        return node

    assert skeleton(beat_json_schema()) == skeleton(
        strip_unsupported_keywords(Beat.model_json_schema())
    )
