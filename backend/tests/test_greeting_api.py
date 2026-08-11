"""The arrival endpoint (PRODUCT.md §4.5, §18.3, §13.7).

End-to-end through DRF: consent, ownership, and the body the returning-greeting
scene consumes. The greeting's *content* is tested where it is decided
(`apps.companion.tests.test_greeting`); what is asserted here is the API contract
around it — including that memory retrieval is behind the same ownership check as
everything else, which is the one endpoint in the product where a permissions slip
would hand one family another family's child.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.companion import greeting as arrival
from apps.companion.service import create_companion
from apps.memory.models import Memory, MemoryType
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db


def greeting_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/greeting"


@pytest.fixture
def rina(parent, consent) -> Child:
    child = Child.objects.create(
        parent=parent,
        first_name="Rina",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["dragons"],
    )
    create_companion(
        child=child,
        archetype="dragon",
        name="Sparkle",
        palette="ember",
        personality_preset="warm_guide",
    )
    return child


def _remember(child: Child, content: str) -> Memory:
    from apps.media.adapters import embedding as embedding_adapter

    return Memory.objects.create(
        child=child,
        type=MemoryType.EVENT,
        content=content,
        salience=0.85,
        valence=0.7,
        embedding=embedding_adapter.embed(content),
    )


def test_a_returning_child_is_greeted_by_name_about_something_they_did(auth, rina, fake_ai) -> None:
    _remember(rina, "Rina counted three stepping stones across the stream.")

    response = auth.get(greeting_url(rina.id))

    assert response.status_code == 200, response.data
    body = response.data
    assert body["kind"] == arrival.RETURNING
    assert "Rina" in body["text"]
    assert "stepping stones" in body["text"]
    assert body["companion_name"] == "Sparkle"
    # The memory the greeting refers to, sent so the scene can show what is being
    # remembered and so a parent can check the claim (§12.6).
    assert "stepping stones" in body["remembered"]


def test_a_brand_new_child_gets_the_first_meeting(auth, rina, fake_ai) -> None:
    response = auth.get(greeting_url(rina.id))

    assert response.status_code == 200
    assert response.data["kind"] == arrival.FIRST_MEETING
    assert "Rina" in response.data["text"]
    assert fake_ai.greeting_calls == 0


def test_another_parent_cannot_read_this_childs_greeting(api, other_parent, rina) -> None:
    """The greeting *is* memory, rendered. It is behind the same gate (§11.10)."""
    from tests.conftest import LOGIN_URL, PASSWORD

    token = api.post(
        LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    assert api.get(greeting_url(rina.id)).status_code == 403


def test_it_needs_a_signed_in_parent(api, rina) -> None:
    assert api.get(greeting_url(rina.id)).status_code == 401


def test_it_needs_consent(auth, parent, fake_ai) -> None:
    """No consent record, no child data — including the memory of a child who
    somehow already has one (§13.7).
    """
    child = Child.objects.create(
        parent=parent,
        first_name="Ada",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )
    create_companion(
        child=child,
        archetype="fox",
        name="Rusty",
        palette="moss",
        personality_preset="calm_anchor",
    )

    assert auth.get(greeting_url(child.id)).status_code == 403


def test_an_unknown_child_is_a_404(auth) -> None:
    assert auth.get(greeting_url("11111111-1111-1111-1111-111111111111")).status_code == 404
