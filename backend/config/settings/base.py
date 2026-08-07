"""Shared Django settings for The Primer (PRODUCT.md §16.3).

Environment-driven. ``dev.py`` and ``prod.py`` import everything here and then
override only what differs per environment.
"""

from datetime import timedelta
from pathlib import Path

import environ

# backend/
BASE_DIR = Path(__file__).resolve().parents[2]
# repo root
REPO_ROOT = BASE_DIR.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
)

# .env may live at the repo root (preferred) or beside manage.py.
for _candidate in (REPO_ROOT / ".env", BASE_DIR / ".env"):
    if _candidate.exists():
        environ.Env.read_env(_candidate)
        break

SERVICE_NAME = "primer"
SERVICE_VERSION = "0.1.0"

SECRET_KEY = env("SECRET_KEY", default="django-insecure-day1-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    # The blacklist app is what makes logout and refresh-rotation real: without
    # it a "revoked" refresh token stays valid until it expires (§18.8).
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_celery_results",
]

# One Django app per service boundary in PRODUCT.md §5.2. Most are empty shells
# on Day 1; they exist so later days drop code into a stable home.
LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.profiles",
    "apps.companion",
    "apps.story",
    "apps.learning",
    "apps.memory",
    "apps.safety",
    "apps.media",
    "apps.analytics",
    "apps.billing",
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database (Postgres + pgvector — PRODUCT.md §16.5)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://primer:primer@localhost:5432/primer",
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email is the login identifier. This MUST be set before the first migration.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# I18N / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# DRF + OpenAPI (PRODUCT.md §18.1)
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # Bearer access tokens are the product's auth (§18.2). Session auth stays
        # only so the admin and the browsable API remain usable in dev.
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Deny by default. Every public endpoint (health, signup, login, refresh, the
    # schema) opts out explicitly, so forgetting to think about auth on a new view
    # locks it down rather than opening it up.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # §18.2: per-parent and per-IP limits. `auth` is the tighter scope applied by
    # hand to the credential endpoints, which are the ones worth guessing at.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="100/day"),
        "user": env("THROTTLE_USER", default="1000/day"),
        "auth": env("THROTTLE_AUTH", default="30/hour"),
    },
}

# ---------------------------------------------------------------------------
# JWT (PRODUCT.md §18.2, §18.8)
# ---------------------------------------------------------------------------
# Short access + long rotating refresh: a stolen access token is worthless in
# minutes, while a family that opens the app once a fortnight stays logged in.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": SECRET_KEY,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "The Primer API",
    "DESCRIPTION": (
        "The Primer — an AI-generated living storybook and lifelong learning "
        "companion for children aged 3-7."
    ),
    "VERSION": SERVICE_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api",
}

# ---------------------------------------------------------------------------
# CORS — the Vite dev server is a separate origin (PRODUCT.md §16.2)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# Where the parent-facing app lives, for links we put in email. Day 2 only logs
# the verification link to the console (a real provider lands later).
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://localhost:5173")

# The consent version a parent must hold before any child data is collected
# (§13.7). Bumping this string forces every parent to re-consent, which is the
# point: a materially changed disclosure is a new consent.
REQUIRED_CONSENT_TYPE = env("REQUIRED_CONSENT_TYPE", default="data_collection_v1")

# ---------------------------------------------------------------------------
# Celery (PRODUCT.md §16.3)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
# Celery 6 default; setting it now keeps startup retries and silences the warning.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------------------------------------------------------------------
# AI layer (PRODUCT.md §19.3)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")

# OpenRouter is OpenAI-API-compatible and fronts many models, including free ones.
# It is an *option*, not the default: selecting it is purely a matter of putting it
# first in AI_PROVIDER_ORDER.
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
# Optional OpenRouter attribution header. Not a secret, not child data.
OPENROUTER_SITE_URL = env("OPENROUTER_SITE_URL", default="http://localhost:5173")

# Claude is primary; OpenAI/Google are the fallback seam (not implemented yet).
# AI_BEAT_MODEL is provider-relative: a Claude model id for `anthropic`, an
# OpenRouter model slug (e.g. `vendor/model:free`) for `openrouter`.
AI_BEAT_MODEL = env("AI_BEAT_MODEL", default="claude-opus-5")
AI_PROVIDER_ORDER = env.list("AI_PROVIDER_ORDER", default=["anthropic"])

# How hard to insist on provider-enforced structured output: auto | strict |
# json_object. `auto` (try strict, degrade if the model can't) is the shipping
# behaviour. Applies to OpenAI-compatible providers; Anthropic enforces natively.
AI_STRUCTURED_OUTPUT_MODE = env("AI_STRUCTURED_OUTPUT_MODE", default="auto")
