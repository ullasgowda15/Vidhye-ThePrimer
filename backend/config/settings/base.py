"""Shared Django settings for The Primer (PRODUCT.md §16.3).

Environment-driven. ``dev.py`` and ``prod.py`` import everything here and then
override only what differs per environment.
"""

from datetime import timedelta
from pathlib import Path

import environ

# Django is not configured yet at this point, so this import is only safe because
# `model_ids` imports nothing but the standard library — see its docstring.
from apps.common import model_ids

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
        # The returning greeting (§4.5). One language call, one TTS call, and a
        # vector query — paid once when the child arrives, and the audio is cached
        # on `(voice, text)` so a reload re-synthesises nothing. Sized like the
        # companion create rather than like a read, because a client that asked for
        # it in a loop would be generating text for a child who is already looking
        # at the previous one.
        "greeting": env("THROTTLE_GREETING", default="30/hour"),
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
# Safety layer (PRODUCT.md §13.2, §13.3)
# ---------------------------------------------------------------------------
# **The independent output classifier is a different model, on purpose** (§13.3):
# "a model grading its own output is weak; a separate guard is defense-in-depth."
# Provider-relative in exactly the way AI_BEAT_MODEL is — a Claude model id under
# `anthropic`, an OpenRouter slug under `openrouter` — and it rides the same
# AI_PROVIDER_ORDER, because a provider outage that took the generator down took
# the grader with it either way.
#
# Defaulted to a small fast model rather than the beat model: grading against a
# rubric is a classification problem, and putting the expensive model on it would
# add its latency to *every* beat for a judgement the cheap one makes as well.
AI_SAFETY_MODEL = env("AI_SAFETY_MODEL", default="claude-haiku-4-5-20251001")

# Whether the model-backed half of the output guard runs at all.
#
# The deterministic policy check (`apps.safety.policy`) always runs and needs no
# provider; this switches on the model call that sits on top of it. Defaulted
# **on**, because the alternative is a product whose strongest guard is off unless
# someone remembers to turn it on. With no credentials the call fails, and the
# pipeline records that as a *degradation* with an incident row — never as a pass.
SAFETY_CLASSIFIER_ENABLED = env.bool("SAFETY_CLASSIFIER_ENABLED", default=True)

# How many constrained regenerations one beat may cost before the pre-vetted
# fallback is served instead (§13.2's soft-fail loop, §19.6's bound). Two, because
# the child is waiting and a third model call buys less than the fallback does.
SAFETY_MAX_REWRITES = env.int("SAFETY_MAX_REWRITES", default=2)

# The image safety gate (§13.8). `auto` runs the vision moderation call when a
# provider is configured and falls back to the structural checks when it is not,
# logging the gap; `off` is structural-only and is what CI and offline
# development use. There is no mode in which bytes reach storage unscanned.
IMAGE_MODERATION = env("IMAGE_MODERATION", default="auto")
# Provider-relative, like the two above. Must be a vision-capable model.
IMAGE_MODERATION_MODEL = env("IMAGE_MODERATION_MODEL", default="claude-haiku-4-5-20251001")

# ---------------------------------------------------------------------------
# Memory (PRODUCT.md §11)
# ---------------------------------------------------------------------------
# The model that consolidates a finished sitting into a few typed memory
# candidates (§11.4, `apps.memory.prompts`). Provider-relative and riding the same
# `AI_PROVIDER_ORDER` as the two above, so it is checked by `_check_model_pairing`
# below for the same reason they are.
#
# Defaulted to *whatever the safety grader is using*, rather than to a model id of
# its own. Both want the same kind of model — small, fast, good at reading a rubric
# — and inheriting means a deployment that has already picked a provider-correct
# small model does not have to pick a second one. A literal default here would have
# shipped an Anthropic id into every OpenRouter deployment and been caught by
# `_check_model_pairing` below as a boot failure on a configuration that was
# otherwise entirely correct.
AI_MEMORY_MODEL = env("AI_MEMORY_MODEL", default=AI_SAFETY_MODEL)

# How long short-term memory lives in Redis (§11.2). "A few hours": long enough
# that a consolidation job delayed by a worker restart still finds the sitting,
# short enough that STM is genuinely ephemeral working memory rather than an
# unpolicied second copy of every session (§13.8). The TTL is a backstop —
# `consolidation` deletes the key when it is done with it.
MEMORY_STM_TTL_S = env.int("MEMORY_STM_TTL_S", default=6 * 60 * 60)

# The ceiling on one sitting's event log. A session is a handful of beats and a
# handful of taps, so this is far above anything real; it exists so a client stuck
# in a submit loop costs a trimmed list rather than an unbounded Redis key.
MEMORY_STM_MAX_EVENTS = env.int("MEMORY_STM_MAX_EVENTS", default=500)

# The salience score a candidate must reach to become a long-term memory (§11.4).
# **This is the tuning dial of the whole memory system.** Too low and the table
# fills with "the child tapped to carry on", retrieval stops being about what
# matters, and every prompt gets more expensive; too high and the companion
# forgets the dragon the child named. `memory.consolidate` logs the score of the
# best *rejected* candidate on every run, which is how you tell which way to move
# it.
MEMORY_SALIENCE_THRESHOLD = env.float("MEMORY_SALIENCE_THRESHOLD", default=0.5)

# The most memories one sitting may produce, whatever they scored. A ceiling as
# well as a threshold because they fail differently: the threshold judges one
# moment, this one says what a sitting *is*. Half an hour with a four-year-old
# contains a handful of things worth remembering.
MEMORY_MAX_PER_SESSION = env.int("MEMORY_MAX_PER_SESSION", default=6)

# How many tokens of retrieved memory one generation may carry (§11.9, §19.4).
# **The dial that keeps "remembers everything" affordable.** §19.4's instruction is
# "few, high-value memories", and this is the number that makes it true whatever a
# child's history grows to: the bundle is ranked and then cut here, so a family
# three years in pays the same per beat as a family three days in. Roughly a dozen
# one-sentence memories at 800; raising it makes every beat of every adventure more
# expensive, forever, and gives the model more to be distracted by.
MEMORY_RETRIEVAL_TOKEN_BUDGET = env.int("MEMORY_RETRIEVAL_TOKEN_BUDGET", default=800)

# How many nearest neighbours the vector half fetches before ranking. Comfortably
# more than survives the budget, because the ranking sees signals the ANN query
# cannot — salience, recall recency, and what the rule half has already claimed.
# This is a bound on one indexed query, not on the prompt.
MEMORY_RETRIEVAL_CANDIDATES = env.int("MEMORY_RETRIEVAL_CANDIDATES", default=20)

# ---------------------------------------------------------------------------
# "No use of child data to train third-party models" (§13.8) — what that means here
# ---------------------------------------------------------------------------
# Two settings in this section send a child's session to a third party:
# `AI_MEMORY_MODEL` gets the session digest, and `EMBEDDING_PROVIDER` (below) gets
# each memory's one-sentence content. Encryption at rest does nothing about either
# — the plaintext is the request body — so the commitment is contractual and it is a
# *deployment* obligation, written down here because this is where somebody choosing
# a provider will look:
#
#   Every provider named in this file must be used under API terms that exclude
#   inputs from model training. Anthropic and the OpenAI *API* both exclude API
#   traffic from training by default; a self-serve consumer plan or a gateway that
#   resells prompts (some free OpenRouter routes do) does not, and must not be put
#   in `AI_PROVIDER_ORDER` or `EMBEDDING_PROVIDER` for that reason.
#
# What the code can do about it, it does: the digest carries each beat's first
# clause rather than the whole story, `content` is one sentence, and neither
# carries anything identifying beyond the child's first name (§13.5's scrub strips
# PII on the way in). Data minimisation is the part of this that is enforceable in
# software; the rest is a purchasing decision.

# The key `memory.content` is encrypted with at rest (§11.10, §13.8). Unset here,
# and derived from SECRET_KEY by `apps.common.crypto`, which keeps development
# zero-setup; `prod.py` refuses to boot without a real one, because rotating
# SECRET_KEY would otherwise orphan every memory ever written. Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MEMORY_ENCRYPTION_KEY = env("MEMORY_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# Embeddings (PRODUCT.md §11.3, §16.6)
# ---------------------------------------------------------------------------
# The third adapter in `apps.media.adapters`, same shape as image and TTS: one
# provider name, resolved by a registry, and `local` as the deliberate offline
# choice. `local` is a hashing trick rather than a model — it makes the vector
# column, the ANN index and the ordering of a nearest-neighbour query exercisable
# with no key and no spend, and it is lexical, not semantic. Every consolidation
# run says which was used.
EMBEDDING_PROVIDER = env("EMBEDDING_PROVIDER", default="openai")
EMBEDDING_MODEL = env("EMBEDDING_MODEL", default="text-embedding-3-small")

# The width of `memory.embedding`, and therefore of every vector we store. Changing
# it is a migration plus a re-embed of every row, not a config flip — which is why
# `adapters.embedding.embed` rejects a provider whose output does not match rather
# than padding or truncating.
EMBEDDING_DIMENSIONS = env.int("EMBEDDING_DIMENSIONS", default=1536)

# Used by the embedding adapter and nowhere else. Anthropic publishes no embedding
# model, so this is the one place the OpenAI key is load-bearing rather than a
# fallback seam — which usefully means remembering and story-telling fail
# independently.
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")


# ---------------------------------------------------------------------------
# Provider/model pairing — checked here, at boot (PRODUCT.md §13.3, §19.3)
# ---------------------------------------------------------------------------
# `AI_BEAT_MODEL` and `AI_SAFETY_MODEL` are *provider-relative* and both ride one
# `AI_PROVIDER_ORDER`. That makes one specific mistake very easy and completely
# silent: put `openrouter` first, set `AI_BEAT_MODEL` to a slug, and forget that
# `AI_SAFETY_MODEL` still holds its Anthropic-flavoured default. Beats generate
# fine; the gateway answers `400 … is not a valid model ID` to *every* classifier
# call, and the strongest guard in the product is off. Day 7's review found
# exactly this on the founder's own machine.
#
# It fails the boot rather than logging, for the same reason `prod.py` fails the
# boot on `SAFETY_CLASSIFIER_ENABLED=0`: a deployment that is quietly less safe
# than it looks is worse than one that will not start. Unlike those checks this
# one runs in **every** environment, because the misconfiguration it catches is a
# development-machine mistake that only shows up as a degradation storm.
#
# Deliberately narrow. It checks the *shape* of the id against the provider that
# will actually be selected — a slug has a vendor prefix, a Claude id does not —
# and nothing else. Whether the model exists is the provider's answer to give.
#
# The rule itself lives in `apps.common.model_ids` rather than here, because Day
# 15's `ai.beat_model` feature flag (§16.10's no-deploy rollback) has to apply the
# same rule when a model id is set at runtime — and a boot check and a runtime
# check that disagree about what a valid model id is would let the rollback lever
# set something the next boot refuses to start on.
def _selected_ai_provider() -> str:
    """The provider `ModelRouter.select_provider` will pick: first in order with a key."""
    credentials = {"anthropic": ANTHROPIC_API_KEY, "openrouter": OPENROUTER_API_KEY}
    for _name in AI_PROVIDER_ORDER:
        if credentials.get(_name):
            return str(_name)
    # No key configured for anything: nothing will be called, so there is no
    # pairing to be wrong. `select_provider` raises its own clear error later.
    return ""


def _check_model_pairing() -> None:
    from django.core.exceptions import ImproperlyConfigured

    provider = _selected_ai_provider()
    if not provider:
        return

    # `IMAGE_MODERATION_MODEL` is deliberately absent: it does not go through the
    # router. `media.moderation.ClaudeImageModerator` calls Anthropic directly, so
    # it is a Claude id whatever `AI_PROVIDER_ORDER` says, and checking it here
    # would refuse a perfectly correct configuration.
    models = {
        "AI_BEAT_MODEL": AI_BEAT_MODEL,
        "AI_SAFETY_MODEL": AI_SAFETY_MODEL,
        # Same trap, one app over: `AI_MEMORY_MODEL` rides the same provider order
        # and holds an Anthropic-flavoured default, so an OpenRouter deployment
        # that forgot it would consolidate nothing — silently, every night, with
        # only a warning in a worker log nobody reads (§11.4's model half degrades
        # rather than failing).
        "AI_MEMORY_MODEL": AI_MEMORY_MODEL,
    }
    for _setting, _model in models.items():
        problem = model_ids.pairing_error(
            setting=_setting,
            model=_model,
            provider=provider,
            order=list(AI_PROVIDER_ORDER),
        )
        if problem:
            raise ImproperlyConfigured(problem)


_check_model_pairing()


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
# Flash v2.5 by default, measured against a live key rather than chosen by name:
# it bills 0.5 credits/character where multilingual_v2 and v3 bill 1.0, so the
# same quota speaks twice as many lines, and it is the lowest-latency model
# ElevenLabs offers — which is the part of MEDIA_TURN_BUDGET_S a child spends
# waiting. Quality is not the axis a 30-word beat is decided on; running out of
# voice mid-story is.
ELEVENLABS_MODEL_ID = env("ELEVENLABS_MODEL_ID", default="eleven_flash_v2_5")
# Optional map from a companion's semantic voice key (§9.1, set from its
# archetype) to a real ElevenLabs voice id, as JSON:
#   ELEVENLABS_VOICE_MAP={"warm_low":"21m00Tcm...","bright":"AZnzlk1X..."}
# Unmapped keys fall back to ELEVENLABS_VOICE_ID, so one configured voice is
# enough to ship and per-archetype voices are a config change later.
ELEVENLABS_VOICE_MAP = env.json("ELEVENLABS_VOICE_MAP", default={})

# Voice providers to try when TTS_PROVIDER fails, in order — the same shape as
# AI_PROVIDER_ORDER above, and for a sharper reason. The illustration is
# non-essential to answering a beat, so an image outage is a picture-less but
# playable scene; the *voice* is what a pre-reader has instead of the words on
# screen, so a voice outage is a screen they cannot read and nothing to tap. It
# happened for real: ElevenLabs answered `quota_exceeded` and the child got 21
# seconds of untappable narration before MEDIA_TURN_BUDGET_S let them past.
#
# Defaulted to Google rather than left empty: it needs one key and nothing else,
# so the failover exists as soon as the key does. A named provider with no
# credentials is skipped rather than an error — a fallback nobody configured is
# not a misconfiguration, it is simply absent.
#
# `local` is deliberately *not* here and must not be added: the offline chime is
# two notes, not speech, so it would land the voice stage, hand the child their
# turn, and tell them nothing — a silent screen with a bell on it, and an outage
# no log would ever show as one.
TTS_FALLBACK_PROVIDERS = env.list("TTS_FALLBACK_PROVIDERS", default=["google"])

# Google Cloud Text-to-Speech — the implemented fallback voice. 1M free
# characters a month on the Neural2 tier, which is why it is the swappable
# option when ElevenLabs runs dry.
GOOGLE_TTS_API_KEY = env("GOOGLE_TTS_API_KEY", default="")
# Google *does* have a default voice for a language, unlike ElevenLabs, so a key
# alone is enough to speak. Naming one is still better: the language code is
# derived from the name, so the two can never disagree.
GOOGLE_TTS_VOICE = env("GOOGLE_TTS_VOICE", default="en-US-Neural2-F")
GOOGLE_TTS_LANGUAGE_CODE = env("GOOGLE_TTS_LANGUAGE_CODE", default="en-US")
# Same idea as ELEVENLABS_VOICE_MAP: semantic archetype key -> Google voice name.
GOOGLE_TTS_VOICE_MAP = env.json("GOOGLE_TTS_VOICE_MAP", default={})

# A generated asset that is wildly larger than a storybook illustration is a
# provider bug or a hostile response, not a picture. The scan rejects it before
# it reaches storage (§13.8).
MEDIA_MAX_BYTES = env.int("MEDIA_MAX_BYTES", default=12 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Analytics and unit economics (PRODUCT.md §5.6, §13.8, §19.9, §22.4)
# ---------------------------------------------------------------------------
# Where `manage.py drain_events` writes the warehouse export. A directory under
# the repo by default so the command works on a fresh checkout with no
# configuration; in production this is a mounted volume that something ships to
# object storage.
EVENT_EXPORT_DIR = env("EVENT_EXPORT_DIR", default=str(BASE_DIR / "exports"))

# §13.8: short retention on *raw child behavioural* events. Ninety days is long
# enough to compute a W4 retention cohort and a quarter of unit economics, and
# short enough that we are not sitting on two years of a four-year-old's taps.
# Nothing applies it on a timer — `drain_events --prune` is deliberate and manual,
# and it never deletes a row that has not reached the export.
EVENT_RETENTION_DAYS = env.int("EVENT_RETENTION_DAYS", default=90)

# The global inference-budget circuit breaker (§18.8, §19.9, `apps.analytics.budget`).
#
# **What it protects against is not one abusive caller** — the throttle scopes
# above do that — but the whole product spending more in an hour than it should,
# which is what a price change, a prompt regression or a retry storm looks like
# from here. Over the cap, new paid generations are refused and the child gets
# cached art and a pre-vetted warm beat instead of an error (§13.3).
#
# Defaulted to **0.00, meaning "do not enforce"**, and that is deliberate rather
# than lazy: a cap is a number somebody has to choose from their own cost/session
# figure (`manage.py metrics`), and a plausible-looking default would be a
# throttle on a real family's story chosen by nobody. `prod.py` refuses to boot
# without one, so the choice cannot be skipped where it matters — see DEPLOY.md
# and Docs/Runbooks/Launch.md for how to size it.
INFERENCE_BUDGET_USD_PER_WINDOW = env.float("INFERENCE_BUDGET_USD_PER_WINDOW", default=0.0)
# An hour. Long enough that a busy bedtime does not trip it, short enough that a
# trip clears itself the same evening rather than taking the product out for a day.
INFERENCE_BUDGET_WINDOW_S = env.int("INFERENCE_BUDGET_WINDOW_S", default=3600)

# ---------------------------------------------------------------------------
# Database dumps (PRODUCT.md §16.10, §12.8)
# ---------------------------------------------------------------------------
# Three numbers, and they exist because a *sentence in the privacy disclosure*
# depends on them. `apps.accounts.privacy` used to say "we do not keep backups
# yet, so today there is no copy anywhere for a deletion to miss" while a 205 KB
# dump of every family sat in the repository working tree (the audit's F-5). The
# disclosure now says what is true — dumps are taken, and held for this many days
# — and these are the values it states, read by `infra/backup/backup.sh` and
# checked by `manage.py launch_check`.
#
# Written down here rather than only in the shell script so that the claim and
# the enforcement are one number. A promise about retention that nothing reads is
# how the sentence became false in the first place.
BACKUP_DIR = env("BACKUP_DIR", default=str(REPO_ROOT / "backups"))
# Long enough to survive a corruption nobody noticed for a few days, short enough
# that a family who left is not still sitting in a dump a month later.
BACKUP_RETENTION_DAYS = env.int("BACKUP_RETENTION_DAYS", default=7)
# How stale the newest dump may be before the preflight says the cron is dead.
# Daily dumps plus a margin for a missed run and a timezone.
BACKUP_MAX_AGE_HOURS = env.int("BACKUP_MAX_AGE_HOURS", default=36)

# Deployment-level defaults for `apps.common.flags` (§16.10). A `FeatureFlag` row
# still wins over anything here — the point of the row is that it outruns a
# deploy — so this is the baseline a box starts from, not an override.
#   FEATURE_FLAGS={"media.illustrations":"off"}
FEATURE_FLAGS = env.json("FEATURE_FLAGS", default={})

# Per-generation prices, merged over `apps.analytics.pricing`'s built-in table.
# Shaped `{"llm": {"provider:model": [in_per_1m, out_per_1m]}, "image":
# {"provider": per_image}, "tts": {"provider": per_1k_chars}}`, all in USD.
#
# It exists because a vendor changing its price on a Tuesday should be a config
# edit rather than a deploy — and because an unpriced route is reported as
# unpriced rather than as free, so the dashboard tells you to come here instead
# of quietly reading 0.00 (§22.4).
AI_PRICE_TABLE = env.json("AI_PRICE_TABLE", default={})
