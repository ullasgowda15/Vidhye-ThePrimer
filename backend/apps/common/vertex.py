"""The Vertex AI client, in one place (PRODUCT.md §16.1, §13.8).

Two very different callers need the same client — `story.ai.router.GoogleProvider`
writes beats, grades them and consolidates memory with it, and
`media.moderation.GeminiImageModerator` looks at generated pictures with it — and
they must agree about three things or the deployment is subtly wrong in a way
neither of them can see:

* **What "configured" means.** `is_configured()` is what `select_provider` and
  `select_moderator` both key off, so a half-set deployment falls *through* to the
  next provider rather than raising in the middle of a child's beat.
* **How the credential is obtained.** A service account, read from the JSON file
  `GOOGLE_APPLICATION_CREDENTIALS` names — never an API key. That is what makes
  the call bill to the GCP project (the $300 credit) and run under the GCP
  enterprise terms that exclude our inputs from training (§13.8, `Docs/Hosting.md
  §3`); an AI Studio key is a different product with different terms.
* **That the credential is passed explicitly.** The SDK will happily discover
  Application Default Credentials from the ambient environment, which sounds
  convenient and means a box with a stale gcloud login sends a child's story to
  somebody else's project. The path in the setting is the only credential this
  module will use.

**Deliberately not network-aware.** `is_configured()` reads three strings and
stops: it must not open a socket or stat a file, because it is called on the
selection path in front of every generation, and a predicate that can hang is a
predicate that hangs a four-year-old's story. Whether the file is really there and
really works is a *boot* question, and `config.settings.prod` asks it there.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

#: The settings a Vertex call cannot be made without, in the order a person would
#: set them. Named once so the boot guard, the preflight and the provider all
#: report the same list.
REQUIRED_SETTINGS = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

#: The single OAuth scope a service account needs to call Vertex AI. Narrower
#: scopes exist for other Google APIs; this is the one Vertex publishes.
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def missing_for(project: str, location: str, credentials: str) -> list[str]:
    """Which of `REQUIRED_SETTINGS` are unset, given these three values.

    Takes the values rather than reading them so `config.settings.prod` can ask
    the same question about its own module globals, at a moment when there is no
    configured Django to read them from.
    """
    return [
        name
        for name, value in zip(REQUIRED_SETTINGS, (project, location, credentials), strict=True)
        if not str(value or "").strip()
    ]


def missing_settings() -> list[str]:
    """Which of `REQUIRED_SETTINGS` this deployment has not set."""
    return missing_for(
        str(getattr(settings, "GOOGLE_CLOUD_PROJECT", "") or ""),
        str(getattr(settings, "GOOGLE_CLOUD_LOCATION", "") or ""),
        str(getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", "") or ""),
    )


def is_configured() -> bool:
    """True when a Vertex call can be attempted. Never raises, never blocks."""
    return not missing_settings()


def client() -> Any:
    """A `genai.Client` in Vertex mode, authenticated by the service account.

    Cached on the three settings that define it, which is worth a sentence: the
    credential object holds the OAuth token it exchanged the service-account key
    for, so building a fresh client per call would add a token round-trip to every
    beat, every safety grade and every image. Keyed on the values rather than
    memoised outright so a test — or a settings reload — gets a client that
    matches the configuration it just set.

    **The cached client is checked for a closed transport before it is handed
    out**, because the cache outlives the connection pool inside it. A `genai.
    Client` owns an httpx pool; once that pool is closed, every call made through
    it raises `Cannot send a request, as the client has been closed` *locally and
    instantly* — no socket, no retry, no vendor involved. `lru_cache` then hands
    the same dead object to the next caller forever, so a pool that closes once
    takes every generation in the process down with it until a restart.

    That is not hypothetical: it is what produced the adventure whose title
    promised a cat and whose story was about a snail. `build_plan` and beat one
    both failed 158ms apart — far too fast to have left the machine — and fell
    back independently, one to a templated title built from the child's interest
    and the other to a pre-vetted beat from the safety pool. Two unrelated
    emergency paths, one dead socket, and a child who was promised something the
    story never contained.

    Rebuilding costs one token exchange and only happens when the alternative is a
    guaranteed failure, so the trade is not close.
    """
    key = (
        str(settings.GOOGLE_CLOUD_PROJECT),
        str(settings.GOOGLE_CLOUD_LOCATION),
        str(settings.GOOGLE_APPLICATION_CREDENTIALS),
    )
    candidate = _client(*key)
    if _transport_closed(candidate):
        # Whole-cache clear rather than surgery on one entry: `lru_cache` exposes
        # no single-key eviction, the cache holds at most four clients, and a
        # sibling rebuilding costs one token exchange it would have paid anyway.
        logger.warning("vertex.client_rebuilt reason=closed_transport project=%s", key[0])
        _client.cache_clear()
        candidate = _client(*key)
    return candidate


def _transport_closed(candidate: Any) -> bool:
    """True when this client's **sync** httpx pool has been closed.

    Only the sync pool, because only the sync API is called here — `router`,
    `moderation` and the embedding adapter all use `models.generate_content` /
    `models.embed_content` and none of them touches `.aio`. Checking the async
    pool as well would risk rebuilding on every call in a process that had merely
    finished with an event loop, which is a token round-trip per beat to fix a
    problem nobody has.

    Reaching into `_api_client` is reaching into the SDK's privates, so every step
    is guarded and *anything unexpected reads as healthy*. A client that is fine
    and gets rebuilt costs a token exchange; a broken predicate that raises here
    would take down the call it was added to protect.
    """
    try:
        transport = getattr(getattr(candidate, "_api_client", None), "_httpx_client", None)
        return bool(transport is not None and getattr(transport, "is_closed", False))
    except Exception:  # pragma: no cover - defensive, see the docstring
        logger.exception("vertex.health_check_failed")
        return False


@lru_cache(maxsize=4)
def _client(project: str, location: str, credentials_path: str) -> Any:
    from google import genai
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=list(SCOPES)
    )
    # `vertexai=True` is the whole point: the same SDK fronts both the AI Studio
    # API (an API key, consumer terms) and Vertex (a service account, the GCP
    # project's billing and terms). §13.8 is a statement about the second one.
    return genai.Client(vertexai=True, project=project, location=location, credentials=credentials)
