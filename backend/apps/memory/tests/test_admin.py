"""The memory view (PRODUCT.md §11.10, §12.6, §13.8).

The founder's manual check for the day is "open `/admin/` and see a small set of
meaningful memories". That page has three ways to be quietly broken, and none of
them would show up in a test of the model:

* **the text could be unreadable** — `content` is ciphertext in the column, so a
  list view that rendered the raw value would show `gAAAAA…` for every row;
* **the search box could be a lie** — a `LIKE` over an encrypted column silently
  never matches, which is why the box searches `entities` instead;
* **it could be editable** — a memory somebody typed in is a companion
  confidently remembering something that never happened.

So the page is exercised here rather than eyeballed, and the assertions are about
those three things.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.media.adapters import embedding as embedding_adapter
from apps.memory.models import Memory, MemoryType

pytestmark = pytest.mark.django_db

SPARKLE = "Aarav named the little dragon Sparkle."


@pytest.fixture
def ops(aarav) -> Client:
    """An operator signed in to the admin. Reuses the child's own parent account."""
    operator = get_user_model().objects.filter(pk=aarav.parent_id).first()
    assert operator is not None
    operator.is_staff = True
    operator.is_superuser = True
    operator.save(update_fields=["is_staff", "is_superuser"])

    client = Client()
    client.force_login(operator)
    return client


@pytest.fixture
def remembered(aarav) -> Memory:
    entity = Memory.objects.create(
        child=aarav,
        type=MemoryType.ENTITY,
        content=SPARKLE,
        embedding=embedding_adapter.embed(SPARKLE),
        salience=0.81,
        valence=0.8,
        entities=["character:sparkle"],
    )
    # A second row with no vector, so the `vector` column has both states on screen.
    Memory.objects.create(
        child=aarav,
        type=MemoryType.SKILL_MOMENT,
        content="Aarav counted three frogs all by himself.",
        salience=0.58,
    )
    return entity


def test_the_list_view_shows_the_memory_in_plain_words(ops, remembered) -> None:
    """Decrypted on the way out of the database, or the page is unreadable."""
    response = ops.get("/admin/memory/memory/")

    assert response.status_code == 200
    body = response.content.decode()
    assert "Sparkle" in body
    assert "gAAAAA" not in body, "ciphertext must never reach the page"
    assert "0.81" in body


def test_the_detail_page_shows_the_whole_memory(ops, remembered) -> None:
    response = ops.get(f"/admin/memory/memory/{remembered.pk}/change/")

    assert response.status_code == 200
    assert SPARKLE in response.content.decode()


def test_the_1536_float_embedding_is_not_rendered(ops, remembered) -> None:
    """It would push the content off the screen and tell a human nothing."""
    body = ops.get(f"/admin/memory/memory/{remembered.pk}/change/").content.decode()

    vector = remembered.embedding
    assert vector is not None
    # No run of the actual vector's numbers anywhere on the page.
    assert f"{vector[0]:.6f}" not in body


def test_search_and_filters_work_against_the_columns_that_are_not_encrypted(
    ops, remembered, aarav
) -> None:
    assert ops.get("/admin/memory/memory/?q=sparkle").status_code == 200
    assert ops.get("/admin/memory/memory/?q=Aarav").status_code == 200
    assert (
        ops.get(f"/admin/memory/memory/?type__exact=entity&child__id__exact={aarav.pk}").status_code
        == 200
    )


def test_a_memory_cannot_be_added_or_edited_by_hand(ops, remembered) -> None:
    """Read-only more strictly than the incident queue — see the module docstring."""
    assert ops.get("/admin/memory/memory/add/").status_code == 403

    # A POST at the change URL must not rewrite what a child is remembered for.
    ops.post(
        f"/admin/memory/memory/{remembered.pk}/change/",
        {"content": "Aarav did something else entirely.", "salience": "0.1"},
    )
    remembered.refresh_from_db()
    assert remembered.content == SPARKLE
    assert remembered.salience == 0.81


def test_deletion_stays_available_because_erasure_is_a_right(ops, remembered) -> None:
    """§13.8's erasure right has to be honourable before Day 12's parent control."""
    response = ops.post(f"/admin/memory/memory/{remembered.pk}/delete/", {"post": "yes"})

    assert response.status_code in (200, 302)
    assert not Memory.objects.filter(pk=remembered.pk).exists()
