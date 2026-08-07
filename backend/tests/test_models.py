"""Smoke tests for the Day-1 data model.

These assert the relationships and defaults the rest of the product is built
on — email login, cascade from parent to child, the 1:1 companion.
"""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.companion.models import Companion
from apps.profiles.models import Child
from apps.story.models import PlaySession

User = get_user_model()


@pytest.fixture
def parent(db):
    return User.objects.create_user(email="priya@example.com", password="pw", display_name="Priya")


@pytest.fixture
def child(parent):
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(2021, 5, 4),
        interests=["frogs", "space"],
    )


def test_user_uses_email_as_login_identifier(parent) -> None:
    assert User.USERNAME_FIELD == "email"
    assert parent.check_password("pw")
    assert str(parent) == "priya@example.com"


def test_superuser_gets_staff_and_superuser_flags(db) -> None:
    admin = User.objects.create_superuser(email="ops@example.com", password="pw")
    assert admin.is_staff and admin.is_superuser


def test_email_is_unique(parent, db) -> None:
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="priya@example.com", password="pw2")


def test_child_defaults_are_safety_conservative(child) -> None:
    assert child.pre_reader is True
    assert child.intensity_ceiling == 2
    assert child.session_cap_min == 15
    assert child.avoid_topics == []
    assert child.parent.children.count() == 1


def test_companion_is_one_per_child(child) -> None:
    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    assert child.companion.name == "Pip"

    with pytest.raises(IntegrityError):
        Companion.objects.create(child=child, archetype="owl", name="Hoot")


def test_play_session_links_to_child(child) -> None:
    session = PlaySession.objects.create(child=child, device="tablet")
    assert session.ended_at is None
    assert child.sessions.count() == 1


def test_deleting_parent_removes_child_data(parent, child) -> None:
    # One delete satisfies "delete my child's data" (COPPA/GDPR, §17.5).
    parent.delete()
    assert Child.objects.count() == 0
