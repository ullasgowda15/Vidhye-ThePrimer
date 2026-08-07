"""Pure helpers behind `manage.py smoke_ai`.

The command itself makes live calls and is deliberately outside CI and the default
test run. Its decision-making is not: picking a model and judging a beat are the
parts that can be wrong in a way that wastes the founder's time, so they are
covered here with no network and no key.
"""

from typing import Any

import httpx
import pytest

from apps.story.ai.schemas import Beat
from apps.story.management.commands import smoke_ai
from apps.story.management.commands.smoke_ai import (
    MAX_DETAIL_CHARS,
    SmokeFailure,
    assert_usable_beat,
    pick_free_model,
    scrub,
)
from tests.test_ai_router import VALID_BEAT


def model(
    slug: str,
    *,
    prompt: str = "0",
    completion: str = "0",
    supports: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": slug,
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": supports or [],
        "architecture": {
            "input_modalities": inputs or ["text"],
            "output_modalities": outputs or ["text"],
        },
    }


@pytest.fixture
def catalogue(monkeypatch):
    """Serve a scripted OpenRouter model list instead of calling the real one."""

    def serve(entries: list[dict[str, Any]] | Exception) -> None:
        def fake_get(url: str, timeout: float = 0) -> Any:
            if isinstance(entries, Exception):
                raise entries
            return httpx.Response(
                200,
                json={"data": entries},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(smoke_ai.httpx, "get", fake_get)

    return serve


def test_prefers_a_model_that_advertises_structured_outputs(catalogue) -> None:
    catalogue(
        [
            model("vendor/plain:free"),
            model("vendor/tools:free", supports=["tools"]),
            model("vendor/strict:free", supports=["structured_outputs", "tools"]),
        ]
    )

    # Check A is only meaningful if it can exercise the strict path, so the pick
    # is ranked by structure support rather than by whatever is listed first.
    assert pick_free_model() == "vendor/strict:free"


def test_ignores_paid_models(catalogue) -> None:
    catalogue(
        [
            model("vendor/cheap-but-paid", prompt="0.0000012", supports=["structured_outputs"]),
            model("vendor/output-costs", completion="0.000003", supports=["structured_outputs"]),
            model("vendor/actually-free:free", supports=["tools"]),
        ]
    )

    assert pick_free_model() == "vendor/actually-free:free"


def test_ignores_models_that_do_not_return_text(catalogue) -> None:
    # The free tier carries image and music models. One of them advertising
    # `response_format` would otherwise out-rank a perfectly good text model and
    # produce a baffling failure.
    catalogue(
        [
            model(
                "vendor/music:free",
                supports=["structured_outputs", "response_format"],
                outputs=["audio"],
            ),
            model("vendor/vision-only:free", supports=["tools"], inputs=["image"]),
            model("vendor/text:free", supports=["tools"]),
        ]
    )

    assert pick_free_model() == "vendor/text:free"


def test_says_what_to_do_when_nothing_is_free(catalogue) -> None:
    catalogue([model("vendor/paid", prompt="0.001", completion="0.002")])

    with pytest.raises(SmokeFailure, match="AI_BEAT_MODEL"):
        pick_free_model()


def test_says_what_to_do_when_the_catalogue_is_unreachable(catalogue) -> None:
    catalogue(httpx.ConnectError("no route to host"))

    with pytest.raises(SmokeFailure, match="AI_BEAT_MODEL"):
        pick_free_model()


def test_scrub_redacts_account_identifiers_from_provider_errors() -> None:
    # A real OpenRouter 400 body. A failing smoke run is exactly what gets pasted
    # into an issue, so nothing identifying should survive the summary table.
    raw = (
        "BadRequestError: Error code: 400 - {'error': {'message': 'bad model', "
        "'code': 400}, 'user_id': 'user_3EqGy99wuyHFz6o7LkSJ3nqRUWn'}"
    )

    cleaned = scrub(raw)

    assert "user_3EqGy99wuyHFz6o7LkSJ3nqRUWn" not in cleaned
    assert "<redacted>" in cleaned
    assert "bad model" in cleaned  # the actionable part survives


@pytest.mark.parametrize(
    "raw",
    [
        "api_key: sk-or-v1-abcdef",
        "{'token': 'ghp_secret'}",
        'Authorization="Bearer abc123"',
    ],
)
def test_scrub_redacts_credential_shaped_fields(raw: str) -> None:
    cleaned = scrub(raw)

    for secret in ("sk-or-v1-abcdef", "ghp_secret", "Bearer abc123"):
        assert secret not in cleaned


def test_scrub_clips_a_runaway_error() -> None:
    cleaned = scrub("x" * 5000)

    assert len(cleaned) <= MAX_DETAIL_CHARS + len("...")


def test_accepts_a_well_formed_beat() -> None:
    assert_usable_beat(Beat.model_validate(VALID_BEAT))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("narration", "   "),
        ("beat_id", ""),
        ("emotion", ""),
        ("next_hint", ""),
    ],
)
def test_rejects_a_beat_with_a_blank_required_field(field: str, value: str) -> None:
    # Schema-valid but useless: an empty narration passes validation and gives the
    # child nothing. This is the gap between "parses" and "is a story".
    beat = Beat.model_validate({**VALID_BEAT, field: value})

    with pytest.raises(SmokeFailure, match=field):
        assert_usable_beat(beat)


def test_rejects_a_beat_with_no_characters_in_the_scene() -> None:
    beat = Beat.model_validate(
        {**VALID_BEAT, "scene": {**VALID_BEAT["scene"], "characters_present": []}}
    )

    with pytest.raises(SmokeFailure, match="characters_present"):
        assert_usable_beat(beat)
