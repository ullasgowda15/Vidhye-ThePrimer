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
        vertex: bool = False,
    ) -> None:
        monkeypatch.setattr(base, "AI_PROVIDER_ORDER", order)
        monkeypatch.setattr(base, "AI_BEAT_MODEL", beat)
        monkeypatch.setattr(base, "AI_SAFETY_MODEL", safety)
        monkeypatch.setattr(base, "AI_MEMORY_MODEL", safety if memory is None else memory)
        monkeypatch.setattr(base, "ANTHROPIC_API_KEY", anthropic_key)
        monkeypatch.setattr(base, "OPENROUTER_API_KEY", openrouter_key)
        # Vertex is credentialled by three settings rather than one key, and all
        # three have to be here: `_selected_ai_provider` returns `""` when the
        # selected provider has no credentials, and an empty provider skips the
        # whole check. A google test that forgot them would pass by not running.
        monkeypatch.setattr(base, "GOOGLE_CLOUD_PROJECT", "primer-test" if vertex else "")
        monkeypatch.setattr(base, "GOOGLE_CLOUD_LOCATION", "global" if vertex else "")
        monkeypatch.setattr(
            base, "GOOGLE_APPLICATION_CREDENTIALS", "/nowhere/sa.json" if vertex else ""
        )
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


def test_a_gateway_slug_under_google_fails(pairing: Any) -> None:
    """Vertex takes a bare `gemini-2.5-flash`, never a `vendor/model` slug.

    This mistake arrives by a specific road rather than out of nowhere:
    `Docs/Hosting.md` recommended routing Gemini *through OpenRouter* before there
    was a native provider, so a half-migrated `.env` keeps the slug and changes
    only `AI_PROVIDER_ORDER`. Vertex answers 404 to every call, and when the
    setting is `AI_SAFETY_MODEL` that is §13.3's guard silently off — the same
    shape of failure Day 7 found under OpenRouter.
    """
    with pytest.raises(ImproperlyConfigured) as caught:
        pairing(
            order=["google"],
            beat="google/gemini-2.5-flash",
            safety="gemini-2.5-flash-lite",
            vertex=True,
        )

    message = str(caught.value)
    assert "AI_BEAT_MODEL" in message
    # It has to say what to do, like the other two branches do.
    assert "gemini-2.5-flash" in message


def test_the_shipping_vertex_pairing_boots(pairing: Any) -> None:
    """The all-Google configuration from `Docs/Hosting.md §4`: bare Gemini ids, a
    grader that is a different model from the writer."""
    pairing(
        order=["google"],
        beat="gemini-2.5-flash",
        safety="gemini-2.5-flash-lite",
        vertex=True,
    )


def test_a_google_deployment_missing_its_credentials_checks_nothing(pairing: Any) -> None:
    """The reason `_selected_ai_provider` had to learn about Vertex.

    With no credentials nothing will be called, so there is no pairing to be
    wrong about — but the same code path is what makes the *credentialled* case
    checked at all, and before this it returned `""` for every all-Vertex
    deployment.
    """
    pairing(order=["google"], beat="google/gemini-2.5-flash", vertex=False)


def test_no_credentials_at_all_is_not_a_pairing_error(pairing: Any) -> None:
    """CI has no keys. Nothing will be called, so nothing can be mispaired —
    and `select_provider` raises its own, clearer error if anything tries."""
    pairing(order=["openrouter", "anthropic"], beat="claude-opus-5")


# ---------------------------------------------------------------------------
# No child data down a route that may train on it (§13.8) — the audit's F-3
# ---------------------------------------------------------------------------
#: The minimum environment `config.settings.prod` will start under. Every value
#: is a boot guard that predates this test and is not what is being tested here;
#: they are set so the *one* guard under test is the only thing that can fail.
PROD_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "SECRET_KEY": "test-secret-not-a-real-one-0123456789",
    "ALLOWED_HOSTS": "primer.example",
    "CORS_ALLOWED_ORIGINS": "https://primer.example",
    "DATABASE_URL": "postgres://primer:primer@localhost:5432/primer",
    "REDIS_URL": "redis://localhost:6379/0",
    "MEDIA_STORAGE": "s3",
    "AWS_STORAGE_BUCKET_NAME": "primer-media",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "SAFETY_CLASSIFIER_ENABLED": "1",
    "IMAGE_MODERATION": "auto",
    "MEMORY_ENCRYPTION_KEY": "6H0y0AGRTP-cKQ0RRW9x8p2yh4Y3iCJqQyLwLQ4qSAI=",
    "INFERENCE_BUDGET_USD_PER_WINDOW": "25",
    # Paired for `anthropic`, which is what the shipping defaults are.
    "AI_PROVIDER_ORDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-key",
    "OPENROUTER_API_KEY": "",
    "AI_BEAT_MODEL": "claude-opus-5",
    "AI_SAFETY_MODEL": "claude-haiku-4-5-20251001",
    "AI_MEMORY_MODEL": "claude-haiku-4-5-20251001",
    # The embedding route is named rather than left on its default, and the Vertex
    # trio is blanked rather than left absent, for the same reason every other
    # value here is pinned: `settings.base` reads the repository's own `.env` at
    # boot, so an unset key in this dict is not "unset" — it is whatever the
    # machine running the tests happens to have. `EMBEDDING_PROVIDER` defaults to
    # `google`, so without these four lines this baseline would be a *Vertex*
    # deployment on the founder's laptop and a broken one in CI.
    "EMBEDDING_PROVIDER": "openai",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "IMAGE_MODERATION_PROVIDER": "auto",
    "GOOGLE_CLOUD_PROJECT": "",
    "GOOGLE_CLOUD_LOCATION": "",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
}


def _boot_prod(**overrides: str) -> tuple[int, str]:
    """Import `config.settings.prod` in a fresh interpreter. Returns (code, stderr).

    A subprocess rather than an import, because the guard runs at module import
    and a settings module cannot be imported twice in one process — and because
    what is being asserted is precisely that *the process does not start*, which
    is not something a monkeypatched function call can demonstrate.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    environment = {
        **{key: value for key, value in os.environ.items() if key.startswith(("SYSTEM", "PATH"))},
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(backend),
        **PROD_ENV,
        **overrides,
    }
    result = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=str(backend),
        env=environment,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def test_the_shipping_production_configuration_boots() -> None:
    """The control. Without it, the four tests below would pass on any error."""
    code, stderr = _boot_prod()
    assert code == 0, stderr


@pytest.mark.parametrize(
    "setting", ("AI_BEAT_MODEL", "AI_SAFETY_MODEL", "AI_MEMORY_MODEL", "EMBEDDING_MODEL")
)
def test_prod_refuses_to_boot_on_a_training_permitted_route(setting: str) -> None:
    """§13.8, and the sentence in `apps.accounts.privacy` it exists to keep true.

    An OpenRouter `:free` route is the same weights under terms that permit the
    gateway to log and train on the prompt. The audit found *every* child-data
    model on one, with `launch_check` reporting the grader as healthy because it
    only asked whether the route was reachable.

    Each of the four is asserted separately and on purpose: the beat model is in
    the list because a beat prompt carries the child's first name, age band,
    avoid list, fears and retrieved memories, and a guard that covered three of
    the four would be the same shape of gap the audit was written about.
    """
    code, stderr = _boot_prod(**{setting: "vendor/some-model:free"})

    assert code != 0, "prod booted with child data pointed at a training-permitted route"
    assert "ImproperlyConfigured" in stderr
    assert setting in stderr
    assert ":free" in stderr

    # The message is what somebody meets at their least patient moment, in a
    # terminal. The Windows console renders a section sign or an em dash as a
    # replacement character, so the guard says so in ASCII — the same rule
    # `launch_check` holds itself to.
    problem = next(line for line in stderr.splitlines() if setting in line)
    assert problem.isascii(), problem


# ---------------------------------------------------------------------------
# Gemini on Vertex needs all three of its settings (§16.1, §13.8)
# ---------------------------------------------------------------------------
# Why this is a boot failure and not a warning: a half-configured Vertex
# deployment does not crash. `GoogleProvider.is_configured()` returns *false* on a
# missing project — correct at runtime, because falling through beats raising
# inside a beat a child is waiting for — so the box quietly finds no provider at
# all and serves pre-vetted fallback beats to every child, forever. The image gate
# degrades the same way, to `moderation=absent`. Both are invisible from outside.


def _vertex_env(credentials: str) -> dict[str, str]:
    """A `PROD_ENV` overlay for the all-Google deployment.

    All four child-data routes, because that is what `.env.example` and
    `Docs/Hosting.md §4` now describe: beats, the grader, the memory digest and
    the embeddings, on one service account.
    """
    return {
        "AI_PROVIDER_ORDER": "google",
        "ANTHROPIC_API_KEY": "",
        "AI_BEAT_MODEL": "gemini-2.5-flash",
        "AI_SAFETY_MODEL": "gemini-2.5-flash-lite",
        "AI_MEMORY_MODEL": "gemini-2.5-flash-lite",
        "EMBEDDING_PROVIDER": "google",
        "EMBEDDING_MODEL": "gemini-embedding-001",
        "GOOGLE_CLOUD_PROJECT": "primer-test",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_APPLICATION_CREDENTIALS": credentials,
    }


@pytest.fixture
def service_account_file(tmp_path: Any) -> str:
    """A stand-in for the service-account JSON. Never a real key, and never in
    the repository — the guard only asks whether the path is a file."""
    path = tmp_path / "vertex-sa.json"
    path.write_text('{"type": "service_account"}', encoding="utf-8")
    return str(path)


def test_the_all_google_production_configuration_boots(service_account_file: str) -> None:
    """The control, and the thing the runbook tells the founder to deploy."""
    code, stderr = _boot_prod(**_vertex_env(service_account_file))
    assert code == 0, stderr


@pytest.mark.parametrize(
    "setting", ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS")
)
def test_prod_refuses_to_boot_with_google_selected_and_a_setting_missing(
    setting: str, service_account_file: str
) -> None:
    code, stderr = _boot_prod(**{**_vertex_env(service_account_file), setting: ""})

    assert code != 0, f"prod booted with {setting} unset and google selected"
    assert "ImproperlyConfigured" in stderr
    assert setting in stderr

    # Plain ASCII, like every other boot guard: this is read in a terminal, and
    # the Windows console renders a section sign as a replacement character.
    problem = next(line for line in stderr.splitlines() if setting in line)
    assert problem.isascii(), problem


def test_prod_refuses_to_boot_when_the_key_file_is_not_there(tmp_path: Any) -> None:
    """The commoner of the two mistakes: the settings get copied from the runbook
    and the key stays on the laptop it was downloaded to. Same silent degradation
    as an unset setting, one layer later."""
    code, stderr = _boot_prod(**_vertex_env(str(tmp_path / "not-downloaded-yet.json")))

    assert code != 0
    assert "is not a file on this box" in stderr


def test_the_image_moderator_alone_is_enough_to_require_vertex(
    service_account_file: str,
) -> None:
    """Google can be the *moderator* without being the writer — the illustrator,
    the writer and the moderator being three different companies is a stronger
    §13.3 guarantee, not a misconfiguration. It still needs its credentials."""
    code, stderr = _boot_prod(
        IMAGE_MODERATION_PROVIDER="google",
        IMAGE_MODERATION_MODEL="gemini-2.5-flash-lite",
        GOOGLE_APPLICATION_CREDENTIALS="",
    )

    assert code != 0
    assert "IMAGE_MODERATION_PROVIDER" in stderr
    assert "GOOGLE_APPLICATION_CREDENTIALS" in stderr


def test_the_embedding_route_alone_is_enough_to_require_vertex() -> None:
    """The Vertex review's P1-1, and the quietest of the three routes.

    `EMBEDDING_PROVIDER` defaults to `google` (`settings/base.py`), so this is not
    an exotic configuration — it is what a box gets by saying nothing at all. The
    guard listed only `AI_PROVIDER_ORDER` and `IMAGE_MODERATION_PROVIDER`, so a
    deployment with no Vertex credentials booted, served stories, and then:
    `select_embedding_provider()` raises `MediaConfigurationError`, `consolidation`
    catches it as the `MediaError` it is and writes the memory with no vector, and
    `retrieval._nearest` filters on `embedding__isnull=False` and finds nothing.
    Every sitting remembered, no sitting ever recalled semantically, nothing
    failing and nothing paging. The other two routes degrade loudly enough to be
    noticed in a day; this one is invisible until somebody asks why the companion
    never brings anything up.
    """
    code, stderr = _boot_prod(
        EMBEDDING_PROVIDER="google",
        EMBEDDING_MODEL="gemini-embedding-001",
    )

    assert code != 0, "prod booted with google embeddings and no Vertex credentials"
    assert "ImproperlyConfigured" in stderr
    assert "EMBEDDING_PROVIDER" in stderr
    # It has to say what the silence costs, or the operator reads it as a
    # formality and sets the three settings to whatever makes the boot stop.
    assert "no vector" in stderr

    problem = next(line for line in stderr.splitlines() if "EMBEDDING_PROVIDER" in line)
    assert problem.isascii(), problem


def test_google_embeddings_with_the_credentials_set_boot(service_account_file: str) -> None:
    """The other half, and the reason the guard is a list rather than a hard no:
    an Anthropic-LLM box embedding on Vertex is a legitimate deployment, and it
    boots as long as it has the credential it depends on."""
    code, stderr = _boot_prod(
        EMBEDDING_PROVIDER="google",
        EMBEDDING_MODEL="gemini-embedding-001",
        GOOGLE_CLOUD_PROJECT="primer-test",
        GOOGLE_CLOUD_LOCATION="global",
        GOOGLE_APPLICATION_CREDENTIALS=service_account_file,
    )
    assert code == 0, stderr


def test_google_further_down_the_order_is_absence_rather_than_misconfiguration(
    service_account_file: str,
) -> None:
    """A fallback nobody configured is not a broken deployment — the same reading
    `TTS_FALLBACK_PROVIDERS` gets. Refusing to boot here would make adding a
    fallback provider a liability."""
    code, stderr = _boot_prod(AI_PROVIDER_ORDER="anthropic,google")
    assert code == 0, stderr
