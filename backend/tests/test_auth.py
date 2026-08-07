"""Auth flows (PRODUCT.md §7.1, §18.3, §18.8).

The token lifecycle is SimpleJWT's, so these tests assert the *configuration* we
depend on rather than reimplementing its unit tests: that refresh rotates and
blacklists, that logout actually revokes, and that /me is scoped to the caller.
"""

import pytest
from django.contrib.auth import get_user_model

from tests.conftest import (
    LOGIN_URL,
    LOGOUT_URL,
    ME_URL,
    PASSWORD,
    REFRESH_URL,
    SIGNUP_URL,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_signup_creates_a_parent_and_returns_tokens(api) -> None:
    response = api.post(
        SIGNUP_URL,
        {"email": "New@Example.com", "password": PASSWORD, "display_name": "Nia"},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["user"]["email"] == "new@example.com"
    assert response.data["access"] and response.data["refresh"]
    assert User.objects.filter(email="new@example.com").exists()


def test_signup_never_echoes_the_password(api) -> None:
    response = api.post(SIGNUP_URL, {"email": "a@example.com", "password": PASSWORD}, format="json")
    assert "password" not in response.data
    assert "password" not in response.data["user"]


def test_signup_rejects_a_weak_password(api) -> None:
    response = api.post(SIGNUP_URL, {"email": "a@example.com", "password": "12345"}, format="json")

    assert response.status_code == 400
    assert "password" in response.data


def test_signup_rejects_a_duplicate_email_case_insensitively(api, parent) -> None:
    response = api.post(
        SIGNUP_URL, {"email": parent.email.upper(), "password": PASSWORD}, format="json"
    )

    assert response.status_code == 400
    assert "email" in response.data


def test_signup_logs_a_verification_link(api, caplog) -> None:
    with caplog.at_level("INFO", logger="apps.accounts.verification"):
        api.post(SIGNUP_URL, {"email": "a@example.com", "password": PASSWORD}, format="json")

    assert "/parent/verify-email?uid=" in caplog.text


def test_login_authenticates_with_email(api, parent) -> None:
    response = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")

    assert response.status_code == 200
    assert response.data["access"] and response.data["refresh"]


def test_login_rejects_a_bad_password(api, parent) -> None:
    response = api.post(LOGIN_URL, {"email": parent.email, "password": "wrong"}, format="json")
    assert response.status_code == 401


def test_refresh_rotates_and_blacklists_the_old_token(api, parent) -> None:
    tokens = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json").data

    rotated = api.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert rotated.status_code == 200
    assert rotated.data["access"]
    # Rotation issues a *new* refresh token; the client must replace both halves.
    assert rotated.data["refresh"] != tokens["refresh"]

    # The presented token is dead the moment it is exchanged, so a stolen copy
    # is worth one use at most.
    replay = api.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401


def test_logout_invalidates_the_refresh_token(auth, parent) -> None:
    tokens = auth.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json").data

    assert auth.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 204
    assert auth.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_logout_requires_authentication(api) -> None:
    assert api.post(LOGOUT_URL, {"refresh": "nonsense"}, format="json").status_code == 401


def test_me_returns_the_parent_and_their_children(auth, parent, consent, child_payload) -> None:
    auth.post("/api/v1/children", child_payload, format="json")

    response = auth.get(ME_URL)

    assert response.status_code == 200
    assert response.data["email"] == parent.email
    assert [c["first_name"] for c in response.data["children"]] == ["Aarav"]


def test_me_requires_authentication(api) -> None:
    assert api.get(ME_URL).status_code == 401


def test_me_never_leaks_permission_flags(auth) -> None:
    response = auth.get(ME_URL)
    assert not {"is_staff", "is_superuser", "password"} & set(response.data)
