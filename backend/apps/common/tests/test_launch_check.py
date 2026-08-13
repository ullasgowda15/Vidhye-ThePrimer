"""The launch preflight (PRODUCT.md §13.2, §16.10, §18.8; Day 15).

A preflight that cannot fail is a ceremony. So what is tested here is not "it
prints a list" but that **each check actually catches the thing it names** — one
test per misconfiguration, each one making the deployment genuinely wrong and
asserting the command refuses it.

The last two tests are about the command's own honesty: `--strict` must convert
warnings into refusals (that is what a release pipeline runs), and the exit code
must be non-zero, because a preflight that prints red text and returns success is
a preflight that a script will happily deploy past.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def run(**options: Any) -> tuple[str, int]:
    """Run the preflight, capturing its output and its exit status."""
    out = io.StringIO()
    try:
        call_command("launch_check", stdout=out, stderr=out, **options)
        return out.getvalue(), 0
    except SystemExit as exit_code:
        return out.getvalue(), int(exit_code.code or 0)


@pytest.fixture
def launchable(settings: Any, db: Any) -> None:
    """A deployment that should pass: every check's happy path.

    Assembled rather than mocked, so a new check added to the command shows up
    here as a failure to be thought about rather than as a silently-passing one.
    """
    settings.SAFETY_CLASSIFIER_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.AI_PROVIDER_ORDER = ["anthropic"]
    settings.AI_BEAT_MODEL = "claude-opus-5"
    settings.AI_SAFETY_MODEL = "claude-haiku-4-5-20251001"
    settings.IMAGE_MODERATION = "auto"
    settings.MEMORY_ENCRYPTION_KEY = "6H0y0AGRTP-cKQ0RRW9x8p2yh4Y3iCJqQyLwLQ4qSAI="
    settings.MEDIA_STORAGE = "s3"
    settings.INFERENCE_BUDGET_USD_PER_WINDOW = 25.0
    get_user_model().objects.create_user(
        email="ops@example.com", password="storybook-primer-42", is_staff=True
    )


class TestEachCheckCatchesItsOwnFailure:
    def test_a_switched_off_classifier_blocks_the_launch(
        self, launchable: None, settings: Any
    ) -> None:
        settings.SAFETY_CLASSIFIER_ENABLED = False
        output, code = run()
        assert "FAIL  safety.classifier" in output
        assert code == 1

    def test_a_grader_with_no_provider_blocks_the_launch(
        self, launchable: None, settings: Any
    ) -> None:
        """A classifier switched on with nothing to call is not a classifier — it
        is a degradation on every beat, which `prod.py` deliberately does not
        refuse to boot on but nobody should *launch* into."""
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENROUTER_API_KEY = ""
        output, code = run()
        assert "FAIL  safety.grader_reachable" in output
        assert code == 1

    def test_the_writer_grading_itself_blocks_the_launch(
        self, launchable: None, settings: Any
    ) -> None:
        """§13.3: "a model grading its own output is weak"."""
        settings.AI_SAFETY_MODEL = settings.AI_BEAT_MODEL
        output, code = run()
        assert "FAIL  safety.grader_is_separate" in output
        assert code == 1

    def test_no_spend_cap_blocks_the_launch(self, launchable: None, settings: Any) -> None:
        settings.INFERENCE_BUDGET_USD_PER_WINDOW = 0.0
        output, code = run()
        assert "FAIL  spend.breaker" in output
        assert code == 1

    def test_an_unreachable_incident_queue_blocks_the_launch(self, launchable: None) -> None:
        """§13.11's queue is only a queue if somebody can open it."""
        get_user_model().objects.filter(is_staff=True).update(is_staff=False)
        output, code = run()
        assert "FAIL  monitoring.incident_queue" in output
        assert code == 1

    def test_an_unreviewed_serious_incident_blocks_the_launch(
        self, launchable: None, child: Any
    ) -> None:
        """§22.5: a serious safety incident is a stop-the-line event. Launching
        with one still open is the definition of not stopping the line."""
        from apps.safety.models import Action, SafetyIncident, Severity

        SafetyIncident.objects.create(
            child=child,
            category="violence",
            severity=Severity.HIGH,
            action_taken=Action.BLOCKED,
        )
        output, code = run()
        assert "FAIL  monitoring.open_incidents" in output
        assert code == 1

    def test_a_pulled_feature_flag_is_warned_about(self, launchable: None) -> None:
        """A flag left pulled after an incident is the commonest way a product
        quietly runs degraded for a month. Not a failure — somebody may have
        pulled it on purpose — but never silent."""
        from apps.common.models import FeatureFlag

        FeatureFlag.objects.create(name="media.illustrations", value="off")
        output, code = run()
        assert "WARN  spend.flags" in output
        assert "media.illustrations=off" in output
        assert code == 0


class TestTheCommandsOwnHonesty:
    def test_a_clean_deployment_passes(self, launchable: None) -> None:
        output, code = run()
        assert code == 0
        assert "FAIL" not in output

    def test_strict_turns_a_warning_into_a_refusal(self, launchable: None, settings: Any) -> None:
        """What a release pipeline runs. The restore-drill check is always a
        warning — it is the one thing the command genuinely cannot determine — so
        `--strict` always has at least that to refuse on, which is the point:
        a release should not proceed on an unverified backup.
        """
        plain_output, plain_code = run()
        assert plain_code == 0
        assert "WARN  durability.restore_drill" in plain_output

        strict_output, strict_code = run(strict=True)
        assert strict_code == 1
        assert "NOT READY" in strict_output

    def test_it_exits_non_zero_rather_than_printing_red_text(
        self, launchable: None, settings: Any
    ) -> None:
        """The property a deploy script depends on. Everything else in this file
        is decoration if the command returns 0 while saying NOT READY."""
        settings.SAFETY_CLASSIFIER_ENABLED = False
        output, code = run()
        assert "NOT READY" in output
        assert code == 1

    def test_its_output_is_plain_ascii(self, launchable: None) -> None:
        """This prints to a terminal, and the Windows console mangles anything
        that is not — the same reason `router.select_provider`'s error message
        says so. A preflight whose findings render as `NOT READY: ... (18.8)`
        with replacement characters in the middle is a preflight somebody stops
        reading."""
        output, _ = run()
        assert output.isascii(), [line for line in output.splitlines() if not line.isascii()]
