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
    """
    return _client(
        str(settings.GOOGLE_CLOUD_PROJECT),
        str(settings.GOOGLE_CLOUD_LOCATION),
        str(settings.GOOGLE_APPLICATION_CREDENTIALS),
    )


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


def forget_client() -> None:
    """Drop the cached client. For tests and for a credential rotation."""
    _client.cache_clear()
