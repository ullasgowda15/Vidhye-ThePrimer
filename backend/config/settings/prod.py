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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
