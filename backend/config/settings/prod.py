"""Production / staging settings."""

from django.core.exceptions import ImproperlyConfigured

from apps.common import model_ids
from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = False

# No default: a missing SECRET_KEY must fail the boot, not silently ship a known key.
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")

# Behind a TLS-terminating proxy (Render/Railway/Fly/ALB).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# The refresh cookie carries a fourteen-day session. Over plain HTTP it would be
# readable by anything on the path, so outside development it is TLS-only.
REFRESH_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# `STORAGES` (including the `media` alias generated illustrations and audio are
# written through) is defined once in base.py and driven by MEDIA_STORAGE.
# Redefining it here is how the media alias silently disappears in production, so
# it deliberately is not redefined — set MEDIA_STORAGE=s3 in the env.
if MEDIA_STORAGE != "s3":  # noqa: F405
    # Local disk is not a deployment target: containers are ephemeral and there is
    # no CDN in front of them, so a child's illustrations would vanish on restart.
    raise ImproperlyConfigured(
        "MEDIA_STORAGE must be 's3' outside development. Set it, plus "
        "AWS_STORAGE_BUCKET_NAME / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
        "(and AWS_S3_ENDPOINT_URL for a non-AWS S3-compatible bucket)."
    )

# ---------------------------------------------------------------------------
# The safety layer is not optional outside development (PRODUCT.md §13.2)
# ---------------------------------------------------------------------------
# Both guards ship **on** in `base.py`, so reaching either of these means someone
# turned one off in the environment. They fail the boot rather than logging a
# warning for the same reason `SECRET_KEY` does: a deployment that is quietly
# less safe than it looks is worse than one that will not start, and "the
# classifier has been off since March" is not something an incident review should
# ever have to discover.
#
# Note what is *not* checked here: the presence of a key. A provider outage is a
# runtime condition the pipeline already handles honestly — the deterministic
# policy stands, an incident records the degradation, and nothing is reported as
# a pass. What cannot be handled at runtime is somebody having decided the guard
# should not run at all.
if not SAFETY_CLASSIFIER_ENABLED:  # noqa: F405
    raise ImproperlyConfigured(
        "SAFETY_CLASSIFIER_ENABLED must stay on outside development. The "
        "independent output classifier (§13.3) is the guard that reads subtext; "
        "the keyword policy alone is not the product's safety story."
    )

if IMAGE_MODERATION == "off":  # noqa: F405
    raise ImproperlyConfigured(
        "IMAGE_MODERATION must not be 'off' outside development. Set it to "
        "'auto' and give the deployment credentials for a vision model - an "
        "ANTHROPIC_API_KEY, or the GOOGLE_CLOUD_* settings for Gemini on Vertex "
        "- so generated illustrations are actually looked at before a child "
        "sees them (§13.8)."
    )

# ---------------------------------------------------------------------------
# Gemini on Vertex needs all three of its settings (PRODUCT.md §16.1, §13.8)
# ---------------------------------------------------------------------------
# A half-configured Vertex deployment does not fail loudly, and that is the whole
# reason this guard exists. `GoogleProvider.is_configured()` returns *false* on a
# missing project — which is right at runtime, because falling through to the
# next provider is better than raising inside a beat — so a box that meant to run
# every model on Gemini and forgot `GOOGLE_CLOUD_PROJECT` does not crash. It
# quietly finds no configured provider at all and serves pre-vetted fallback
# beats to every child, forever, while the logs say the thing the fallback path
# always says. The image gate degrades the same way: `IMAGE_MODERATION=auto` with
# no usable credential is `moderation=absent` on every scan.
#
# So the check happens at boot, where "this deployment cannot do what it says"
# is still a sentence somebody reads.
#
# Only when Google is the *intended* one: first in `AI_PROVIDER_ORDER`, or named
# as the image moderator. Google sitting further down the order is a fallback
# nobody configured, which is absence rather than misconfiguration - the same
# reading `TTS_FALLBACK_PROVIDERS` gets.
#
# Plain ASCII, like the other guards: this is read in a terminal, and the Windows
# console renders a section sign as a replacement character.
_order = list(AI_PROVIDER_ORDER)  # noqa: F405
_wants_vertex = [
    label
    for label, wanted in (
        ("AI_PROVIDER_ORDER", bool(_order) and _order[0] == "google"),
        ("IMAGE_MODERATION_PROVIDER", IMAGE_MODERATION_PROVIDER == "google"),  # noqa: F405
    )
    if wanted
]
if _wants_vertex:
    from apps.common import vertex

    _missing = vertex.missing_for(
        GOOGLE_CLOUD_PROJECT,  # noqa: F405
        GOOGLE_CLOUD_LOCATION,  # noqa: F405
        GOOGLE_APPLICATION_CREDENTIALS,  # noqa: F405
    )
    if _missing:
        raise ImproperlyConfigured(
            f"{' and '.join(_wants_vertex)} selects google (Gemini on Vertex AI), but "
            f"{', '.join(_missing)} is not set. Vertex is authenticated by a service "
            "account, not an API key - that is what bills the call to the GCP project "
            "and keeps it under the terms that exclude our inputs from training "
            "(13.8, Docs/Hosting.md section 3). Set GOOGLE_CLOUD_PROJECT, "
            "GOOGLE_CLOUD_LOCATION and GOOGLE_APPLICATION_CREDENTIALS (an absolute "
            "path to the service-account JSON on this box). Without them nothing "
            "calls Gemini: every beat degrades to the pre-vetted fallback and the "
            "image gate reports moderation=absent."
        )

    from pathlib import Path

    # And that the file is really there. A path typo produces exactly the same
    # silent degradation the block above describes, one layer later, and it is the
    # commonest of the two mistakes: the settings get copied from the runbook and
    # the key gets left on the laptop it was downloaded to.
    if not Path(GOOGLE_APPLICATION_CREDENTIALS).is_file():  # noqa: F405
        raise ImproperlyConfigured(
            f"GOOGLE_APPLICATION_CREDENTIALS='{GOOGLE_APPLICATION_CREDENTIALS}' is not a "  # noqa: F405
            "file on this box. It must be an absolute path to the service-account JSON "
            "key, readable by the process (mount it into the container; never commit it)."
        )

# ---------------------------------------------------------------------------
# No child data down a route that may train on it (PRODUCT.md §13.8)
# ---------------------------------------------------------------------------
# `apps.accounts.privacy` tells every parent, in the present tense and without
# qualification: *"We never use your child's data to train anybody's AI models —
# not ours, and not our providers."* Until the Day 1-15 audit nothing enforced
# that sentence. Every one of the four models below was pointed at an OpenRouter
# `:free` route — the variant whose terms permit prompt logging and training, and
# which is free precisely because of that trade — and the preflight reported the
# safety grader as `OK` because it only asked whether the route was reachable.
#
# All four are named, including the beat model. The beat prompt carries the
# child's first name, their age band, the avoid list, the fears list and the
# retrieved memory bundle; the memory prompt carries the verbatim session digest.
# "Only the safety and memory models see child data" was never true here.
#
# A boot failure rather than a warning, for the same reason the three checks
# around it are: a deployment that breaks a promise the product makes in writing
# to a parent about their four-year-old is worse than a deployment that will not
# start. `manage.py launch_check` blocks on the same rule, so the person running
# the preflight sees it before the boot does.
_training_problems = [
    problem
    for _setting in model_ids.CHILD_DATA_MODEL_SETTINGS
    if (problem := model_ids.training_error(setting=_setting, model=globals().get(_setting, "")))
]
if _training_problems:
    raise ImproperlyConfigured("\n".join(_training_problems))

# ---------------------------------------------------------------------------
# Child memory needs a key of its own (PRODUCT.md §11.10, §13.8)
# ---------------------------------------------------------------------------
# `apps.common.crypto` derives a key from SECRET_KEY when this is unset, which is
# what makes development zero-setup. That derivation is not weaker cryptography —
# it is a key with the wrong *lifecycle*. Rotating SECRET_KEY is a routine thing to
# do after a leak, and doing it would silently make every memory ever written
# undecryptable: the most sensitive data we hold, lost, by a correct security
# action. §11.10 calls durability a first-class SLO, so this fails the boot.
if not MEMORY_ENCRYPTION_KEY:  # noqa: F405
    raise ImproperlyConfigured(
        "MEMORY_ENCRYPTION_KEY must be set outside development, so that child "
        "memory (§11.10) does not share a lifecycle with SECRET_KEY — rotating "
        "SECRET_KEY would otherwise orphan every memory ever written. Generate "
        'one with: python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())"'
    )

# ---------------------------------------------------------------------------
# The spend cap has to be a number somebody chose (PRODUCT.md §18.8, §22.4)
# ---------------------------------------------------------------------------
# `base.py` defaults it to 0.00 = "do not enforce", which is the right default for
# a development box and the wrong state for a deployment holding real families'
# accounts and a real payment method. There is no safe default value to pick here
# — the right cap comes from this deployment's own cost/session and how many
# children it serves (`manage.py metrics`) — so this asks for the number rather
# than inventing one.
#
# It is a *cost* control and not a safety one, and the failure mode is mild by
# construction: over the cap, children get cached art and pre-vetted warm beats
# (§13.3), never an error. That is exactly why it fails the boot instead of
# warning — an unset cap is invisible until the invoice arrives.
if not INFERENCE_BUDGET_USD_PER_WINDOW:  # noqa: F405
    raise ImproperlyConfigured(
        "INFERENCE_BUDGET_USD_PER_WINDOW must be set outside development — the "
        "global inference-budget circuit breaker (§18.8) does not enforce a cap "
        "of zero. Size it from `manage.py metrics` (cost/session x sessions per "
        f"{INFERENCE_BUDGET_WINDOW_S}s, with headroom); see "  # noqa: F405
        "Docs/Runbooks/Launch.md."
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
