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
def launchable(settings: Any, db: Any, tmp_path: Any) -> None:
    """A deployment that should pass: every check's happy path.

    Assembled rather than mocked, so a new check added to the command shows up
    here as a failure to be thought about rather than as a silently-passing one.
    """
    # A fresh dump in a directory of its own. Pointed at `tmp_path` rather than
    # the repository's real `backups/` because a check that reads a file's age
    # would otherwise start failing on the developer's machine a day and a half
    # after they last ran `make backup`.
    (tmp_path / "primer-20260813T000000Z.dump").write_bytes(b"x" * 2048)
    settings.BACKUP_DIR = str(tmp_path)
    settings.SAFETY_CLASSIFIER_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.AI_PROVIDER_ORDER = ["anthropic"]
    settings.AI_BEAT_MODEL = "claude-opus-5"
    settings.AI_SAFETY_MODEL = "claude-haiku-4-5-20251001"
    # Pinned rather than inherited from the developer's `.env`: `privacy.provider_terms`
    # reads all four child-data models, and a test whose answer depended on the
    # machine it ran on would be the opposite of a preflight.
    settings.AI_MEMORY_MODEL = "claude-haiku-4-5-20251001"
    settings.EMBEDDING_MODEL = "gemini-embedding-001"
    settings.IMAGE_MODERATION = "auto"
    # `auto` resolves to a *moderator*, not to a string, and the preflight now
    # asks which. Anthropic is first in the auto order and has a key here, so this
    # deployment's gate is Claude's vision model no matter what the machine's own
    # `.env` says about Google — pinned for the same reason the models above are.
    settings.IMAGE_MODERATION_PROVIDER = "auto"
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

    def test_the_rollback_flag_pinning_the_writer_to_the_grader_blocks_the_launch(
        self, launchable: None, settings: Any
    ) -> None:
        """The audit's F-4, at the preflight end.

        `grader_is_separate` used to compare `AI_SAFETY_MODEL` against
        `AI_BEAT_MODEL` — the *settings*. The router reads the `ai.beat_model`
        flag first, so a flag pinning the writer to the grader's model left the
        preflight green while the same weights wrote and graded every beat. The
        write-side half of the fix refuses the flag; this is the half that would
        have caught a row already in the table.
        """
        from apps.common import flags
        from apps.common.models import FeatureFlag

        # Written straight to the table, bypassing the validator, because the
        # question here is what the preflight says about a bad row that already
        # exists — an admin edit from before the validator, or a hand-run UPDATE.
        FeatureFlag.objects.create(name="ai.beat_model", value=settings.AI_SAFETY_MODEL)
        flags.invalidate()

        output, code = run()
        assert "FAIL  safety.grader_is_separate" in output
        assert code == 1

    @pytest.mark.parametrize(
        "setting", ("AI_BEAT_MODEL", "AI_SAFETY_MODEL", "AI_MEMORY_MODEL", "EMBEDDING_MODEL")
    )
    def test_a_training_permitted_route_blocks_the_launch(
        self, launchable: None, settings: Any, setting: str
    ) -> None:
        """The audit's F-3. A blocking row, not a reachability one.

        Before this existed the preflight printed

            OK    safety.grader_reachable  a provider is configured for
                                           google/gemma-4-26b-a4b-it:free

        — true, and silent about the fact that `:free` is the variant whose terms
        permit the gateway to log and train on the prompt. Every one of the four
        settings carries a child's words or profile, so every one of them is
        checked, and each is asserted separately: a guard that covered three of
        four is how this product got here.
        """
        setattr(settings, setting, "vendor/some-model:free")
        output, code = run()

        assert "FAIL  privacy.provider_terms" in output
        assert setting in output
        assert code == 1

    def test_a_clean_route_set_reports_ok(self, launchable: None) -> None:
        output, _ = run()
        assert "OK    privacy.provider_terms" in output

    def test_an_image_gate_with_nothing_to_run_blocks_the_launch(
        self, launchable: None, settings: Any
    ) -> None:
        """The Vertex review's P1-2. `IMAGE_MODERATION=auto` is a *string*, and
        this row used to report it back: `OK safety.image_gate IMAGE_MODERATION=auto`
        while `select_moderator()` returned `None` and every scan logged
        `moderation=absent`. A preflight row that says OK about a gate that is off
        is worse than no row, because this screen is what a founder reads before
        letting a family in.

        Exactly the upgrade `safety.grader_reachable` got eight lines above: stop
        asking what the setting says, ask the thing that would run.
        """
        from apps.media import moderation

        settings.ANTHROPIC_API_KEY = ""
        settings.GOOGLE_CLOUD_PROJECT = ""
        settings.GOOGLE_CLOUD_LOCATION = ""
        settings.GOOGLE_APPLICATION_CREDENTIALS = ""

        # The premise, asserted rather than assumed: there really is no moderator.
        assert moderation.select_moderator() is None

        output, code = run()
        assert "FAIL  safety.image_gate" in output
        assert "moderation=absent" in output
        assert code == 1

    def test_a_configured_image_gate_says_which_model_looks_at_the_picture(
        self, launchable: None
    ) -> None:
        """The positive half. `OK` now means a moderator exists and is named — the
        difference between "the setting is auto" and "Claude's vision model will
        look at every illustration".

        The **model** is part of that naming, which the test's own title has always
        said and its assertion did not: the row printed a provider id
        (`google-vision`) on the one screen where a tester is verifying the model
        they configured."""
        from django.conf import settings as django_settings

        output, code = run()
        assert "OK    safety.image_gate" in output
        assert "anthropic-vision" in output
        assert django_settings.IMAGE_MODERATION_MODEL in output
        assert code == 0

    def test_a_dump_older_than_the_disclosure_promises_blocks_the_launch(
        self, launchable: None, settings: Any, tmp_path: Any
    ) -> None:
        """The audit's F-5, at the end where a promise becomes checkable.

        `apps.accounts.privacy` tells parents a nightly copy is taken and expires
        after `BACKUP_RETENTION_DAYS`. A dead cron is the failure that looks
        exactly like a healthy deployment from every other angle — and it makes
        the disclosure false in the direction that matters most, because the
        sentence about deletions reaching every copy assumes copies keep rolling.
        """
        import os
        import time

        stale = tmp_path / "primer-20260101T000000Z.dump"
        stale.write_bytes(b"x" * 2048)
        ancient = time.time() - (settings.BACKUP_MAX_AGE_HOURS + 24) * 3600
        for dump in tmp_path.glob("primer-*.dump"):
            os.utime(dump, (ancient, ancient))

        output, code = run()
        assert "FAIL  durability.backup_freshness" in output
        assert code == 1

    def test_no_dump_at_all_is_a_warning_rather_than_a_refusal(
        self, launchable: None, settings: Any, tmp_path: Any
    ) -> None:
        """A brand-new box and a laptop both look like this, and a preflight that
        failed on them would stop being run — which is the failure mode this
        command's docstring is most worried about. `--strict`, which a release
        pipeline uses, still refuses."""
        for dump in tmp_path.glob("primer-*.dump"):
            dump.unlink()

        output, code = run()
        assert "WARN  durability.backup_freshness" in output
        assert code == 0

        _, strict_code = run(strict=True)
        assert strict_code == 1

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


class TestTheAllGoogleDeployment:
    """The configuration `Docs/Hosting.md §4` actually ships: Gemini on Vertex,
    no API key anywhere.

    The preflight used to ask "is `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`
    set?", which is a question about API keys and not about reachability. Vertex
    is credentialled by a service account and three settings, so an all-Google
    box — fully working, generating beats — would have been told its safety
    grader was unreachable and refused the launch.
    """

    @pytest.fixture
    def all_google(self, launchable: None, settings: Any) -> None:
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENROUTER_API_KEY = ""
        settings.AI_PROVIDER_ORDER = ["google"]
        settings.GOOGLE_CLOUD_PROJECT = "primer-test"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/service-account.json"
        settings.AI_BEAT_MODEL = "gemini-2.5-flash"
        settings.AI_SAFETY_MODEL = "gemini-2.5-flash-lite"
        settings.AI_MEMORY_MODEL = "gemini-2.5-flash-lite"
        settings.IMAGE_MODERATION_PROVIDER = "google"
        settings.IMAGE_MODERATION_MODEL = "gemini-2.5-flash-lite"

    def test_it_passes_the_preflight(self, all_google: None) -> None:
        output, code = run()

        assert "OK    safety.grader_reachable" in output
        assert "google is configured for gemini-2.5-flash-lite" in output
        assert code == 0

    def test_the_grader_is_a_different_model_from_the_writer(self, all_google: None) -> None:
        """§13.3, on the bare Gemini ids. Flash writes, Flash-Lite grades — which
        is also the constraint that rules out `asia-south1`, the only region near
        the pilot's users, because it serves Flash and not Flash-Lite."""
        output, _ = run()
        assert "OK    safety.grader_is_separate" in output

    def test_the_gemini_routes_satisfy_the_training_rule(self, all_google: None) -> None:
        """§13.8. A bare `gemini-2.5-flash` carries no `:free` suffix, and Vertex
        bills to the GCP project under terms that exclude inputs from training —
        the second half being a purchasing fact (`Docs/Hosting.md §3`) that the
        guard cannot prove and does not claim to."""
        output, _ = run()
        assert "OK    privacy.provider_terms" in output
        assert "gemini-2.5-flash" in output

    def test_the_image_gate_runs_on_the_vertex_credentials_alone(self, all_google: None) -> None:
        """No Anthropic key anywhere, and the gate is still a real vision call —
        which is the whole reason `GeminiImageModerator` exists. Asserted through
        the preflight *and* through `select_moderator()`, because the finding this
        row was fixed for is precisely those two disagreeing."""
        from django.conf import settings as django_settings

        from apps.media import moderation

        assert isinstance(moderation.select_moderator(), moderation.GeminiImageModerator)

        output, _ = run()
        assert "OK    safety.image_gate" in output
        assert "google-vision" in output
        # The model too, not just the provider: an operator reading this row is
        # checking the gate against the `IMAGE_MODERATION_MODEL` they set.
        assert django_settings.IMAGE_MODERATION_MODEL in output

    def test_the_beat_route_is_priced(self, all_google: None) -> None:
        """Otherwise cost/session — the number the pilot is judged on — is missing
        its largest term (§22.4)."""
        output, _ = run()
        assert "OK    spend.pricing" in output

    def test_losing_the_vertex_credentials_blocks_the_launch(
        self, all_google: None, settings: Any
    ) -> None:
        """The mirror image, and the reason the row is worth anything: a box that
        thinks it is on Gemini and has no credentials degrades every beat to the
        fallback and every scan to `moderation=absent`."""
        settings.GOOGLE_APPLICATION_CREDENTIALS = ""

        output, code = run()
        assert "FAIL  safety.grader_reachable" in output
        assert code == 1


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
