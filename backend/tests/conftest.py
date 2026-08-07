"""Shared API-test fixtures.

The throttle counters live in Django's cache, which is process-global and
survives between tests. Left alone, test ordering would decide whether a test
sees a 201 or a 429 — so the cache is cleared around every test, and the tests
that care about throttling set their own rates explicitly.
"""

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord

User = get_user_model()

CONSENT_URL = "/api/v1/consent"
CHILDREN_URL = "/api/v1/children"
SIGNUP_URL = "/api/v1/auth/signup"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/me"

# Strong enough for Django's validators; obvious enough that nobody mistakes it
# for a credential.
PASSWORD = "storybook-primer-42"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def parent(db) -> Any:
    return User.objects.create_user(
        email="priya@example.com", password=PASSWORD, display_name="Priya"
    )


@pytest.fixture
def other_parent(db) -> Any:
    return User.objects.create_user(email="sam@example.com", password=PASSWORD)


@pytest.fixture
def auth(api, parent) -> APIClient:
    """An API client authenticated as `parent` with a real bearer token."""
    response = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    assert response.status_code == 200, response.data
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return api


@pytest.fixture
def consent(parent) -> ConsentRecord:
    return ConsentRecord.objects.create(
        parent=parent,
        consent_type="data_collection_v1",
        method="checkbox_attestation",
    )


@pytest.fixture
def child_payload() -> dict[str, Any]:
    return {
        "first_name": "Aarav",
        "birthdate": str(datetime.date(datetime.date.today().year - 5, 5, 4)),
        "interests": ["dinosaurs", "the sea"],
        "fears": ["the dark"],
        "avoid_topics": ["hospitals"],
        "intensity_ceiling": 1,
        "session_cap_min": 20,
    }
