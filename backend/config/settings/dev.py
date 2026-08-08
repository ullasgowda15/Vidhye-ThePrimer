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
