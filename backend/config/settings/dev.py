"""Local development settings."""

from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]

# ---------------------------------------------------------------------------
# CORS — scoped, even here (PRODUCT.md §13.1)
# ---------------------------------------------------------------------------
# This used to be `CORS_ALLOW_ALL_ORIGINS = True`, which combines with
# `CORS_ALLOW_CREDENTIALS = True` in base.py to reflect *any* origin back with
# `access-control-allow-credentials: true`. That was cosmetically wrong while the
# session was a bearer token in memory and nothing was attached automatically.
#
# It stops being cosmetic the moment a cookie carries the session, which is now:
# a page on any origin a developer visits could then make credentialed requests
# to their local API and read the responses. `SameSite=Lax` is the primary
# control; a scoped allow-list is its defence-in-depth pair, and the two belong
# together.
#
# A regex rather than the literal list from base.py so any localhost port works
# (Vite picks a new one when 5173 is taken, and a second frontend on 5174 is a
# normal thing to be running) without opening the door to arbitrary origins.
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^http://localhost:\d+$",
    r"^http://127\.0\.0\.1:\d+$",
]

# Fail loudly in dev if a task blows up, instead of silently retrying forever.
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Illustrations: local placeholders, and no cloud vendor by accident
# ---------------------------------------------------------------------------
# `base.py` defaults `IMAGE_PROVIDER` to `cloudflare` with a `google` fallback,
# which is right for a deployment and wrong for a laptop: every `make run`, every
# manual click through a story, every half-finished experiment drew real FLUX
# images and spent real quota. The quota that then ran out was the one a child
# was going to need.
#
# So development draws the deterministic offline placeholder instead. The
# pipeline it exercises is the same one — hashing, the structural safety scan,
# storage, CDN URLs, the cache — because that is what `local` exists for, and
# every `Media` row still records which provider drew it, so placeholder art is
# never mistaken for the real thing.
#
# **Set here rather than in `.env`, and deliberately not `env(...)`-readable.**
# A default is a thing you forget you are relying on; a `.env` copied from
# `.env.example` ships `IMAGE_PROVIDER=cloudflare`, and the whole point of this
# block is that local development cannot reach a paid image vendor by accident.
# `prod.py` is a separate module and is untouched.
#
# Three settings rather than one, because one would not be enough:
#
#   IMAGE_PROVIDER            the primary
#   IMAGE_FALLBACK_PROVIDERS  emptied, or a local failure would fail *over* to
#                             Gemini and quietly spend on Vertex instead
#   IMAGE_MODERATION          `off`, which base.py already names as the mode
#                             "CI and offline development use" — it is
#                             structural-only, never unscanned, and it stops a
#                             vision call to Gemini on a placeholder we drew
#
# The escape hatch is `ALLOW_CLOUD_IMAGES=1`, and it exists for exactly four
# commands: `make smoke-media`, `make smoke-story`, `make render-companion` and
# `make render-consistency`. Those run through `manage.py`, so they load *these*
# settings, and their entire purpose is to call the real vendors and look at what
# comes back. Without an opt-in they would silently render placeholders and
# report success, which is worse than not having them.
if env.bool("ALLOW_CLOUD_IMAGES", default=False):
    IMAGE_PROVIDER = env("IMAGE_PROVIDER", default="cloudflare")
    IMAGE_FALLBACK_PROVIDERS = env.list("IMAGE_FALLBACK_PROVIDERS", default=["google"])
    IMAGE_MODERATION = env("IMAGE_MODERATION", default="auto")
else:
    IMAGE_PROVIDER = "local"
    IMAGE_FALLBACK_PROVIDERS = []
    IMAGE_MODERATION = "off"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Django's default configuration routes the `django` logger to the console and
# leaves everything else to the root logger, which has no handler — so every
# `logger.info` in `apps/` went nowhere. That is fine for a request log and wrong
# for this pipeline: the story orchestrator's own output is how anyone sees a
# cache hit, a repair attempt, a plan falling back, or the `story.media.timing`
# lines that show the illustration and the voice overlapping (§16.4). Those are
# the observability of the product, and a developer running `make run` should be
# able to read them without configuring anything.
#
# `prod.py` already configures the root logger; this is the dev equivalent.
# The shape is deliberate: `apps` sets a *level* and keeps propagating, and the
# one console handler lives on the root logger. Two consequences follow, and both
# are the reason not to hang a handler on `apps` directly:
#
# * `apps.*` records are emitted at INFO while `httpx`, `boto3` and friends stay
#   at the root's WARNING — a logger's level decides whether a record is created,
#   and propagation to an ancestor's handler ignores that ancestor's level. So we
#   get our own output turned up without turning everything up.
# * Anything that attaches a handler to the root logger sees them. `caplog` does
#   exactly that, and a `propagate: False` here would make every log assertion in
#   the test suite silently pass against an empty capture.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"dev": {"format": "%(levelname)-7s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "dev"}},
    "loggers": {
        "apps": {"level": "INFO", "propagate": True},
        # Django's own default config already gives `django` a console handler and
        # lets it propagate, which would print every request line twice against
        # the root handler below. Its handlers are replaced here, not added to.
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}
