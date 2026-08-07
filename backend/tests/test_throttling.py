"""Rate limits (PRODUCT.md §18.2, §18.8).

Rates are overridden per-test rather than exercised at their real values: the
point is that the limit is wired up and answers 429, not that we can send a
thousand requests in a unit test.
"""

import pytest
from rest_framework.throttling import SimpleRateThrottle

from tests.conftest import CHILDREN_URL, LOGIN_URL, PASSWORD, SIGNUP_URL

pytestmark = pytest.mark.django_db


@pytest.fixture
def tight_rates(monkeypatch):
    """Shrink every rate for one test.

    `SimpleRateThrottle.THROTTLE_RATES` is bound to the settings dict at import
    time, so overriding `REST_FRAMEWORK` in Django settings does *not* reach it.
    Patching the class attribute is what actually changes the limit.
    """
    rates = {"anon": "3/min", "user": "3/min", "auth": "2/min"}
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


def test_the_health_probe_is_never_throttled(api, tight_rates) -> None:
    # A load balancer polls this constantly from one address; throttling it would
    # take the service out of rotation.
    statuses = [api.get("/api/health/").status_code for _ in range(6)]
    assert statuses == [200] * 6
