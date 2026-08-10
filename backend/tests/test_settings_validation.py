"""Configuration that must fail the boot rather than fail quietly (§13.3, §19.3).

`AI_BEAT_MODEL`, `AI_SAFETY_MODEL` and `AI_MEMORY_MODEL` are provider-relative
and share one `AI_PROVIDER_ORDER`, which makes exactly one mistake both easy and
invisible: lead with `openrouter`, set the beat model to a slug, and leave one of
the others on its Anthropic default. Beats generate; the mispaired one 400s on
every single call. Day 7's review found this running on the founder's own machine
with the safety classifier as the casualty.

`AI_MEMORY_MODEL` (Day 8) is in the check for the same reason and defaults to
whatever `AI_SAFETY_MODEL` is, so it inherits a provider-correct id rather than
being a third thing to remember.

The check lives in `config.settings.base` and runs at import, so it is exercised
here by calling it directly against patched module globals — importing the
settings module twice inside one process is not a thing a test should do.
"""

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import base


@pytest.fixture
def pairing(monkeypatch: Any) -> Any:
    """Set the globals the check reads, then run it.

    `memory` defaults to the same value as `safety`, mirroring `base.py`'s own
    default — a test that left it on the developer's real `.env` value would fail
    on a machine configured for OpenRouter and pass in CI, which is precisely the
    class of bug this file exists for.
    """

    def run(
        *,
        order: list[str],
        beat: str = "claude-opus-5",
        safety: str = "claude-haiku-4-5-20251001",
        memory: str | None = None,
        anthropic_key: str = "",
        openrouter_key: str = "",
    ) -> None:
        monkeypatch.setattr(base, "AI_PROVIDER_ORDER", order)
        monkeypatch.setattr(base, "AI_BEAT_MODEL", beat)
        monkeypatch.setattr(base, "AI_SAFETY_MODEL", safety)
        monkeypatch.setattr(base, "AI_MEMORY_MODEL", safety if memory is None else memory)
        monkeypatch.setattr(base, "ANTHROPIC_API_KEY", anthropic_key)
        monkeypatch.setattr(base, "OPENROUTER_API_KEY", openrouter_key)
        base._check_model_pairing()

    return run


def test_the_founders_env_no_longer_boots(pairing: Any) -> None:
    """The exact configuration the review found: openrouter, Anthropic safety model."""
    with pytest.raises(ImproperlyConfigured) as caught:
        pairing(
            order=["openrouter", "anthropic"],
            beat="vendor/some-model",
            safety="claude-haiku-4-5-20251001",
            openrouter_key="test-key",
        )

    message = str(caught.value)
    assert "AI_SAFETY_MODEL" in message
    # It has to say *what to do*: a boot failure a founder cannot act on is a
    # boot failure they will work around by deleting the check.
    assert "vendor/model" in message
    # And it has to say the grader is not the place to economise (§13.3).
    assert "capable model" in message


def test_a_slug_under_anthropic_fails_too(pairing: Any) -> None:
    """The mirror image, which is the same bug pointing the other way."""
    with pytest.raises(ImproperlyConfigured, match="AI_BEAT_MODEL"):
        pairing(
            order=["anthropic"],
            beat="vendor/some-model",
            anthropic_key="test-key",
        )


def test_a_mispaired_memory_model_fails_too(pairing: Any) -> None:
    """The consolidator (§11.4) is in the check for the same reason the grader is.

    Its failure is quieter than the grader's and therefore worse to leave
    unchecked: `consolidation` treats an unreachable model as a *degradation*, so a
    mispaired id would mean every sitting consolidating on its rules half only,
    every night, with nothing but a worker warning to show for it.
    """
    with pytest.raises(ImproperlyConfigured, match="AI_MEMORY_MODEL"):
        pairing(
            order=["openrouter", "anthropic"],
            beat="vendor/some-model",
            safety="vendor/some-grader",
            memory="claude-haiku-4-5-20251001",
            openrouter_key="test-key",
        )


def test_the_memory_model_inherits_a_provider_correct_default(pairing: Any) -> None:
    """`base.py` defaults it to `AI_SAFETY_MODEL`, so it is never a third thing to set."""
    from config.settings import base as settings_base

    assert settings_base.AI_MEMORY_MODEL == settings_base.AI_SAFETY_MODEL

    pairing(
        order=["openrouter", "anthropic"],
        beat="vendor/some-model",
        safety="vendor/some-grader",
        openrouter_key="test-key",
    )


def test_a_matched_openrouter_pairing_boots(pairing: Any) -> None:
    pairing(
        order=["openrouter", "anthropic"],
        beat="vendor/some-model",
        safety="vendor/some-grader",
        openrouter_key="test-key",
    )


def test_the_shipping_default_boots(pairing: Any) -> None:
    pairing(order=["anthropic"], anthropic_key="test-key")


def test_the_check_follows_selection_rather_than_the_first_name_in_the_order(
    pairing: Any,
) -> None:
    """`openrouter` first with no OpenRouter key means anthropic is what runs.

    Reading `AI_PROVIDER_ORDER[0]` instead of mirroring
    `ModelRouter.select_provider` would reject a correct configuration here —
    and a boot check that cries wolf is one somebody removes.
    """
    pairing(order=["openrouter", "anthropic"], anthropic_key="test-key")


def test_no_credentials_at_all_is_not_a_pairing_error(pairing: Any) -> None:
    """CI has no keys. Nothing will be called, so nothing can be mispaired —
    and `select_provider` raises its own, clearer error if anything tries."""
    pairing(order=["openrouter", "anthropic"], beat="claude-opus-5")
