"""The eval harness's fixtures (PRODUCT.md §19.7).

**What this suite is, precisely.** It plays the *product's own loop* — the real
orchestrator, the real prompt assembly, the real safety pipeline, the real
fallbacks — and grades what comes out. It is not a test of a model's talent and it
does not pretend to be one.

Two modes, and the difference is exactly one thing:

* **scripted** (the default, and the one CI gates on). The provider is
  `harness.EvalProvider`: it answers by schema, deterministically, and it can be
  told to answer *badly*. What is graded is therefore the pipeline — does the
  learning objective actually reach the beat that is delivered, does the
  companion stay one character, does the close ask nothing, does an injected
  instruction reach an instruction layer, does a blocked beat still leave a child
  with something to tap. Those are properties of our code, they are the ones a
  prompt or a router change breaks, and they are checkable on every commit for
  nothing.
* **live** (`pytest evals/ --live`). The same scenarios against the configured
  provider, spending real money. This is the end-to-end red-team pass a person
  runs before a release, and the only mode in which the age rubric's model-grader
  runs. Never in CI: CI has no keys, deliberately (`ci.yml` says why).

**The gate is the scripted mode.** A suite whose ship gate depended on a live
provider would be a gate that goes red when a vendor has a bad afternoon, and a
gate that goes red for reasons unrelated to the change is a gate somebody
disables. `thresholds.py` holds the numbers; every test states which one it is
asserting against.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Grade against the configured provider instead of the scripted one. Spends money.",
    )
    parser.addoption(
        "--spotcheck",
        action="store_true",
        default=False,
        help="Write the graded beats to evals/spotcheck/ for a human to read (§19.7's (c)).",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "live: only meaningful against a real provider.")


@pytest.fixture(scope="session")
def live(pytestconfig: Any) -> bool:
    return bool(pytestconfig.getoption("--live"))


@pytest.fixture(autouse=True)
def _offline(live: bool, tmp_path: Any, settings: Any) -> None:
    """Media and credentials, pinned for the scripted run.

    The same rule `backend/conftest.py` applies to the unit suite, and for the
    same reason: a developer's shell holds keys, CI does not, and an eval that
    quietly spends money on one machine and not the other is not a gate.

    In `--live` mode only the *media* half is pinned. The point of a live run is
    the language model; rendering six illustrations to grade a warm close would
    spend real money on the one part of the beat no grader in this suite reads.
    """
    settings.MEDIA_ROOT = str(tmp_path)
    settings.STORAGES = {
        **settings.STORAGES,
        "media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path), "base_url": "/media/"},
        },
    }
    settings.MEDIA_CDN_BASE_URL = ""
    settings.IMAGE_PROVIDER = "local"
    settings.TTS_PROVIDER = "local"
    settings.TTS_FALLBACK_PROVIDERS = []
    settings.EMBEDDING_PROVIDER = "local"
    settings.CLOUDFLARE_ACCOUNT_ID = ""
    settings.CLOUDFLARE_API_TOKEN = ""
    settings.ELEVENLABS_API_KEY = ""

    if live:
        return

    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = ""
    settings.OPENAI_API_KEY = ""
    # The model-backed guard needs a provider. The *deterministic* policy check and
    # the scrubber need none and stay on — which is the half of §13.2 this suite
    # can hold to a standard on every commit, and `test_redteam.py` says so.
    settings.SAFETY_CLASSIFIER_ENABLED = False
    settings.IMAGE_MODERATION = "off"


@pytest.fixture(autouse=True)
def _clear_flag_cache() -> Any:
    """Same hazard as the backend suite's fixture of this name, same reason.

    `flags` caches for `CACHE_TTL_S` seconds in a process-global cache, so a flag
    set by one eval would silently govern the next ten seconds of them. A gate
    whose result depends on how fast the machine is running is not a gate.
    """
    from apps.common import flags

    flags.invalidate()
    yield
    flags.invalidate()


@pytest.fixture(autouse=True)
def _no_workers(monkeypatch: Any) -> None:
    """No broker. The eval grades beats, not the scheduler.

    Media and prefetch are enqueued through `transaction.on_commit`, and a suite
    that queued them into a broker nobody is draining would be measuring a
    timeout.
    """
    from apps.story import orchestrator

    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
    monkeypatch.setattr(orchestrator, "schedule_advance", lambda adventure, after: "eval")


#: The gate drill's switch (§19.7: *"prove by forcing a fail"*).
#:
#:     PRIMER_EVAL_FORCE_REGRESSION=cliffhanger_close pytest evals/ -q
#:
#: Applies a named regression from `harness.REGRESSIONS` to every scripted run, so
#: the suite goes red for a real reason — a story that ends on an unanswered
#: threat, a companion who changes name, an objective that never lands — and the
#: deploy is blocked by the same mechanism a genuine regression would trip. An
#: env var rather than an edit, so the drill is reproducible by anybody, in CI,
#: without a commit that has to be remembered and reverted.
#:
#: `Docs/Runbooks/Eval-Gate.md` is the drill.
FORCE_REGRESSION_ENV = "PRIMER_EVAL_FORCE_REGRESSION"


@pytest.fixture
def provider(live: bool, monkeypatch: Any) -> Any:
    """The thing under grading. Scripted unless `--live`."""
    import os

    from harness import EvalProvider

    if live:
        return None

    from apps.story.ai.router import ModelRouter

    scripted = EvalProvider()
    if forced := os.environ.get(FORCE_REGRESSION_ENV, "").strip():
        scripted.regress(*[name.strip() for name in forced.split(",") if name.strip()])

    monkeypatch.setattr(
        ModelRouter,
        "from_settings",
        classmethod(lambda cls, **_: ModelRouter(providers=[scripted])),
    )
    return scripted


@pytest.fixture
def child(db: Any) -> Any:
    """A five-year-old with a companion, interests, a fear and an avoid list.

    Every field is load-bearing for some grader: the interests are what
    `test_golden.py` follows into a beat, the fear and the avoid topic are what
    `test_redteam.py` probes the ceiling with, and the age band selects the rubric
    `test_age_rubric.py` grades against.
    """
    from apps.companion.service import create_companion
    from apps.profiles.models import Child
    from django.contrib.auth import get_user_model

    parent = get_user_model().objects.create_user(
        email="evals@example.com", password="storybook-primer-42"
    )
    subject = Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs", "rivers"],
        fears=["the dark"],
        avoid_topics=["hospitals"],
        intensity_ceiling=1,
    )
    companion = create_companion(
        child=subject,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    companion.visual_descriptor = "a small round friendly dragon with warm ember scales"
    companion.save(update_fields=["visual_descriptor"])
    return subject


@pytest.fixture
def spotcheck(pytestconfig: Any) -> Any:
    """§19.7's (c): "a rubric + model-grader **+ human spot-check**".

    The hook a person actually uses. With `--spotcheck` every graded beat is
    written to `evals/spotcheck/`, in reading order, as plain markdown — because
    the third leg of the age-appropriateness rubric is somebody reading the
    stories out loud, and a hook that produced a JSON dump would not be read.
    """
    from harness import SpotcheckWriter

    return SpotcheckWriter(enabled=bool(pytestconfig.getoption("--spotcheck")))
