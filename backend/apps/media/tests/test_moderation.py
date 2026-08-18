"""The image gate (PRODUCT.md §13.8, Day 7).

Day 3 shipped `scan` as a structural check with a `TODO` on it and a test file
that said so. This is the other half: the moderation call, and — more importantly
— what the scan does when it cannot make one.

The property under test throughout is that **there is no configuration in which
an unscanned asset is reported as scanned**. A gate that quietly turns itself off
is worse than no gate, because the log and the `Media` row both say everything is
fine.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.media import moderation
from apps.media.models import SafetyStatus
from apps.media.safety import scan

PNG = b"\x89PNG\r\n\x1a\n" + b"." * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"." * 64


class _Moderator:
    """A stand-in for the vision call, with the verdict chosen by the test."""

    name = "stub"

    def __init__(self, *, safe: bool = True, reason: str = "", boom: bool = False) -> None:
        self.safe, self.reason, self.boom = safe, reason, boom
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def classify(self, data: bytes, content_type: str) -> tuple[bool, str]:
        self.calls += 1
        if self.boom:
            raise RuntimeError("provider is down")
        return self.safe, self.reason


@pytest.fixture
def gate_on(settings: Any, monkeypatch: Any) -> Any:
    """Turn the gate on with a moderator the test controls."""

    def install(**kwargs: Any) -> _Moderator:
        settings.IMAGE_MODERATION = moderation.AUTO
        stub = _Moderator(**kwargs)
        monkeypatch.setattr(moderation, "select_moderator", lambda: stub)
        return stub

    return install


def test_a_clean_picture_passes_and_says_it_was_moderated(gate_on: Any) -> None:
    stub = gate_on(safe=True)

    verdict = scan(PNG)

    assert verdict.is_safe
    assert verdict.moderated is True
    assert stub.calls == 1


def test_an_unsafe_picture_is_blocked_before_it_is_stored(gate_on: Any) -> None:
    gate_on(safe=False, reason="a frightening figure")

    verdict = scan(PNG)

    assert verdict.status == SafetyStatus.BLOCKED
    assert verdict.is_safe is False
    assert "frightening" in verdict.reason


def test_a_moderator_that_errors_fails_closed(gate_on: Any) -> None:
    """The opposite posture to `apps.safety.classifier`, and deliberately so.

    Refusing a *beat* when the guard is down would cost the child their story.
    Refusing a *picture* costs one illustration on a beat that is still fully
    playable (§4.9) and the caller substitutes stock art — so here the cautious
    answer is cheap and it is the one we take.
    """
    gate_on(boom=True)

    verdict = scan(PNG)

    assert verdict.status == SafetyStatus.FLAGGED
    assert verdict.is_safe is False
    assert "moderator_error" in verdict.reason


def test_an_unconfigured_gate_passes_the_asset_but_never_claims_it_moderated(
    settings: Any,
) -> None:
    """The honest degradation. `moderated` is the whole point of the field.

    Blocking every picture when no key is set would make offline development and
    CI impossible; reporting the structural check as a moderation pass would make
    an expired key invisible. So the asset is served and the verdict says plainly
    that the strong check did not run.
    """
    settings.IMAGE_MODERATION = moderation.OFF

    verdict = scan(PNG)

    assert verdict.is_safe
    assert verdict.moderated is False


def test_audio_and_svg_are_not_sent_to_a_vision_model(gate_on: Any) -> None:
    """A narration clip has no pixels, and the SVGs are our own vetted stock."""
    stub = gate_on(safe=True)

    assert scan(WAV).moderated is False
    assert scan(SVG).moderated is False
    assert stub.calls == 0


def test_the_structural_check_still_runs_first(gate_on: Any, settings: Any) -> None:
    """Bytes we cannot even identify are not worth a vision call."""
    stub = gate_on(safe=True)

    assert scan(b"").status == SafetyStatus.BLOCKED
    assert scan(b"\x00\x01\x02\x03").status == SafetyStatus.FLAGGED

    settings.MEDIA_MAX_BYTES = 8
    assert scan(PNG).status == SafetyStatus.BLOCKED
    assert stub.calls == 0


def test_a_moderator_that_answers_in_an_unexpected_shape_is_not_a_pass() -> None:
    """A classifier whose unparseable answers mean "fine" is not a classifier."""

    class Vague:
        def __init__(self) -> None:
            self.stop_reason = None
            self.content = [type("B", (), {"type": "text", "text": "hmm, hard to say"})()]

    verdict = moderation.ClaudeImageModerator(api_key="k")._verdict_from(Vague())
    assert verdict == (False, "hmm, hard to say")


def test_the_gate_is_off_when_no_credential_is_present(settings: Any) -> None:
    settings.IMAGE_MODERATION = moderation.AUTO
    settings.ANTHROPIC_API_KEY = ""

    assert moderation.select_moderator() is None


# ---------------------------------------------------------------------------
# The gate under Google (Vertex Gemini), with no Anthropic key
# ---------------------------------------------------------------------------
# The mitigation the Day-1-15 audit called unproven. `IMAGE_MODERATION=auto` on a
# deployment with no `ANTHROPIC_API_KEY` degraded to the structural check, which
# means the vision layer behind the F-1/F-2 companion-injection fix **did not
# run** — while the setting said `auto` and every scan logged
# `moderation=absent` into a file nobody reads. These tests are about the one
# question that follows: on the all-Google configuration, does a real vision call
# actually happen?


@pytest.fixture
def google_creds(settings: Any) -> None:
    """Vertex configured, Anthropic absent — the shipping all-Google shape."""
    settings.IMAGE_MODERATION = moderation.AUTO
    settings.ANTHROPIC_API_KEY = ""
    settings.GOOGLE_CLOUD_PROJECT = "primer-test"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/service-account.json"
    settings.IMAGE_MODERATION_MODEL = "gemini-2.5-flash-lite"


def _gemini_reply(
    text: str = "", *, finish: str = "STOP", block: str = "", candidates: bool = True
) -> Any:
    """One Gemini response, built from the real SDK types.

    The real types rather than a stub, so an attribute `_verdict_from` reads and
    the SDK does not have fails here instead of on a child's illustration.
    """
    from google.genai import types

    return types.GenerateContentResponse(
        candidates=(
            [
                types.Candidate(
                    content=(
                        types.Content(role="model", parts=[types.Part(text=text)]) if text else None
                    ),
                    finish_reason=types.FinishReason(finish),
                )
            ]
            if candidates
            else []
        ),
        prompt_feedback=(
            types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason(block))
            if block
            else None
        ),
    )


@pytest.fixture
def gemini_vision(monkeypatch: Any) -> Any:
    """Hand the test control of what the Vertex vision call answers."""

    def install(answer: Any) -> Any:
        from apps.common import vertex

        class FakeVertex:
            def __init__(self) -> None:
                self.models = self
                self.calls: list[dict[str, Any]] = []

            def generate_content(self, **kwargs: Any) -> Any:
                self.calls.append(kwargs)
                if isinstance(answer, Exception):
                    raise answer
                return answer

        client = FakeVertex()
        monkeypatch.setattr(vertex, "client", lambda: client)
        return client

    return install


class TestWhichModeratorRuns:
    def test_google_credentials_alone_are_enough_to_get_a_real_gate(
        self, google_creds: None
    ) -> None:
        """The audit's finding, closed. No Anthropic key, `auto`, and the moderator
        is a vision model rather than `None`."""
        chosen = moderation.select_moderator()

        assert isinstance(chosen, moderation.GeminiImageModerator)
        assert chosen.is_configured() is True

    def test_an_anthropic_deployment_is_unchanged(self, settings: Any) -> None:
        """`auto` tries Anthropic first, so adding Gemini moved nothing for a
        deployment that already had a key. The gate is not a place to surprise
        anyone."""
        settings.IMAGE_MODERATION = moderation.AUTO
        settings.ANTHROPIC_API_KEY = "test-key"
        settings.GOOGLE_CLOUD_PROJECT = "primer-test"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"

        assert isinstance(moderation.select_moderator(), moderation.ClaudeImageModerator)

    def test_naming_a_provider_pins_it(self, settings: Any) -> None:
        settings.IMAGE_MODERATION = moderation.AUTO
        settings.ANTHROPIC_API_KEY = "test-key"
        settings.GOOGLE_CLOUD_PROJECT = "primer-test"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"
        settings.IMAGE_MODERATION_PROVIDER = "google"

        assert isinstance(moderation.select_moderator(), moderation.GeminiImageModerator)

    def test_a_named_provider_with_no_credentials_leaves_the_gate_absent(
        self, settings: Any
    ) -> None:
        """It does **not** quietly fall back to the other one. An operator who
        asked for Gemini and silently got Claude has a deployment that is not the
        one they described — and the honest answer to "I asked for a gate I did
        not configure" is the `moderation=absent` the caller already logs."""
        settings.IMAGE_MODERATION = moderation.AUTO
        settings.ANTHROPIC_API_KEY = "test-key"
        settings.GOOGLE_CLOUD_PROJECT = ""
        settings.IMAGE_MODERATION_PROVIDER = "google"

        assert moderation.select_moderator() is None

    def test_an_unknown_provider_name_is_not_a_silent_pass(self, settings: Any) -> None:
        settings.IMAGE_MODERATION = moderation.AUTO
        settings.IMAGE_MODERATION_PROVIDER = "gemini"  # not a registered name

        assert moderation.select_moderator() is None

    def test_off_still_wins_over_everything(self, google_creds: None, settings: Any) -> None:
        """`prod.py` refuses to boot on `off`; where it *is* legitimate (CI,
        offline development) it must still beat a configured provider."""
        settings.IMAGE_MODERATION = moderation.OFF
        settings.IMAGE_MODERATION_PROVIDER = "google"

        assert moderation.select_moderator() is None


class TestTheGeminiVerdict:
    def test_a_benign_illustration_passes_through_the_whole_scan(
        self, google_creds: None, gemini_vision: Any
    ) -> None:
        """End to end through `media.safety.scan`: `moderated=True` is the
        assertion that matters — it is the field that distinguishes a real gate
        from the structural fallback."""
        gemini_vision(_gemini_reply("SAFE"))

        verdict = scan(PNG)

        assert verdict.is_safe
        assert verdict.moderated is True

    def test_an_unsafe_illustration_is_blocked_with_its_reason(
        self, google_creds: None, gemini_vision: Any
    ) -> None:
        gemini_vision(_gemini_reply("UNSAFE\na frightening figure with bared teeth"))

        verdict = scan(PNG)

        assert verdict.status == SafetyStatus.BLOCKED
        assert "frightening" in verdict.reason

    def test_a_provider_failure_fails_closed(self, google_creds: None, gemini_vision: Any) -> None:
        """Fail closed: a stock illustration is a cheap outcome and an unmoderated
        one is not. The exception must reach `moderation.classify`'s handler as an
        exception — a moderator that swallowed it would be a permanent pass."""
        gemini_vision(RuntimeError("vertex is down"))

        verdict = scan(PNG)

        assert verdict.status == SafetyStatus.FLAGGED
        assert verdict.is_safe is False
        assert "moderator_error" in verdict.reason

    @pytest.mark.parametrize(
        "response",
        [_gemini_reply(block="SAFETY", candidates=False), _gemini_reply(finish="IMAGE_SAFETY")],
        ids=["prompt-blocked", "image-safety"],
    )
    def test_the_providers_own_refusal_is_itself_a_block(
        self, google_creds: None, gemini_vision: Any, response: Any
    ) -> None:
        """A vision model declining to describe a picture has answered the question
        we asked. The safe reading is "do not show this to a child" — not an
        error, and certainly not a pass."""
        gemini_vision(response)

        verdict = scan(PNG)

        assert verdict.status == SafetyStatus.BLOCKED
        assert "provider_refused" in verdict.reason

    def test_an_empty_answer_is_unavailable_rather_than_safe(self) -> None:
        """Which the caller fails closed. This is the branch a thinking model
        would land on if `thinking_budget` were not zero — see the class docstring
        — so it is worth being sure it is not a pass."""
        with pytest.raises(moderation.ModerationUnavailable):
            moderation.GeminiImageModerator._verdict_from(_gemini_reply(""))

    def test_an_answer_in_an_unexpected_shape_is_not_a_pass(self) -> None:
        assert moderation.GeminiImageModerator._verdict_from(_gemini_reply("hmm, hard to say")) == (
            False,
            "hmm, hard to say",
        )

    def test_thinking_is_disabled_so_the_hundred_token_budget_reaches_the_verdict(
        self, google_creds: None, gemini_vision: Any
    ) -> None:
        """The failure this guards against is silent and total: a Gemini 2.5 model
        with dynamic thinking spends the whole 100-token budget reasoning, returns
        no text, and every generated illustration in the product is flagged and
        replaced with stock art."""
        client = gemini_vision(_gemini_reply("SAFE"))
        scan(PNG)

        config = client.calls[0]["config"]
        assert config.thinking_config.thinking_budget == 0
        assert config.max_output_tokens == moderation.GeminiImageModerator.MAX_TOKENS
        assert config.system_instruction == moderation.MODERATION_PROMPT

    def test_the_picture_is_sent_as_bytes_with_its_real_content_type(
        self, google_creds: None, gemini_vision: Any
    ) -> None:
        client = gemini_vision(_gemini_reply("SAFE"))
        scan(PNG)

        parts = client.calls[0]["contents"][0].parts
        assert parts[0].inline_data.mime_type == "image/png"
        assert parts[0].inline_data.data == PNG
