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
