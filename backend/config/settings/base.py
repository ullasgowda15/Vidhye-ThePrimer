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
# Object storage for generated media (PRODUCT.md §16.6, §5.8)
# ---------------------------------------------------------------------------
# Illustrations and narration audio are blobs: they live in object storage and
# are served by a CDN, never streamed by Django and never stored in Postgres.
# The `media` alias is what `apps.media.storage` writes through, so swapping
# S3 for R2/B2/MinIO — or for the local disk in dev — is this block only.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# local (dev default, zero setup) | s3 (anything S3-compatible)
MEDIA_STORAGE = env("MEDIA_STORAGE", default="local")

AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default="") or None
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="auto")

# The public base a child's device actually fetches from. Set this to the CDN
# hostname in front of the bucket; media URLs are then built by string join and
# never carry a signature, which is what makes them cacheable at the edge.
MEDIA_CDN_BASE_URL = env("MEDIA_CDN_BASE_URL", default="").rstrip("/")

_S3_STORAGE = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "bucket_name": AWS_STORAGE_BUCKET_NAME,
        "endpoint_url": AWS_S3_ENDPOINT_URL,
        "region_name": AWS_S3_REGION_NAME,
        "default_acl": None,
        # Generated media is immutable and content-addressed, so a long
        # max-age is free: the key changes whenever the bytes do.
        "object_parameters": {"CacheControl": "public, max-age=31536000, immutable"},
        "file_overwrite": True,
        "querystring_auth": False,
    },
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "media": (
        _S3_STORAGE
        if MEDIA_STORAGE == "s3"
        else {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    ),
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

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
        # Guessing at credentials. Tight on purpose: a human types their password
        # wrong a handful of times, never thirty.
        "auth": env("THROTTLE_AUTH", default="30/hour"),
        # Restoring a session is *not* a credential attempt, and must not share the
        # budget above. The app refreshes once per page load (§14.5) — automatic
        # traffic, several an hour during ordinary use and far more while a
        # developer reloads. Sharing one scope meant those page loads spent the
        # login allowance, and the parent was then refused on a correct password
        # for the rest of the hour. Same mistake as `companion` below, one layer up.
        #
        # Unauthenticated, so it can only be keyed by IP, which a family behind one
        # NAT shares — sized well above anything real rather than tight, since the
        # thing it guards against is a runaway retry loop and not a guessable
        # secret (a refresh token is signed, and rotation makes a stolen one
        # single-use).
        "session_refresh": env("THROTTLE_SESSION_REFRESH", default="240/hour"),
        # Creating a companion fans out into a handful of paid image calls plus a
        # TTS call (§16.6). A family does it once per child; anything beyond a
        # couple of attempts an hour is a bug or an attack, and either way it is
        # spending real money.
        "companion": env("THROTTLE_COMPANION", default="20/hour"),
        # *Reading* a companion is one indexed row lookup, and the greeting scene
        # polls it while the art renders (§4.3). It therefore gets its own scope,
        # sized for that poll and not for the create above — sharing one budget
        # between them is what made the poll spend the create's allowance in forty
        # seconds and then 429 itself into a companion that never woke up.
        #
        # 480/hour against a poll that settles at 6/min leaves real headroom, so
        # the product's own client never reaches it while a runaway one still
        # stops.
        "companion_read": env("THROTTLE_COMPANION_READ", default="480/hour"),
        # Beginning an adventure costs a plan call, a beat call, an illustration
        # and a TTS call before the child has done anything (§22.4). A family
        # starts a few a day; a loop that starts one a second is a bug.
        "adventure": env("THROTTLE_ADVENTURE", default="60/hour"),
        # *Asking whether* a story is in progress is one indexed lookup, and the
        # child surface does it on every mount so a reload rejoins the same story.
        # Its own scope for the same reason `companion_read` has one: a read that
        # spends the write budget locks the product out of its own feature.
        "adventure_read": env("THROTTLE_ADVENTURE_READ", default="480/hour"),
        # Submitting a choice costs the next beat: a language call, an
        # illustration, a voice line and up to three choice icons (§22.4). A full
        # adventure is five to seven of these, so this is sized for a handful of
        # adventures an hour with room for a child who taps twice — and not for a
        # client stuck in a loop, which is what it exists to stop.
        "adventure_choice": env("THROTTLE_ADVENTURE_CHOICE", default="120/hour"),
        # Opening the beat stream. This is where a beat is actually generated, so
        # it needs a ceiling of its own — the `adventure` scope above never saw it,
        # because the stream is a plain async view rather than a DRF one. Sized for
        # reloads rather than for starts: a reload re-opens the stream and replays,
        # which is cheap, and React StrictMode opens it twice per mount in dev.
        "adventure_stream": env("THROTTLE_ADVENTURE_STREAM", default="180/hour"),
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

# ---------------------------------------------------------------------------
# The refresh cookie (PRODUCT.md §18.2, §14.5)
# ---------------------------------------------------------------------------
# The child experience runs under a parent session and **a child cannot log in**
# (§18.2). So the session has to survive a cold start — a morning, a reboot, a
# tab the OS evicted — or a three-year-old is dropped at a login form, which is a
# fail state and not an inconvenience (§14.5).
#
# It is persisted in an `httpOnly` cookie rather than `localStorage` because we
# control the backend and therefore have no reason to choose the option that any
# script on the origin can read: an XSS on a page showing a child's memories must
# not also hand over a fourteen-day refresh token.
#
# `SameSite=Lax` is the **load-bearing CSRF control** here, not a hardening
# extra. The refresh endpoint is unauthenticated by design (the cookie *is* the
# credential), so without it any page on the internet could POST to it in a
# signed-in parent's browser and rotate their session. Lax withholds the cookie
# from cross-site POSTs, which is exactly that attack. It requires the API and the
# app to be same-site in production (`api.theprimer.app` / `theprimer.app`) — see
# DEPLOY.md. Scoped CORS is its defence-in-depth pair.
REFRESH_COOKIE_NAME = env("REFRESH_COOKIE_NAME", default="primer_refresh")
# Path-scoped to the two endpoints that need it (refresh, logout) rather than
# sent with every API call. The access token is a header; this is not.
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_COOKIE_SAMESITE = "Lax"
# Overridden to True in prod.py. False here so the cookie works over plain HTTP
# on localhost; a `Secure` cookie is simply never stored by the browser in dev.
REFRESH_COOKIE_SECURE = False

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
    # The companion catalogue's keys appear on several fields (`key`,
    # `default_palette`, `archetype`). Without these, the generator invents
    # names like `Key734Enum` from a hash, and the generated TypeScript union
    # would be renamed by any unrelated change to the catalogue.
    "ENUM_NAME_OVERRIDES": {
        "ArchetypeEnum": "apps.companion.catalog.ARCHETYPE_KEYS",
        "PaletteEnum": "apps.companion.catalog.PALETTE_KEYS",
        "PersonalityPresetEnum": "apps.companion.catalog.PRESET_KEYS",
    },
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

# ---------------------------------------------------------------------------
# Illustration + voice adapters (PRODUCT.md §16.6, §5.8)
# ---------------------------------------------------------------------------
# One name each, chosen by configuration, resolved by the adapter registry. A
# provider that is named but has no credentials is an error rather than a silent
# downgrade: quietly serving placeholder art in production would be invisible
# until a parent noticed their child's storybook looked wrong.
#
# `local` is the deliberate offline choice — a deterministic placeholder that
# draws a flat vector character from the prompt's own character/scene sections
# and synthesises a short chime. It exists so the *pipeline* (hashing, safety
# scan, storage, CDN URLs, cache) is exercisable and testable with no key and no
# spend. Every Media row records the provider that produced it, so placeholder
# art is never mistaken for the real thing.
IMAGE_PROVIDER = env("IMAGE_PROVIDER", default="cloudflare")
TTS_PROVIDER = env("TTS_PROVIDER", default="elevenlabs")

# Cloudflare Workers AI runs FLUX (§5.8's named image models) at the edge.
CLOUDFLARE_ACCOUNT_ID = env("CLOUDFLARE_ACCOUNT_ID", default="")
CLOUDFLARE_API_TOKEN = env("CLOUDFLARE_API_TOKEN", default="")
IMAGE_MODEL = env("IMAGE_MODEL", default="@cf/black-forest-labs/flux-1-schnell")
# flux-1-schnell is a few-step distilled model; 4 is its default and 8 its ceiling.
IMAGE_STEPS = env.int("IMAGE_STEPS", default=4)

# ElevenLabs is the companion's voice.
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", default="")
ELEVENLABS_MODEL_ID = env("ELEVENLABS_MODEL_ID", default="eleven_multilingual_v2")
# Optional map from a companion's semantic voice key (§9.1, set from its
# archetype) to a real ElevenLabs voice id, as JSON:
#   ELEVENLABS_VOICE_MAP={"warm_low":"21m00Tcm...","bright":"AZnzlk1X..."}
# Unmapped keys fall back to ELEVENLABS_VOICE_ID, so one configured voice is
# enough to ship and per-archetype voices are a config change later.
ELEVENLABS_VOICE_MAP = env.json("ELEVENLABS_VOICE_MAP", default={})

# A generated asset that is wildly larger than a storybook illustration is a
# provider bug or a hostile response, not a picture. The scan rejects it before
# it reaches storage (§13.8).
MEDIA_MAX_BYTES = env.int("MEDIA_MAX_BYTES", default=12 * 1024 * 1024)
