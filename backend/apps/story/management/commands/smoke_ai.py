"""Live smoke test for the AI path (PRODUCT.md §19.3, §19.6, §8.1).

    python manage.py smoke_ai [--pick-free-model]

This makes **real provider calls** and asserts the outcomes, which is why it is
opt-in: it is absent from CI and from the default pytest run, and it needs a real
key. `make test` still makes zero live calls.

It exists because the expensive Day-1 bug — a JSON Schema the provider would
reject — was invisible to a test suite that stubs `generate_json`, and was caught
only by a human running the command and reading the output. Everything below is
that human check, written down and asserted.

It calls the same router the product calls. The only thing it does differently is
wrap the calls in assertions, so a regression on any future day shows up here in
seconds rather than in a child's story.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.story.ai.prompts import hello_beat as hello_beat_prompt
from apps.story.ai.router import AIError, ModelRouter
from apps.story.ai.schemas import Beat, InteractionType

# Exit codes. 2 means "you haven't set this up yet" and 1 means "the AI path is
# broken" — worth distinguishing, because only one of them is a real failure.
EXIT_MISCONFIGURED = 2
EXIT_CHECK_FAILED = 1

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Best-to-worst evidence that a model can be held to a schema. `structured_outputs`
# means it honours a strict json_schema; the rest are weaker signals that it at
# least understands constrained output.
_STRUCTURE_SIGNALS = ("structured_outputs", "response_format", "tools")


# Provider error bodies are echoed straight into the summary, and they carry more
# than the error: OpenRouter's 400s include the account's `user_id`. None of it is
# a secret we hold, but a failing smoke run is exactly what gets pasted into an
# issue or a chat, so identifiers get redacted on the way out.
_SENSITIVE_FIELD = re.compile(
    r"""(['"]?(?:user_id|user|api_key|apikey|key|token|authorization)['"]?\s*[:=]\s*)"""
    r"""(['"][^'"]*['"]|\S+)""",
    re.IGNORECASE,
)
MAX_DETAIL_CHARS = 300


def scrub(text: str) -> str:
    """Redact identifiers and clip runaway provider errors."""
    redacted = _SENSITIVE_FIELD.sub(r"\1<redacted>", text)
    if len(redacted) > MAX_DETAIL_CHARS:
        redacted = f"{redacted[:MAX_DETAIL_CHARS]}..."
    return " ".join(redacted.split())


@dataclass
class CheckResult:
    """One independently-reported check."""

    key: str
    title: str
    passed: bool
    detail: str


class SmokeFailure(Exception):
    """A check's assertion failed. Carries the reason, not a traceback."""


@contextlib.contextmanager
def override_setting(name: str, value: Any) -> Iterator[None]:
    """Temporarily change a setting, restoring it even on failure.

    Check B has to flip `AI_STRUCTURED_OUTPUT_MODE` for one generation. Flipping
    the real setting (rather than reaching into the provider) is deliberate: it
    proves the knob operators actually use is wired to the behaviour.
    """
    missing = object()
    previous = getattr(settings, name, missing)
    setattr(settings, name, value)
    try:
        yield
    finally:
        if previous is missing:
            delattr(settings, name)
        else:
            setattr(settings, name, previous)


@contextlib.contextmanager
def count_repairs() -> Iterator[list[int]]:
    """Count validate-and-repair retries the router logs during the block."""
    counter = [0]

    class _Counter(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "repair" in record.getMessage().lower():
                counter[0] += 1

    handler = _Counter()
    logger = logging.getLogger("apps.story.ai.router")
    logger.addHandler(handler)
    try:
        yield counter
    finally:
        logger.removeHandler(handler)


def pick_free_model(timeout: float = 20.0) -> str:
    """Return the slug of a currently-available free OpenRouter model.

    Free-model availability changes week to week, so hand-copied slugs go stale
    and the founder gets a 404 that looks like a broken smoke test. Ask the API
    instead, and prefer models that advertise structured-output support so
    Check A has a chance of exercising the strict path.
    """
    try:
        response = httpx.get(OPENROUTER_MODELS_URL, timeout=timeout)
        response.raise_for_status()
        catalogue = response.json()["data"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise SmokeFailure(
            f"Could not read the OpenRouter model list ({exc}). "
            "Set AI_BEAT_MODEL in .env to a free model slug and re-run."
        ) from exc

    candidates = [entry for entry in catalogue if _is_free_text_model(entry)]
    if not candidates:
        raise SmokeFailure(
            "OpenRouter listed no free text models right now. "
            "Set AI_BEAT_MODEL in .env explicitly and re-run."
        )

    # Stable sort: best structure signal first, otherwise the API's own ordering.
    candidates.sort(key=_structure_rank)
    return str(candidates[0]["id"])


def _is_free_text_model(entry: dict[str, Any]) -> bool:
    pricing = entry.get("pricing") or {}
    try:
        # Prices are strings ("0", "0.0", "0.0000012"), so compare numerically.
        if float(pricing.get("prompt", "1")) != 0.0 or float(pricing.get("completion", "1")) != 0.0:
            return False
    except (TypeError, ValueError):
        return False

    # Text in, text out. The free tier also carries image and audio models, and
    # a music model will happily accept a beat prompt and return nothing usable.
    architecture = entry.get("architecture") or {}
    inputs = architecture.get("input_modalities") or []
    outputs = architecture.get("output_modalities") or []
    return "text" in inputs and "text" in outputs


def _structure_rank(entry: dict[str, Any]) -> int:
    supported = set(entry.get("supported_parameters") or [])
    for rank, signal in enumerate(_STRUCTURE_SIGNALS):
        if signal in supported:
            return rank
    return len(_STRUCTURE_SIGNALS)


def assert_usable_beat(beat: Beat) -> None:
    """Assert the §8.1 contract beyond what the schema alone can express.

    The schema guarantees the fields exist and are typed. It does not guarantee
    they carry anything — an empty `narration` validates fine and is useless to a
    child. These are the checks a human was doing by eye.
    """
    required_text = {
        "beat_id": beat.beat_id,
        "narration": beat.narration,
        "emotion": beat.emotion,
        "next_hint": beat.next_hint,
        "scene.setting": beat.scene.setting,
        "scene.illustration_prompt": beat.scene.illustration_prompt,
        "learning.objective_id": beat.learning.objective_id,
        "learning.skill_evidence_key": beat.learning.skill_evidence_key,
    }
    empty = sorted(field for field, value in required_text.items() if not value.strip())
    if empty:
        raise SmokeFailure(f"beat has empty required field(s): {', '.join(empty)}")

    if not 0.0 <= beat.learning.difficulty <= 1.0:
        raise SmokeFailure(f"learning.difficulty {beat.learning.difficulty} is outside [0, 1]")

    if not isinstance(beat.interaction.type, InteractionType):
        raise SmokeFailure(f"interaction.type '{beat.interaction.type}' is not a known type")

    if not beat.scene.characters_present:
        raise SmokeFailure("scene.characters_present is empty")


class Command(BaseCommand):
    help = (
        "Live smoke test of the AI path: makes real provider calls and asserts the "
        "results. Costs real (free-tier) tokens; never run in CI."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--pick-free-model",
            action="store_true",
            help=(
                "When AI_BEAT_MODEL is unset, choose a currently-available free "
                "OpenRouter model instead of failing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider_name, model = self.preflight(pick_free_model_flag=options["pick_free_model"])

        self.stdout.write("")
        results = [
            self.check_a_happy_path(),
            self.check_b_forced_fallback(),
            self.check_c_anthropic_routing(),
        ]
        self.print_summary(results, provider_name=provider_name, model=model)

        if not all(result.passed for result in results):
            raise CommandError(
                "AI smoke test FAILED - see the table above.",
                returncode=EXIT_CHECK_FAILED,
            )

    # --- preflight ---------------------------------------------------------

    def preflight(self, *, pick_free_model_flag: bool) -> tuple[str, str]:
        """Verify we can run at all, and resolve the model. Exits 2 if not."""
        try:
            router = ModelRouter.from_settings()
        except AIError as exc:
            raise CommandError(str(exc), returncode=EXIT_MISCONFIGURED) from exc

        if not router.providers:
            raise CommandError("AI_PROVIDER_ORDER is empty.", returncode=EXIT_MISCONFIGURED)

        provider = router.providers[0]
        env_var = getattr(provider, "credential_env_var", "") or "the provider's API key"
        if not provider.is_configured():
            raise CommandError(
                f"{env_var} is not set, so provider '{provider.name}' (first in "
                f"AI_PROVIDER_ORDER) cannot be called.\n"
                f"Set {env_var} in your .env and re-run.",
                returncode=EXIT_MISCONFIGURED,
            )

        self.stdout.write(self.style.MIGRATE_HEADING("AI smoke test"))
        # Deliberately reports only that a key is present, never any part of it.
        self.stdout.write(f"  provider : {provider.name} ({env_var} is set)")

        # After the heading, so that model-selection notes appear under it.
        model = self.resolve_model(provider.name, pick_free_model_flag=pick_free_model_flag)
        self.stdout.write(f"  model    : {model}")
        self.stdout.write(f"  mode     : {settings.AI_STRUCTURED_OUTPUT_MODE}")
        return provider.name, model

    def resolve_model(self, provider_name: str, *, pick_free_model_flag: bool) -> str:
        """Decide which model to run against, auto-picking one only if asked."""
        # The *env var*, not the setting: the setting always has a value because it
        # falls back to the Claude default, which is wrong for OpenRouter.
        explicit = os.environ.get("AI_BEAT_MODEL", "").strip()
        if explicit:
            if pick_free_model_flag:
                self.stdout.write(
                    self.style.WARNING(
                        f"  note     : AI_BEAT_MODEL={explicit} is set, so "
                        "--pick-free-model was ignored."
                    )
                )
            return explicit

        if pick_free_model_flag:
            try:
                chosen = pick_free_model()
            except SmokeFailure as exc:
                raise CommandError(str(exc), returncode=EXIT_MISCONFIGURED) from exc
            settings.AI_BEAT_MODEL = chosen
            self.stdout.write(f"  picked   : {chosen} (free, via --pick-free-model)")
            return chosen

        if provider_name == "openrouter":
            # settings.AI_BEAT_MODEL is a Claude id here, which OpenRouter will
            # reject with a confusing 404. Say so before spending the call.
            raise CommandError(
                "AI_BEAT_MODEL is not set, and the default is a Claude model id that "
                "OpenRouter cannot serve.\n"
                "Either set AI_BEAT_MODEL in .env to a free OpenRouter model slug, or "
                "re-run with --pick-free-model.",
                returncode=EXIT_MISCONFIGURED,
            )

        return str(settings.AI_BEAT_MODEL)

    # --- checks ------------------------------------------------------------

    def check_a_happy_path(self) -> CheckResult:
        """A real generation through whatever provider is configured."""
        return self.run_check("A", "happy path (real call)", self._check_a)

    def _check_a(self) -> str:
        router = ModelRouter.from_settings()
        provider = router.select_provider()

        with count_repairs() as repairs:
            beat = router.generate_beat(self.context())
        assert_usable_beat(beat)

        # Which mode actually ran matters: a beat produced under json_object was
        # validated by us, not enforced by the provider.
        mode = getattr(provider, "output_mode", "unknown")
        return f"mode={mode} repairs={repairs[0]}"

    def check_b_forced_fallback(self) -> CheckResult:
        """The path most free models actually take, proven rather than assumed."""
        return self.run_check("B", "forced json_object + repair", self._check_b)

    def _check_b(self) -> str:
        with override_setting("AI_STRUCTURED_OUTPUT_MODE", "json_object"):
            router = ModelRouter.from_settings()
            provider = router.select_provider()
            with count_repairs() as repairs:
                beat = router.generate_beat(self.context())
            assert_usable_beat(beat)
            mode = getattr(provider, "output_mode", "unknown")

        if mode != "json_object":
            raise SmokeFailure(f"expected the json_object path, but the provider used {mode}")
        return f"mode={mode} repairs={repairs[0]}"

    def check_c_anthropic_routing(self) -> CheckResult:
        """Switching back to Anthropic is config-only, and fails cleanly with no key."""
        return self.run_check("C", "anthropic routing, no key", self._check_c)

    def _check_c(self) -> str:
        # A subprocess, because "exit code 1 and no traceback" is a property of the
        # process, not of an exception. An in-process call could only assert the
        # exception type and would silently miss a regression in manage.py's
        # error handling. The empty ANTHROPIC_API_KEY wins over any value in .env,
        # so this holds even once the founder has a real Anthropic key.
        env = {
            **os.environ,
            "AI_PROVIDER_ORDER": "anthropic",
            "ANTHROPIC_API_KEY": "",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "manage.py", "hello_beat"],
                cwd=settings.BASE_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure("hello_beat hung instead of failing fast") from exc

        output = f"{completed.stdout}\n{completed.stderr}"

        if completed.returncode != 1:
            raise SmokeFailure(
                f"expected exit 1, got {completed.returncode}. Output: {output.strip()[:400]}"
            )
        if "Traceback" in output:
            raise SmokeFailure("hello_beat printed a traceback instead of a readable message")
        if "No AI provider is configured" not in output:
            raise SmokeFailure(
                f"missing the documented no-key message. Output: {output.strip()[:400]}"
            )
        if "ANTHROPIC_API_KEY" not in output:
            raise SmokeFailure("the error does not name ANTHROPIC_API_KEY, so it isn't actionable")

        return "exit=1, no traceback, names ANTHROPIC_API_KEY"

    # --- plumbing ----------------------------------------------------------

    def context(self) -> dict[str, str]:
        """The real hello-beat prompt — no child data, no parallel plumbing."""
        return {"system": hello_beat_prompt.SYSTEM, "user": hello_beat_prompt.USER}

    def run_check(self, key: str, title: str, body: Any) -> CheckResult:
        """Run one check, converting any failure into a reported FAIL.

        Checks are independent on purpose: one broken path should not hide the
        state of the other two.
        """
        self.stdout.write(f"{key}  {title} ... ", ending="")
        self.stdout.flush()
        try:
            detail = body()
        except SmokeFailure as exc:
            self.stdout.write(self.style.ERROR("FAIL"))
            return CheckResult(key, title, passed=False, detail=scrub(str(exc)))
        except Exception as exc:  # a smoke test reports failures, it never crashes
            self.stdout.write(self.style.ERROR("FAIL"))
            return CheckResult(
                key, title, passed=False, detail=scrub(f"{type(exc).__name__}: {exc}")
            )

        self.stdout.write(self.style.SUCCESS("PASS"))
        return CheckResult(key, title, passed=True, detail=detail)

    def print_summary(self, results: list[CheckResult], *, provider_name: str, model: str) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"  provider={provider_name} model={model}")
        self.stdout.write("")
        for result in results:
            status = self.style.SUCCESS("PASS") if result.passed else self.style.ERROR("FAIL")
            self.stdout.write(f"  {result.key}  {result.title:<32} {status}  {result.detail}")

        self.stdout.write("")
        if all(result.passed for result in results):
            self.stdout.write(self.style.SUCCESS("  OVERALL PASS"))
        else:
            self.stdout.write(self.style.ERROR("  OVERALL FAIL"))
        # The CommandError that follows a failure goes to stderr. Flush first so the
        # table still reads in order when the run is piped to a file or a log.
        self.stdout.flush()
