"""Dev CORS is scoped (PRODUCT.md §13.1).

`CORS_ALLOW_ALL_ORIGINS = True` in dev combines with `CORS_ALLOW_CREDENTIALS =
True` in base to reflect *any* origin back with
`access-control-allow-credentials: true`. That was cosmetic while the session was
a bearer token held in memory and nothing was attached automatically.

It stopped being cosmetic when a cookie started carrying the session: a page on
any origin a developer visits could make credentialed requests to their local API
and read the responses. `SameSite=Lax` is the primary control; this is its
defence-in-depth pair, and a test is the only thing that keeps a permissive
one-liner from coming back "just for today".
"""

import pytest

pytestmark = pytest.mark.django_db

PREFLIGHT_HEADERS = {
    "HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST",
    "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "content-type",
}
REFRESH_URL = "/api/v1/auth/refresh"
ALLOW_ORIGIN = "access-control-allow-origin"


def test_the_vite_dev_origin_is_allowed_with_credentials(api) -> None:
    response = api.options(REFRESH_URL, HTTP_ORIGIN="http://localhost:5173", **PREFLIGHT_HEADERS)

    assert response.headers[ALLOW_ORIGIN] == "http://localhost:5173"
    # Credentials must be permitted or the refresh cookie never reaches the API
    # from the dev server, and session restore silently stops working.
    assert response.headers["access-control-allow-credentials"] == "true"


def test_any_localhost_port_is_allowed(api) -> None:
    """Vite picks a new port when 5173 is taken, and a second app on 5174 is
    normal. The regex covers that without opening the door to arbitrary hosts."""
    response = api.options(REFRESH_URL, HTTP_ORIGIN="http://127.0.0.1:4173", **PREFLIGHT_HEADERS)
    assert response.headers[ALLOW_ORIGIN] == "http://127.0.0.1:4173"


def test_a_hostile_origin_is_not_reflected(api) -> None:
    response = api.options(REFRESH_URL, HTTP_ORIGIN="https://evil.example", **PREFLIGHT_HEADERS)

    # The absence of the header is the whole result: without it the browser
    # refuses to hand the response to the calling page.
    assert ALLOW_ORIGIN not in response.headers


def test_an_origin_that_merely_contains_localhost_is_not_reflected(api) -> None:
    """The regexes are anchored. `http://localhost.evil.example` is a different
    host that a substring match would have welcomed."""
    for origin in (
        "http://localhost.evil.example",
        "https://localhost:5173",
        "http://notlocalhost:5173",
    ):
        response = api.options(REFRESH_URL, HTTP_ORIGIN=origin, **PREFLIGHT_HEADERS)
        assert ALLOW_ORIGIN not in response.headers, origin


def test_dev_settings_do_not_allow_all_origins() -> None:
    from config.settings import dev

    assert not getattr(dev, "CORS_ALLOW_ALL_ORIGINS", False)
