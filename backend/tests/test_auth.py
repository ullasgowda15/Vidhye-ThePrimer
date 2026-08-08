"""Auth flows (PRODUCT.md §7.1, §18.3, §18.8, §14.5).

The token lifecycle is SimpleJWT's, so these tests assert the *configuration* we
depend on rather than reimplementing its unit tests: that refresh rotates and
blacklists, that logout actually revokes, and that /me is scoped to the caller.

The cookie tests are a different kind. The refresh token moving into an
`httpOnly` cookie is what lets a child cold-start into their story without a login
form they cannot pass (§14.5), and the properties that make it safe to do that —
never in a response body, `httpOnly`, `SameSite=Lax`, path-scoped — are invisible
in normal use and silent when they break. So each one is asserted directly.
"""

import pytest
from django.conf import settings
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

COOKIE = settings.REFRESH_COOKIE_NAME


def refresh_cookie(response) -> str:
    """The refresh token this response set, failing loudly if it set none."""
    assert COOKIE in response.cookies, f"no {COOKIE} cookie on {response.request['PATH_INFO']}"
    return response.cookies[COOKIE].value


def test_signup_creates_a_parent_and_returns_an_access_token(api) -> None:
    response = api.post(
        SIGNUP_URL,
        {"email": "New@Example.com", "password": PASSWORD, "display_name": "Nia"},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["user"]["email"] == "new@example.com"
    assert response.data["access"]
    assert refresh_cookie(response)
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
    assert response.data["access"]
    assert refresh_cookie(response)


def test_login_rejects_a_bad_password(api, parent) -> None:
    response = api.post(LOGIN_URL, {"email": parent.email, "password": "wrong"}, format="json")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The refresh cookie (§18.2, §14.5)
# ---------------------------------------------------------------------------
def test_the_refresh_token_never_appears_in_a_response_body(api, parent) -> None:
    """The property everything else rests on.

    A cookie set correctly *and* a token echoed in the body is not a partial win:
    the client can store the echoed copy, and then an XSS has the session again.
    """
    signup = api.post(
        SIGNUP_URL, {"email": "body@example.com", "password": PASSWORD}, format="json"
    )
    login = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    rotated = api.post(REFRESH_URL)

    for response in (signup, login, rotated):
        assert "refresh" not in response.data, response.data


def test_the_refresh_cookie_is_httponly_samesite_and_path_scoped(api, parent) -> None:
    response = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    cookie = response.cookies[COOKIE]

    # httpOnly: an XSS on a page showing a child's memories cannot read a
    # fourteen-day refresh token.
    assert cookie["httponly"]
    # SameSite=Lax is the CSRF control on an endpoint that is unauthenticated by
    # design, not a hardening extra — see apps/accounts/cookies.py.
    assert cookie["samesite"] == "Lax"
    # Path-scoped, so the cookie reaches refresh and logout and nothing else.
    assert cookie["path"] == "/api/v1/auth"
    assert int(cookie["max-age"]) == int(
        settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()
    )


def test_refresh_reads_the_cookie_and_rotates_it(api, parent) -> None:
    login = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    original = refresh_cookie(login)

    # No body at all: the cookie the test client is now holding is the credential.
    rotated = api.post(REFRESH_URL)
    assert rotated.status_code == 200, rotated.data
    assert rotated.data["access"]
    # Rotation issues a *new* refresh token, and the cookie must carry it — a
    # client that kept the old one would present a blacklisted token next time.
    assert refresh_cookie(rotated) != original


def test_refresh_ignores_a_token_in_the_body(api, parent) -> None:
    """The old, script-readable path is closed rather than merely discouraged.

    Accepting a body as well would mean a client that still worked without the
    cookie — and therefore a client that never had to stop storing the token.
    """
    login = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    token = refresh_cookie(login)

    api.cookies.clear()
    assert api.post(REFRESH_URL, {"refresh": token}, format="json").status_code == 401


def test_refresh_without_a_cookie_is_401_not_400(api) -> None:
    """The bootstrap refresh on a first-ever visit lands here every time.

    It must read as "you have no session", the same as a dead token — a 400 would
    make a normal cold start look like a client bug.
    """
    assert api.post(REFRESH_URL).status_code == 401


def test_a_rotated_cookie_cannot_be_replayed(api, parent) -> None:
    login = api.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")
    stolen = refresh_cookie(login)

    assert api.post(REFRESH_URL).status_code == 200

    # A stolen copy is worth one use at most, because rotation blacklists the
    # token it was presented with.
    api.cookies[COOKIE] = stolen
    assert api.post(REFRESH_URL).status_code == 401


def test_logout_blacklists_the_session_and_deletes_the_cookie(auth, parent) -> None:
    auth.post(LOGIN_URL, {"email": parent.email, "password": PASSWORD}, format="json")

    response = auth.post(LOGOUT_URL)
    assert response.status_code == 204
    # Deleted with the same path it was set with, or the browser keeps sending it
    # and the logout only appears to have worked.
    assert response.cookies[COOKIE].value == ""
    assert response.cookies[COOKIE]["path"] == "/api/v1/auth"

    # The token itself is dead server-side too, so a copy taken beforehand is
    # useless.
    assert auth.post(REFRESH_URL).status_code == 401


def test_logout_requires_authentication(api) -> None:
    assert api.post(LOGOUT_URL).status_code == 401


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
