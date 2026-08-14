"""The launch preflight (PRODUCT.md §13.2, §16.10, §18.8, §22.5; Day 15's §4.4).

    python manage.py launch_check [--strict]

Run against a deployment before any real child touches it, and again before each
release. It answers one question — *is this deployment safe to put a
four-year-old in front of* — by asking the things that are individually obvious
and collectively easy to get one wrong.

**Why a command and not a checklist.** Every item here has a right answer that
the process can determine about itself, and a human reading a markdown checklist
at the end of a fifteen-day sprint will tick "safety classifier on" because it
was on last week. `prod.py` already refuses to boot on the three worst
misconfigurations; this covers the ones that are not boot failures because they
are legitimately optional in development — a missing image-moderation key, an
unreachable Redis, a backup nobody has ever restored.

**Three verdicts, and the middle one is the useful one.**

* `FAIL` — do not launch. A child would be less safe than the product claims.
* `WARN` — launch is a judgement call, and here is what you are accepting.
* `OK`.

`--strict` turns every WARN into a FAIL, which is what `deploy.yml` would use.
The default is deliberately softer so the command is worth running on a laptop:
one that failed on the absence of an ElevenLabs key would never be run at all,
and a preflight nobody runs protects nobody.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    verdict: str
    detail: str


class Command(BaseCommand):
    help = "Preflight a deployment: safety, durability, spend controls, monitoring."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Treat every warning as a failure. What a release pipeline should use.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help=(
                "Ask each configured vendor whether the credential we hold actually works. "
                "Makes real calls: ElevenLabs speaks a word and the Gemini image probe "
                "draws one picture, so a run costs a couple of credits and a few cents."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        checks: list[Check] = [
            *self._safety(),
            *self._privacy(),
            *self._credentials(live=options["live"]),
            *self._durability(),
            *self._spend(),
            *self._monitoring(),
        ]

        width = max(len(check.name) for check in checks)
        styles = {OK: self.style.SUCCESS, WARN: self.style.WARNING, FAIL: self.style.ERROR}
        section = ""
        for check in checks:
            head = check.name.split(".")[0]
            if head != section:
                section = head
                self.stdout.write("")
            line = f"  {check.verdict:<5} {check.name:<{width}}  {check.detail}"
            self.stdout.write(styles[check.verdict](line))

        failed = [c for c in checks if c.verdict == FAIL]
        warned = [c for c in checks if c.verdict == WARN]

        self.stdout.write("")
        if failed or (warned and options["strict"]):
            blocking = failed + (warned if options["strict"] else [])
            self.stdout.write(
                self.style.ERROR(f"NOT READY: {len(blocking)} blocking, {len(warned)} warnings.")
            )
            raise SystemExit(1)

        if warned:
            self.stdout.write(
                self.style.WARNING(
                    f"READY, with {len(warned)} warnings you are accepting. Read them."
                )
            )
            return
        self.stdout.write(self.style.SUCCESS("READY."))

    # -- the safety layer (§13.2, §13.3) ---------------------------------
    def _safety(self) -> list[Check]:
        checks = []

        checks.append(
            Check(
                "safety.classifier",
                OK if settings.SAFETY_CLASSIFIER_ENABLED else FAIL,
                "the independent output classifier is on"
                if settings.SAFETY_CLASSIFIER_ENABLED
                else "OFF. The keyword policy alone is not this product's safety story (13.3).",
            )
        )

        # A classifier that is switched on and has no provider is not a
        # classifier. `prod.py` deliberately does not check this — an outage is a
        # runtime condition the pipeline handles honestly — but launching into it
        # on purpose is a different thing, and this is the moment to notice.
        #
        # Asked through `_selected_provider` rather than "is any API key set",
        # because the all-Vertex deployment has no API key at all and is
        # nonetheless fully configured: Gemini is credentialled by a service
        # account and three settings (`apps.common.vertex`).
        provider = _selected_provider()
        checks.append(
            Check(
                "safety.grader_reachable",
                OK if provider else FAIL,
                f"{provider} is configured for {settings.AI_SAFETY_MODEL}"
                if provider
                else "no AI provider is credentialled: every classifier call will "
                "degrade, on every beat.",
            )
        )

        # **The effective model, not the setting.** `ai.beat_model` is the
        # no-deploy rollback lever (§16.10) and the router reads it in preference
        # to `AI_BEAT_MODEL`, so a preflight that compared the settings was
        # answering a question about a value nothing uses — green while the same
        # weights wrote and graded every beat. That is the audit's F-4, and the
        # write-side half of the fix lives in `flags._validate_beat_model`.
        writer = _effective_beat_model()
        separate = settings.AI_SAFETY_MODEL != writer
        checks.append(
            Check(
                "safety.grader_is_separate",
                OK if separate else FAIL,
                f"the grader ({settings.AI_SAFETY_MODEL}) is a different model from the "
                f"writer ({writer})"
                if separate
                else f"the beat model is grading its own output: writer and grader are "
                f"both '{writer}' (13.3).",
            )
        )

        # **Whether a moderator exists**, not what the setting says. This row used
        # to read `IMAGE_MODERATION` and nothing else, so it printed `OK` on `auto`
        # while `select_moderator()` returned `None` — the gate structurally off,
        # every scan logging `moderation=absent`, on the one screen a founder reads
        # before letting a family in. That is the same question `grader_reachable`
        # eight lines above was upgraded from ("is a key set" -> "ask the selected
        # provider"); this row was left on the older kind.
        from apps.media import moderation as image_moderation

        mode = str(settings.IMAGE_MODERATION)
        moderator = image_moderation.select_moderator() if mode == "auto" else None
        if mode != "auto":
            verdict, detail = (
                (WARN if mode else FAIL),
                f"IMAGE_MODERATION={mode} - generated pictures are structurally"
                " scanned but not looked at (13.8).",
            )
        elif moderator is None:
            verdict, detail = (
                FAIL,
                "IMAGE_MODERATION=auto, but no vision provider is credentialled: "
                "every generated picture is structurally scanned and never looked "
                "at, and every scan logs moderation=absent (13.8). Give the gate a "
                "vision model - an ANTHROPIC_API_KEY, or the GOOGLE_CLOUD_* "
                "settings with IMAGE_MODERATION_PROVIDER=google.",
            )
        else:
            verdict, detail = (
                OK,
                f"IMAGE_MODERATION=auto, and {moderator.name} ({moderator.model}) is "
                "configured to look at every generated picture",
            )
        checks.append(Check("safety.image_gate", verdict, detail))

        # The pre-vetted beats. A pool that has been emptied by a refactor is a
        # blocked generation with nothing to serve in its place.
        from apps.profiles.models import AGE_BAND_OLDER, AGE_BAND_YOUNGER
        from apps.safety.fallbacks import pool_for

        pools = {band: len(pool_for(band)) for band in (AGE_BAND_YOUNGER, AGE_BAND_OLDER)}
        checks.append(
            Check(
                "safety.fallback_pool",
                OK if all(pools.values()) else FAIL,
                f"pre-vetted fallback beats: {pools}",
            )
        )

        from apps.safety.models import SYSTEM_IDENTITY, PromptVersion

        live = PromptVersion.objects.filter(name=SYSTEM_IDENTITY, active=True).first()
        checks.append(
            Check(
                "safety.prompt_version",
                OK if live else FAIL,
                f"system_identity v{live.version} is live"
                if live
                else "no active system_identity prompt: every generation would raise.",
            )
        )
        return checks

    # -- privacy (§13.8) --------------------------------------------------
    def _privacy(self) -> list[Check]:
        """Is every route that carries a child's words one that may not train on them?

        **A blocking row, not a reachability one**, and that distinction is the
        whole finding. Before this existed the preflight said

            OK  safety.grader_reachable  a provider is configured for
                                         google/gemma-4-26b-a4b-it:free

        which is true, and says nothing about the thing that mattered: `:free` is
        the variant whose terms permit the gateway to log and train on the
        prompt, and the memory prompt carries the verbatim session digest of what
        a child said out loud. A parent reads a page promising the opposite.

        `prod.py` refuses to boot on this too. Both, because they catch it at
        different moments: the boot guard stops the deployment existing, and this
        row is what a person reads *before* letting a family in — including on a
        development box, where `prod.py` never runs and where the audit found it.
        """
        from apps.common import model_ids

        problems = [
            problem
            for setting in model_ids.CHILD_DATA_MODEL_SETTINGS
            if (
                problem := model_ids.training_error(
                    setting=setting, model=str(getattr(settings, setting, "") or "")
                )
            )
        ]
        offenders = [
            f"{setting}={getattr(settings, setting, '')}"
            for setting in model_ids.CHILD_DATA_MODEL_SETTINGS
            if model_ids.permits_training(str(getattr(settings, setting, "") or ""))
        ]
        configured = ", ".join(
            str(getattr(settings, setting, "") or "")
            for setting in model_ids.CHILD_DATA_MODEL_SETTINGS
        )
        return [
            Check(
                "privacy.provider_terms",
                OK if not problems else FAIL,
                f"every child-data model is on a route whose terms exclude "
                f"training ({configured})"
                if not problems
                else "child data is going to prompt-reselling routes: "
                + ", ".join(offenders)
                + ". The privacy disclosure says this never happens (13.8).",
            )
        ]

    # -- credentials (§16.10, §16.6) --------------------------------------
    def _credentials(self, *, live: bool) -> list[Check]:
        """Are the keys real, and does the vendor agree?

        Every other section in this command reads configuration. This one is here
        because a deployment can be perfectly configured and completely unable to
        make a picture or a sound, and that is not a hypothetical: the pilot
        launched with `.env` holding the *names* of three credentials rather than
        the credentials, passed this preflight, and told a four-year-old a
        fallback story with no illustration and nothing spoken.

        The offline rows would have caught two of the three on a laptop. The third
        — a project id spelled correctly and rejected anyway — is what `--live` is
        for, and why not passing it is a warning rather than a silence.
        """
        from apps.common import preflight
        from apps.media.adapters import image as image_adapters
        from apps.media.adapters import tts as tts_adapters
        from apps.media.exceptions import MediaConfigurationError

        checks = []

        # Softened under DEBUG for the same reason `durability.memory_key` is: a
        # laptop legitimately runs on the repo's own insecure defaults, and a
        # preflight that refuses on a laptop is a preflight nobody runs. Outside
        # development it is a refusal, because there is no reading of
        # `CLOUDFLARE_ACCOUNT_ID=YOUR_CLOUDFLARE_ACCOUNT_ID` that is fine.
        unfilled = preflight.placeholders()
        checks.append(
            Check(
                "credentials.placeholders",
                OK if not unfilled else (WARN if settings.DEBUG else FAIL),
                "no credential is still the word describing it"
                if not unfilled
                else "still holding the placeholder from .env.example: " + ", ".join(unfilled),
            )
        )

        # The illustrator. `select_image_provider` is the product's own answer, so
        # a FAIL here is the exact error a child's beat would hit on a cache miss.
        try:
            illustrator = image_adapters.select_image_provider()
            verdict, detail = OK, f"IMAGE_PROVIDER={illustrator.name} has credentials"
        except MediaConfigurationError as exc:
            verdict, detail = FAIL, str(exc)
        checks.append(Check("credentials.illustrator", verdict, detail))

        # And the voice, where an empty chain is the one §4.9 calls unplayable: a
        # missing picture is a beat a child can still be told, a missing voice is a
        # screen a pre-reader cannot read and nothing to tap.
        try:
            chain = [provider.name for provider in tts_adapters.select_tts_providers()]
            verdict, detail = OK, f"voice chain: {', '.join(chain)}"
        except MediaConfigurationError as exc:
            chain, verdict, detail = [], FAIL, str(exc)
        checks.append(Check("credentials.voice", verdict, detail))

        # A fallback that is named and uncredentialled is the failure that only
        # appears during the outage it was supposed to cover. Both adapters skip
        # one silently at runtime (`media.image_fallback_unconfigured`, and a
        # voice chain that quietly has one member) — correct there, and exactly
        # what this row exists to say out loud beforehand.
        absent = [
            f"{setting}={name}"
            for setting, names, configured in (
                (
                    "IMAGE_FALLBACK_PROVIDERS",
                    list(settings.IMAGE_FALLBACK_PROVIDERS),
                    _configured_image_fallbacks(),
                ),
                ("TTS_FALLBACK_PROVIDERS", list(settings.TTS_FALLBACK_PROVIDERS), chain),
            )
            for name in names
            if name and name not in configured
        ]
        checks.append(
            Check(
                "credentials.fallbacks",
                WARN if absent else OK,
                "every named fallback has credentials"
                if not absent
                else "named but uncredentialled, so absent at the moment it is needed: "
                + ", ".join(absent),
            )
        )

        if not live:
            checks.append(
                Check(
                    "credentials.reachable",
                    WARN,
                    "not probed. Re-run with --live before letting a family in: a key can be "
                    "well-formed, correctly wired and refused by the vendor.",
                )
            )
            return checks

        for probe in preflight.probe_all():
            checks.append(
                Check(f"credentials.{probe.name}", OK if probe.ok else FAIL, probe.detail)
            )
        return checks

    # -- durability (§16.10, §11.10) --------------------------------------
    def _durability(self) -> list[Check]:
        checks = []

        checks.append(
            Check(
                "durability.memory_key",
                OK if settings.MEMORY_ENCRYPTION_KEY else (FAIL if not settings.DEBUG else WARN),
                "MEMORY_ENCRYPTION_KEY is set, with its own lifecycle"
                if settings.MEMORY_ENCRYPTION_KEY
                else "derived from SECRET_KEY: rotating SECRET_KEY would orphan every "
                "memory ever written (11.10).",
            )
        )

        checks.append(
            Check(
                "durability.media_storage",
                OK if settings.MEDIA_STORAGE == "s3" else (WARN if settings.DEBUG else FAIL),
                f"MEDIA_STORAGE={settings.MEDIA_STORAGE}"
                + (
                    ""
                    if settings.MEDIA_STORAGE == "s3"
                    else ": a container restart takes every illustration with it."
                ),
            )
        )

        # The dump the disclosure now describes (§12.8, the audit's F-5).
        #
        # `apps.accounts.privacy` tells parents that a dump is taken and held for
        # `BACKUP_RETENTION_DAYS` days. This is the row that makes that a checkable
        # claim rather than a hopeful one: it reads the newest dump's age against
        # the number the sentence states. A dead cron is the failure that looks
        # exactly like a healthy one from every other angle.
        newest = _newest_dump_age_hours()
        if newest is None:
            verdict, detail = (
                WARN,
                f"no dump in {settings.BACKUP_DIR}. Docs/Runbooks/Launch.md makes the "
                f"dump cron a launch item; the disclosure says one is taken.",
            )
        elif newest > settings.BACKUP_MAX_AGE_HOURS:
            verdict, detail = (
                FAIL,
                f"the newest dump is {newest:.0f}h old (limit {settings.BACKUP_MAX_AGE_HOURS}h). "
                f"The cron is not running, and the disclosure says dumps are taken.",
            )
        else:
            verdict, detail = (
                OK,
                f"newest dump is {newest:.0f}h old, kept {settings.BACKUP_RETENTION_DAYS} days "
                f"(what the disclosure states)",
            )
        checks.append(Check("durability.backup_freshness", verdict, detail))

        # The one thing this command genuinely cannot determine: whether a restore
        # has ever been *done*. It reports the honest answer rather than a
        # comfortable one — see the runbook's drill log, which is where the
        # evidence lives.
        checks.append(
            Check(
                "durability.restore_drill",
                WARN,
                "not machine-checkable. Confirm the last entry in "
                "Docs/Runbooks/Backup-Restore.md postdates the last schema change.",
            )
        )
        return checks

    # -- spend (§18.8, §22.4) ---------------------------------------------
    def _spend(self) -> list[Check]:
        from apps.analytics import budget
        from apps.common import flags

        checks = []
        state = budget.state()

        if not state.cap_usd:
            verdict, detail = (
                FAIL,
                ("INFERENCE_BUDGET_USD_PER_WINDOW is 0: the breaker enforces nothing (18.8)."),
            )
        elif not state.available:
            verdict, detail = (
                FAIL,
                (
                    "a cap is set but the counter is unreadable (Redis). Nothing is enforced, "
                    "and the dashboard will say so."
                ),
            )
        else:
            verdict, detail = (
                OK,
                (f"${state.cap_usd} per {state.window_s}s, ${state.spent_usd} spent this window"),
            )
        checks.append(Check("spend.breaker", verdict, detail))

        # A flag left pulled after an incident is the commonest way a product
        # quietly runs degraded for a month.
        overridden = [row for row in flags.snapshot() if row["source"] == "database"]
        checks.append(
            Check(
                "spend.flags",
                OK if not overridden else WARN,
                "no feature flags overridden"
                if not overridden
                else "pulled: "
                + ", ".join(f"{row['name']}={row['value'] or '(empty)'}" for row in overridden),
            )
        )

        priced = _pricing_covers_configured_providers()
        checks.append(
            Check(
                "spend.pricing",
                OK if priced else WARN,
                "the configured providers are in the price table"
                if priced
                else "an unpriced route: cost/session will under-report (22.4). "
                "Add it to AI_PRICE_TABLE.",
            )
        )
        return checks

    # -- monitoring (§13.11, §22.5) ---------------------------------------
    def _monitoring(self) -> list[Check]:
        from apps.safety.models import SafetyIncident, Severity

        checks = []

        # The incident queue has to be *reachable*, which for an admin-backed
        # queue means somebody can sign in to it. A queue nobody can open is not
        # a queue (§13.11).
        from django.contrib.auth import get_user_model

        staff = get_user_model().objects.filter(is_staff=True, is_active=True).count()
        checks.append(
            Check(
                "monitoring.incident_queue",
                OK if staff else FAIL,
                f"{staff} active staff account(s) can open /admin/ and work the queue"
                if staff
                else "no staff account: the safety incident queue is unreachable (13.11).",
            )
        )

        open_serious = SafetyIncident.objects.filter(
            severity__in=[Severity.HIGH, Severity.CRITICAL], reviewed_at__isnull=True
        ).count()
        checks.append(
            Check(
                "monitoring.open_incidents",
                OK if not open_serious else FAIL,
                "no unreviewed serious incidents"
                if not open_serious
                else f"{open_serious} unreviewed high/critical incidents. 22.5 calls a serious "
                "incident a stop-the-line event.",
            )
        )

        # And that the numbers a person is supposed to watch actually compute.
        # A dashboard that raises is a dashboard nobody opens twice.
        try:
            from apps.analytics import internal

            internal.as_text(7)
            checks.append(Check("monitoring.metrics", OK, "/admin/metrics renders"))
        except Exception as exc:
            checks.append(Check("monitoring.metrics", FAIL, f"the metrics page raises: {exc!r}"))

        return checks


def _configured_image_fallbacks() -> list[str]:
    """Which of `IMAGE_FALLBACK_PROVIDERS` could actually be called.

    Built from the adapters' own registry and their own `is_configured()`, and
    deliberately *not* from `select_image_providers()`: that one consults the
    quota cooldowns in Redis, so a provider resting after a bad afternoon would
    read here as a provider with no credentials. Two very different problems, and
    the preflight is about the permanent one.
    """
    from apps.media.adapters import image as image_adapters

    configured = []
    for name in settings.IMAGE_FALLBACK_PROVIDERS:
        factory = image_adapters.IMAGE_PROVIDERS.get(name)
        if factory is not None and factory().is_configured():
            configured.append(name)
    return configured


def _pricing_covers_configured_providers() -> bool:
    """Is every route we will actually bill on in the price table (§22.4)?

    An unpriced route is reported as unpriced rather than as free, which is the
    right runtime behaviour and a bad thing to discover after a week of alpha:
    the cost/session number the launch is being judged on would be missing its
    largest term.
    """
    from apps.analytics import pricing

    label = f"{_selected_provider()}:{settings.AI_BEAT_MODEL}"
    return all(
        (
            pricing.llm_cost(label, input_tokens=1, output_tokens=1) is not None,
            pricing.image_cost(settings.IMAGE_PROVIDER, images=1) is not None,
            pricing.tts_cost(settings.TTS_PROVIDER, characters=1) is not None,
        )
    )


def _newest_dump_age_hours() -> float | None:
    """Hours since the newest `primer-*.dump`, or `None` if there is not one.

    Matches `infra/backup/backup.sh`'s own naming, so a directory holding
    something else is not read as a backup — the mirror of the script only ever
    pruning files it could have written.
    """
    import time
    from pathlib import Path

    directory = Path(str(settings.BACKUP_DIR or ""))
    if not str(settings.BACKUP_DIR) or not directory.is_dir():
        return None

    dumps = [path for path in directory.glob("primer-*.dump") if path.is_file()]
    if not dumps:
        return None
    return (time.time() - max(path.stat().st_mtime for path in dumps)) / 3600.0


def _effective_beat_model() -> str:
    """The model the router will actually generate with (§16.10).

    `apps.story.ai.router` reads the `ai.beat_model` flag first and falls back to
    the setting, so this is what "the writer" means at the moment the preflight
    runs. A flag read here is cheap and cannot fail the command: a flag backend
    that is unreachable resolves to the declared default, which is empty.
    """
    from apps.common import flags

    return str(flags.value("ai.beat_model") or settings.AI_BEAT_MODEL)


def _selected_provider() -> str:
    """The provider the router will actually reach, mirroring `select_provider`.

    `google` is credentialled by three settings rather than one
    (`apps.common.vertex`), and it has to be here for the same reason it is in
    `config.settings.base._selected_ai_provider`: an all-Vertex deployment has no
    `ANTHROPIC_API_KEY`, and a preflight that only knew about API keys would
    report the grader as unreachable and the beat route as unpriced on a
    deployment where both are fine.
    """
    from apps.common import vertex

    credentials = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
        "google": vertex.is_configured(),
    }
    return next((name for name in settings.AI_PROVIDER_ORDER if credentials.get(name)), "")
