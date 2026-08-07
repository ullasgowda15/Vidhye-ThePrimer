"""Fixtures shared by every test in the backend.

Two things are enforced here for the whole suite rather than remembered per
test file:

* **No test ever writes into the repository.** The media backend is pointed at
  a per-test temporary directory, so a run that generates a hundred
  illustrations leaves nothing behind.
* **No test ever calls a paid provider.** The image and voice adapters are
  pinned to their offline `local` implementations. CI has no keys, but a
  developer's shell does — and a suite that quietly spends money on someone's
  machine and not in CI is the worst kind of test.
"""

import datetime
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture(autouse=True)
def no_live_providers(settings: Any) -> None:
    """Blank every credential for the duration of a test.

    CI has no keys, but a developer's `.env` does. Without this, running the
    suite locally would make real calls to Anthropic and to an image provider —
    money spent, and worse, tests that pass on one machine and fail on another.
    Tests that need a provider set their own key explicitly.
    """
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = ""
    settings.CLOUDFLARE_ACCOUNT_ID = ""
    settings.CLOUDFLARE_API_TOKEN = ""
    settings.ELEVENLABS_API_KEY = ""
    settings.ELEVENLABS_VOICE_ID = ""
    settings.ELEVENLABS_VOICE_MAP = {}


@pytest.fixture(autouse=True)
def offline_media(tmp_path: Path, settings: Any) -> Path:
    """Local storage, local providers, for every test."""
    settings.MEDIA_ROOT = str(tmp_path)
    settings.STORAGES = {
        **settings.STORAGES,
        "media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path), "base_url": "/media/"},
        },
    }
    settings.MEDIA_CDN_BASE_URL = ""
    settings.IMAGE_PROVIDER = "local"
    settings.TTS_PROVIDER = "local"
    return tmp_path


@pytest.fixture
def child(db: Any) -> Any:
    """A child with a parent, for tests that need one and nothing else.

    Deliberately independent of the API fixtures in `tests/conftest.py`: unit
    tests for the media and companion services should not have to sign anybody
    in to render a picture.
    """
    from apps.profiles.models import Child

    parent = User.objects.create_user(email="unit-parent@example.com", password="primer-42-test")
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )
