"""Fixtures for the reading tests.

Local rather than borrowed from `backend/tests/conftest.py`: that file serves the
API suite and builds its parent by signing up through the endpoints, which is the
right shape there and several requests of ceremony here. These tests need one
child, their parent authenticated, and a few bytes that look like an upload.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord


@pytest.fixture
def auth_client(child: Any) -> APIClient:
    """An API client acting as the parent of `child`, past the consent gate.

    `force_authenticate` rather than a login round trip: what these tests are
    about is the reading endpoint, and a token exchange in the way of that would
    only ever fail for reasons the auth suite already covers.
    """
    ConsentRecord.objects.create(
        parent=child.parent,
        consent_type="data_collection_v1",
        method="checkbox_attestation",
    )
    client = APIClient()
    client.force_authenticate(user=child.parent)
    return client


@pytest.fixture
def audio() -> SimpleUploadedFile:
    """Something the size and shape of a recorded sentence.

    Deliberately not a real WebM. Nothing in Phase 1 decodes it — the bytes are
    either written to the corpus untouched or dropped — so a fixture that made
    them valid audio would be asserting a property no code has.
    """
    return SimpleUploadedFile(
        "reading.webm", b"\x1a\x45\xdf\xa3" + b"\0" * 2048, content_type="audio/webm"
    )
