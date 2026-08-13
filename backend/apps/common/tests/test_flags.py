"""Feature flags (PRODUCT.md §16.10, Day 15).

Three things are asserted here, in order of how much they matter.

**The registry cannot grow a safety switch.** That is the first test in the file
on purpose. Everything else here is about a lever working; this one is about the
lever that must never exist.

**A rollback happens without a deploy, and the value is validated before it is
stored.** The `ai.beat_model` flag exists to *fix* a bad model change, so a lever
that will accept an unreachable model id is a lever that breaks the product at
the moment it is being used.

**Reading a flag cannot raise.** These are read on a child's story path.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.common import flags
from apps.common.models import FeatureFlag

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_flag_cache() -> Any:
    """Flags are cached for `CACHE_TTL_S`, and the cache is process-global."""
    flags.invalidate()
    cache.clear()
    yield
    flags.invalidate()
    cache.clear()


# ---------------------------------------------------------------------------
# The rule that is not about any one flag
# ---------------------------------------------------------------------------
#: Words that would appear in the name of a flag that turned a guard off. Crude
#: on purpose — the point is to make somebody stop and read `flags.py`'s docstring
#: before they add `safety.classifier_enabled`, not to be a semantic analysis.
_FORBIDDEN = ("safety", "classifier", "guard", "moderation", "scrub", "fallback", "ceiling")


def test_no_flag_can_switch_off_a_safety_guard() -> None:
    """§13's non-negotiable, enforced against the registry rather than trusted.

    Safety configuration lives in settings, where changing it is a deploy and a
    code review, and where `prod.py` refuses to boot on the dangerous values. A
    runtime switch that makes a children's product less safe is a switch that
    will eventually be flipped by somebody with a deadline and an outage.

    If a genuinely safety-adjacent flag is ever needed, this test is the
    conversation: change it deliberately, in a commit that says why.
    """
    offenders = [
        name for name in flags.REGISTRY if any(word in name.lower() for word in _FORBIDDEN)
    ]
    assert offenders == [], (
        f"{offenders} look like safety switches. See apps/common/flags.py's docstring: "
        f"the safety layer is configured in settings, not at runtime."
    )


def test_every_declared_flag_ships_in_its_normal_state() -> None:
    """A flag whose default is the degraded setting is a product that ships degraded.

    Every default here must be "the product working normally", because every
    failure path in `flags.value` — no row, no settings entry, no database —
    lands on the default.
    """
    assert flags.REGISTRY["media.illustrations"].default == "on"
    assert flags.REGISTRY["story.prefetch_next_beat"].default == "on"
    # Empty means "use AI_BEAT_MODEL", i.e. no override at all.
    assert flags.REGISTRY["ai.beat_model"].default == ""


def test_an_undeclared_flag_is_an_error_and_not_a_default() -> None:
    """A typo at a call site must fail in a test, not read as "off" in production."""
    with pytest.raises(KeyError):
        flags.value("media.illustrationz")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
class TestResolution:
    def test_a_row_beats_settings_which_beats_the_default(self, settings: Any) -> None:
        assert flags.value("media.illustrations") == "on"

        settings.FEATURE_FLAGS = {"media.illustrations": "off"}
        flags.invalidate()
        assert flags.enabled("media.illustrations") is False

        # The row is on top because the whole point of it is to outrun a deploy.
        FeatureFlag.objects.create(name="media.illustrations", value="on")
        assert flags.enabled("media.illustrations") is True

    def test_saving_a_row_takes_effect_in_this_process_immediately(self) -> None:
        """`CACHE_TTL_S` is the bound for *other* processes, not for the writer.

        Without `FeatureFlag._forget` a flag flipped in the admin would appear not
        to work for ten seconds on the very worker the operator is watching.
        """
        assert flags.enabled("media.illustrations") is True
        row = FeatureFlag.objects.create(name="media.illustrations", value="off")
        assert flags.enabled("media.illustrations") is False

        row.delete()
        assert flags.enabled("media.illustrations") is True

    def test_an_unparseable_value_reads_as_the_default_not_as_off(self, caplog: Any) -> None:
        """A fat-fingered admin entry must not silently switch a feature off."""
        FeatureFlag.objects.create(name="media.illustrations", value="maybe")
        assert flags.enabled("media.illustrations") is True
        assert "flags.unparseable" in caplog.text

    def test_a_broken_database_reads_as_the_default_and_never_raises(
        self, monkeypatch: Any
    ) -> None:
        """The rule that matters on a child's path (§13)."""

        def explode(_flag: Any) -> str:
            raise RuntimeError("database is down")

        monkeypatch.setattr(flags, "_resolve", explode)
        assert flags.enabled("media.illustrations") is True
        assert flags.value("ai.beat_model") == ""


# ---------------------------------------------------------------------------
# The rollback lever itself
# ---------------------------------------------------------------------------
class TestTheModelRollbackLever:
    def test_the_router_uses_the_flag_when_no_caller_pinned_a_model(self, settings: Any) -> None:
        from apps.story.ai.router import ModelRouter, model_label

        settings.ANTHROPIC_API_KEY = "test-key"
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        settings.AI_BEAT_MODEL = "claude-opus-5"

        # `model_label` rather than `.model`: it is how the product itself reads a
        # provider's model (the `StructuredProvider` protocol deliberately does not
        # declare the attribute), and it is what gets stamped on the `Beat` row —
        # so this asserts against the string a reviewer would later find in the
        # database, not against an implementation detail.
        assert model_label(ModelRouter.from_settings().select_provider()) == (
            "anthropic:claude-opus-5"
        )

        FeatureFlag.objects.create(name="ai.beat_model", value="claude-haiku-4-5-20251001")
        assert model_label(ModelRouter.from_settings().select_provider()) == (
            "anthropic:claude-haiku-4-5-20251001"
        )

    def test_it_cannot_touch_the_safety_grader(self, settings: Any) -> None:
        """§13.3's "a different model, on purpose" is not a runtime knob.

        The classifier pins `AI_SAFETY_MODEL` by passing `model=`, and a caller
        that pinned a model is a caller the flag does not apply to. Without this
        property, one admin form field could point the generator and its
        independent grader at the same weights.
        """
        from apps.story.ai.router import ModelRouter, model_label

        settings.ANTHROPIC_API_KEY = "test-key"
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        FeatureFlag.objects.create(name="ai.beat_model", value="claude-opus-5")

        pinned = ModelRouter.from_settings(model="claude-haiku-4-5-20251001")
        assert model_label(pinned.select_provider()) == "anthropic:claude-haiku-4-5-20251001"

    def test_a_model_id_the_provider_cannot_call_is_refused_on_write(self, settings: Any) -> None:
        """The lever validates before it stores, by the same rule the boot check uses.

        An OpenRouter deployment handed a bare Claude id answers 400 to every call
        — which is the failure this flag exists to *recover* from.
        """
        settings.OPENROUTER_API_KEY = "test-key"
        settings.ANTHROPIC_API_KEY = ""
        settings.AI_PROVIDER_ORDER = ["openrouter"]

        row = FeatureFlag(name="ai.beat_model", value="claude-opus-5")
        with pytest.raises(ValidationError) as caught:
            row.full_clean()
        assert "not an OpenRouter model slug" in str(caught.value)

        # And the shape that *is* right for this deployment goes through.
        FeatureFlag(name="ai.beat_model", value="anthropic/claude-opus-5").full_clean()

    def test_clearing_the_flag_returns_to_the_setting(self, settings: Any) -> None:
        from apps.story.ai.router import ModelRouter, model_label

        settings.ANTHROPIC_API_KEY = "test-key"
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        settings.AI_BEAT_MODEL = "claude-opus-5"
        FeatureFlag.objects.create(name="ai.beat_model", value="claude-haiku-4-5-20251001")

        call_command("flags", "clear", "ai.beat_model", verbosity=0)

        assert not FeatureFlag.objects.filter(name="ai.beat_model").exists()
        assert model_label(ModelRouter.from_settings().select_provider()) == (
            "anthropic:claude-opus-5"
        )


class TestTheCommand:
    def test_set_refuses_an_undeclared_flag(self) -> None:
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="not a declared flag"):
            call_command("flags", "set", "safety.off", "1", verbosity=0)

    def test_set_stores_the_note_and_who(self) -> None:
        call_command(
            "flags",
            "set",
            "media.illustrations",
            "off",
            "--note",
            "cloudflare 5xx storm",
            "--by",
            "priya",
            verbosity=0,
        )
        row = FeatureFlag.objects.get(name="media.illustrations")
        assert (row.value, row.note, row.set_by) == ("off", "cloudflare 5xx storm", "priya")
        assert flags.enabled("media.illustrations") is False

    def test_the_snapshot_says_where_each_value_came_from(self, settings: Any) -> None:
        """ "Off" and "off because somebody set a row on Tuesday" are different
        situations, and a list of values alone cannot tell them apart."""
        settings.FEATURE_FLAGS = {"story.prefetch_next_beat": "off"}
        FeatureFlag.objects.create(name="media.illustrations", value="off")

        sources = {row["name"]: row["source"] for row in flags.snapshot()}
        assert sources == {
            "ai.beat_model": "default",
            "media.illustrations": "database",
            "story.prefetch_next_beat": "settings",
        }
