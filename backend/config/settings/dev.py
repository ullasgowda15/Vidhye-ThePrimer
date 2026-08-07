"""Local development settings."""

from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]

# Vite dev server. Kept permissive in dev only.
CORS_ALLOW_ALL_ORIGINS = True

# Fail loudly in dev if a task blows up, instead of silently retrying forever.
CELERY_TASK_EAGER_PROPAGATES = True
