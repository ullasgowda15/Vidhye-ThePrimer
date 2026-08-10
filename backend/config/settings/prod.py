"""Production / staging settings."""

from django.core.exceptions import ImproperlyConfigured

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
        "'auto' and give the deployment an ANTHROPIC_API_KEY so generated "
        "illustrations are actually looked at before a child sees them (§13.8)."
    )

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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
