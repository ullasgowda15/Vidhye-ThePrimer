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

import ast
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.common import flags
from apps.common.models import FeatureFlag

pytestmark = pytest.mark.django_db

BACKEND = Path(__file__).resolve().parents[3]

#: The layer that must not be configurable at runtime (§13.2). Everything under
#: it: the input scrub, the deterministic policy, the model classifier, the
#: pipeline that sequences them and the prompt assembly that carries the rules.
SAFETY_LAYER = BACKEND / "apps" / "safety"


def _imports(path: Path) -> set[str]:
    """Every module name imported by one file, as written.

    An AST walk rather than a regex, so a name inside a docstring or a comment
    about the boundary does not read as a breach of it — these files discuss
    flags and switches at length, which is exactly the trap a grep falls into.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


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


def test_no_flag_can_reach_a_state_a_boot_guard_forbids(settings: Any) -> None:
    """The same rule, one level down — a flag's *value*, not its name.

    The Day 1-15 audit found the naming rule holding perfectly while the one
    flag we do have could be set to two values a deploy could not: the safety
    grader's own model id (F-4, §13.3 collapsed into self-grading) and a `:free`
    prompt-reselling route (F-3, §13.8's promise broken at runtime). A registry
    with no safety-shaped *names* in it is not the same thing as a registry with
    no safety-shaped *reachable states*, and this asserts the second.

    Every declared flag with a validator is driven, so a flag added later with a
    validator that forgets one of these fails here rather than in production.
    """
    settings.AI_PROVIDER_ORDER = ["openrouter"]
    settings.OPENROUTER_API_KEY = "test-key"
    settings.ANTHROPIC_API_KEY = ""
    settings.AI_SAFETY_MODEL = "vendor/some-grader"

    validated = [flag for flag in flags.REGISTRY.values() if flag.validate is not None]
    assert validated, "no flag has a validator; this test would prove nothing"

    for flag in validated:
        assert flag.validate is not None  # narrowed for the type checker
        assert flag.validate(
            settings.AI_SAFETY_MODEL
        ), f"{flag.name} accepts the safety grader's own model id (13.3)"
        assert flag.validate(
            "vendor/some-model:free"
        ), f"{flag.name} accepts a training-permitted route (13.8)"


class TestTheRuleIsStructural:
    """The same rule as a fact about the import graph (Day 15 review, P2-1).

    The naming test above is a tripwire, not a proof: it reads the *names* in the
    registry, so it catches the earnest addition of `safety.classifier_enabled`
    and misses a flag called `story.gentle_mode` that happens to be read inside
    the output guard. Names are a convention; what actually has to be true is that
    **the safety layer does not consult runtime configuration at all.**

    So this asserts it where it cannot be talked around: no module under
    `apps/safety/` may import `apps.common.flags`. A flag that cannot be read
    inside the safety layer cannot weaken it, whatever it is called, and a person
    who wants to weaken it has to add an import that shows up in a diff next to
    this test's name.

    An AST walk rather than a grep, for the same reason
    `tests/test_analytics_pipeline.py` uses one: these files discuss flags and
    switches at length in prose, and a comment about the boundary must not read as
    a breach of it.

    **What this deliberately does not cover.** `media.service` and
    `story.ai.router` *do* read flags, and should: `media.illustrations` declines
    a new picture and `ai.beat_model` rolls back a model. Neither is a safety
    decision — the safety substitution is exempted at the call site
    (`get_or_generate_stock(paid=False)`, asserted by
    `story/tests/test_degradation.py::test_the_safety_substitution_is_never_refused`)
    and a rolled-back beat model is still graded by the same output guard and the
    same separately-pinned classifier.
    """

    def test_no_module_in_the_safety_layer_can_read_a_flag(self) -> None:
        breaches: list[str] = []
        for path in sorted(SAFETY_LAYER.rglob("*.py")):
            if "/tests/" in path.as_posix() or "/redteam/" in path.as_posix():
                continue
            for module in _imports(path):
                if module.startswith("apps.common.flags"):
                    breaches.append(f"{path.relative_to(BACKEND).as_posix()} imports {module}")

        assert not breaches, (
            "the safety layer must not consult runtime configuration — a flag it "
            "can read is a flag that can weaken it, whatever the flag is called. "
            "See apps/common/flags.py's docstring.\n  " + "\n  ".join(breaches)
        )

    def test_the_guard_is_reading_a_real_safety_layer(self) -> None:
        """The guard on the guard.

        An import scan over a directory that has moved passes vacuously and looks
        exactly like a clean bill of health. Both halves are checked: the path
        exists, and it still contains the modules this rule is actually about.
        """
        assert SAFETY_LAYER.is_dir(), f"the safety layer moved from {SAFETY_LAYER}"

        scanned = {
            path.relative_to(SAFETY_LAYER).as_posix()
            for path in SAFETY_LAYER.rglob("*.py")
            if "/tests/" not in path.as_posix()
        }
        for expected in ("pipeline.py", "policy.py", "scrub.py", "classifier.py"):
            assert expected in scanned, f"{expected} is not where this guard is looking"

    def test_it_would_notice_an_import(self) -> None:
        """And that the walk actually bites, rather than returning an empty set
        because it failed to parse anything."""
        assert any(
            module.startswith("apps.common.flags")
            for module in _imports(BACKEND / "apps" / "media" / "service.py")
        ), "the import walk cannot see a flag import that is definitely there"


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

    def test_the_grader_s_own_model_is_refused_on_write(self, settings: Any) -> None:
        """The audit's F-4. The one value this lever must never accept.

        `test_it_cannot_touch_the_safety_grader` above proves the flag cannot
        *repoint* the classifier. This is the other direction, and it was open:
        set the flag to the grader's own model id and the same weights write the
        beat and grade it — §13.3 collapsed into self-grading, with nothing
        logged, nothing refused, and `launch_check` green throughout because it
        compared the settings rather than the effective model.

        The moment it would happen is the moment the lever exists for: a bad model
        in production at 3am, with the safety model's slug the nearest known-good
        id to hand.
        """
        settings.ANTHROPIC_API_KEY = "test-key"
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        settings.AI_BEAT_MODEL = "claude-opus-5"
        settings.AI_SAFETY_MODEL = "claude-haiku-4-5-20251001"

        row = FeatureFlag(name="ai.beat_model", value=settings.AI_SAFETY_MODEL)
        with pytest.raises(ValidationError) as caught:
            row.full_clean()
        message = str(caught.value)
        assert "13.3" in message
        # Read in an admin form and in a terminal during an incident; the Windows
        # console mangles anything that is not ASCII.
        assert message.isascii(), message

        # Any *other* reachable model still rolls back, which is the whole point
        # of the lever — this must not become a flag that refuses everything.
        FeatureFlag(name="ai.beat_model", value="claude-sonnet-5").full_clean()

    def test_a_training_permitted_route_is_refused_on_write(self, settings: Any) -> None:
        """§13.8, at the one place a model id can be set without a deploy.

        `prod.py` refuses to boot on a `:free` child-data model. A runtime lever
        that could reach the state the boot guard forbids would be a hole in the
        boot guard — the deployment would be in it until the next restart, and
        the restart would then fail for reasons nobody connects to the flag.
        """
        settings.OPENROUTER_API_KEY = "test-key"
        settings.ANTHROPIC_API_KEY = ""
        settings.AI_PROVIDER_ORDER = ["openrouter"]

        row = FeatureFlag(name="ai.beat_model", value="vendor/some-model:free")
        with pytest.raises(ValidationError) as caught:
            row.full_clean()
        assert ":free" in str(caught.value)

        FeatureFlag(name="ai.beat_model", value="vendor/some-model").full_clean()

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
