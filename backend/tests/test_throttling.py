"""Rate limits (PRODUCT.md §18.2, §18.8).

Rates are overridden per-test rather than exercised at their real values: the
point is that the limit is wired up and answers 429, not that we can send a
thousand requests in a unit test.
"""

import pytest
from rest_framework.throttling import SimpleRateThrottle

from tests.conftest import CHILDREN_URL, LOGIN_URL, PASSWORD, REFRESH_URL, SIGNUP_URL

pytestmark = pytest.mark.django_db


@pytest.fixture
def tight_rates(monkeypatch):
    """Shrink every rate for one test.

    `SimpleRateThrottle.THROTTLE_RATES` is bound to the settings dict at import
    time, so overriding `REST_FRAMEWORK` in Django settings does *not* reach it.
    Patching the class attribute is what actually changes the limit.
    """
    rates = {"anon": "3/min", "user": "3/min", "auth": "2/min", "session_refresh": "100/min"}
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)
    return rates


def test_credential_endpoints_throttle_by_ip(api, tight_rates) -> None:
    payload = {"email": "nobody@example.com", "password": "wrong-password"}

    statuses = [api.post(LOGIN_URL, payload, format="json").status_code for _ in range(3)]

    # Two attempts allowed, the third refused — a brute-forcer gets two guesses
    # a minute from one address.
    assert statuses[:2] == [401, 401]
    assert statuses[2] == 429


def test_signup_shares_the_tight_auth_scope(api, tight_rates) -> None:
    for i in range(2):
        api.post(SIGNUP_URL, {"email": f"a{i}@example.com", "password": PASSWORD}, format="json")

    third = api.post(SIGNUP_URL, {"email": "a3@example.com", "password": PASSWORD}, format="json")
    assert third.status_code == 429


def test_authenticated_endpoints_throttle_per_parent(auth, tight_rates) -> None:
    statuses = [auth.get(CHILDREN_URL).status_code for _ in range(4)]

    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429


def test_restoring_a_session_does_not_spend_the_login_budget(api, tight_rates) -> None:
    """The regression that locked a parent out of their own account.

    `POST /auth/refresh` is called once per page load, automatically, by every
    client that has ever signed in. `POST /auth/login` is called by a human, twice
    a month. While both counted against `auth`, a morning of reloads spent the
    login allowance — and because a refused refresh reads as "your session is
    over", the client signed the parent out and sent them to the login form, which
    then answered 429 to a completely correct password for the rest of the hour.

    `auth` here is 2/min. If refresh still touched it, the login below would be
    refused rather than merely wrong.
    """
    for _ in range(6):
        # 401 every time: there is no refresh cookie. Being *answered* is the
        # point — these are the requests that used to be counted as login attempts.
        assert api.post(REFRESH_URL, {}, format="json").status_code == 401

    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    refused = api.post(LOGIN_URL, payload, format="json")
    assert refused.status_code == 401, "login's own budget was spent by page loads"


def test_restoring_a_session_is_still_bounded(api, monkeypatch) -> None:
    """Its own scope, not no scope — a runaway client still meets a ceiling."""
    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {"anon": "100/min", "user": "100/min", "auth": "100/min", "session_refresh": "2/min"},
    )

    statuses = [api.post(REFRESH_URL, {}, format="json").status_code for _ in range(3)]

    assert statuses == [401, 401, 429]


def test_the_health_probe_is_never_throttled(api, tight_rates) -> None:
    # A load balancer polls this constantly from one address; throttling it would
    # take the service out of rotation.
    statuses = [api.get("/api/health/").status_code for _ in range(6)]
    assert statuses == [200] * 6


# ---------------------------------------------------------------------------
# Reading a companion must not spend the budget for creating one (§16.6)
# ---------------------------------------------------------------------------
@pytest.fixture
def split_companion_rates(monkeypatch):
    """One create, three reads. Deliberately different, so a shared scope fails."""
    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {"anon": "100/min", "user": "100/min", "companion": "1/min", "companion_read": "3/min"},
    )


@pytest.fixture
def companion_urls(auth, parent, consent):
    import datetime

    from apps.profiles.models import Child

    child = Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )
    return auth, f"/api/v1/children/{child.id}/companion"


def test_polling_a_companion_does_not_spend_the_create_budget(
    companion_urls, split_companion_rates, settings
) -> None:
    """The regression that mattered.

    The greeting scene polls this GET every couple of seconds while the art
    renders. When both methods shared one scope at the create rate, twenty polls
    spent the hour in forty seconds — and because the poll is how the child's
    screen learns their companion is ready, the companion never woke up.

    The create rate here is 1/min. If the GET touched it, the POST below would be
    refused.
    """
    auth, url = companion_urls
    settings.CELERY_TASK_ALWAYS_EAGER = False

    # Three reads: allowed by `companion_read`, and 404 because no companion
    # exists yet — which is exactly the state the greeting scene polls in.
    assert [auth.get(url).status_code for _ in range(3)] == [404, 404, 404]

    created = auth.post(
        url,
        {
            "archetype": "dragon",
            "name": "Pip",
            "palette": "ember",
            "personality_preset": "brave_spark",
        },
        format="json",
    )
    assert created.status_code == 201, created.data


def test_reading_a_companion_is_still_bounded(companion_urls, split_companion_rates) -> None:
    """Its own scope, not no scope. A runaway client still meets a ceiling."""
    auth, url = companion_urls

    statuses = [auth.get(url).status_code for _ in range(4)]

    assert statuses[:3] == [404, 404, 404]
    assert statuses[3] == 429


def test_creating_a_companion_is_capped_independently(
    companion_urls, split_companion_rates
) -> None:
    auth, url = companion_urls
    payload = {
        "archetype": "dragon",
        "name": "Pip",
        "palette": "ember",
        "personality_preset": "brave_spark",
    }

    assert auth.post(url, payload, format="json").status_code == 201
    # The second attempt is refused by the *create* scope, not by the 409 that
    # would otherwise answer it — the money-spending path stays tightly capped.
    assert auth.post(url, payload, format="json").status_code == 429
