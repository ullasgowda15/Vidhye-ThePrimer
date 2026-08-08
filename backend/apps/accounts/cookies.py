"""The refresh cookie (PRODUCT.md §18.2, §14.5).

One module owns the cookie so its four attributes are written once. A cookie set
with `httpOnly` on login and without it on rotation is not a subtle bug — it is a
refresh token that becomes script-readable a quarter of an hour after sign-in,
and it is exactly the shape of mistake that spreading `set_cookie` calls across
three views produces.

Why a cookie at all: the child experience runs under a parent session and a
three-year-old cannot log in, so the session must survive a cold start (§14.5).
Why not `localStorage`: we control the backend, so there is no reason to pick the
option any script on the origin can read.

The security properties, each doing a specific job:

* **httpOnly** — an XSS on a page showing a child's memories cannot read a
  fourteen-day refresh token.
* **SameSite=Lax** — the CSRF control. `/auth/refresh` is unauthenticated by
  design (the cookie *is* the credential), so without this any page could POST
  to it from a signed-in parent's browser and rotate their session. Lax withholds
  the cookie from cross-site POSTs. It requires the API and the app to be
  same-site in production; see DEPLOY.md.
* **Secure** (outside dev) — the cookie never crosses plain HTTP.
* **Path=/api/v1/auth** — the browser attaches it to refresh and logout, and to
  nothing else. Every other endpoint authenticates with a bearer header, so no
  other endpoint should ever receive this.
"""

from typing import Literal, cast

from django.conf import settings
from rest_framework.response import Response
from rest_framework_simplejwt.settings import api_settings

#: `SameSite` is a closed set in the HTTP spec, and Django's signature says so.
#: The setting is a string, so it is narrowed here — once, at the boundary —
#: rather than at both call sites.
SameSite = Literal["Lax", "Strict", "None"]


def same_site() -> SameSite:
    return cast(SameSite, settings.REFRESH_COOKIE_SAMESITE)


def refresh_max_age() -> int:
    """Cookie lifetime, taken from the token's own lifetime.

    Derived rather than configured separately: a cookie that outlives its token
    means a client that believes it has a session and gets a 401, and a cookie
    that dies first means a parent signed out early for no reason.
    """
    return int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds())


def read(request: object) -> str | None:
    """The refresh token this request carries, or None."""
    cookies = getattr(request, "COOKIES", None) or {}
    return cookies.get(settings.REFRESH_COOKIE_NAME) or None


def attach(response: Response, token: str) -> Response:
    """Put `token` in the refresh cookie."""
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        token,
        max_age=refresh_max_age(),
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=same_site(),
        path=settings.REFRESH_COOKIE_PATH,
    )
    return response


def clear(response: Response) -> Response:
    """Delete the refresh cookie.

    The path must match the one it was set with or the browser deletes nothing and
    keeps sending the old cookie — a logout that appears to work and does not.
    """
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        samesite=same_site(),
    )
    return response


def move_to_cookie(response: Response) -> Response:
    """Take the refresh token out of a response body and into the cookie.

    The body is left carrying `access` only. This is the whole point of the
    change: a token the JavaScript never sees is a token an XSS cannot steal, so a
    view that returns one in its body has undone the work regardless of what it
    also set a cookie to.
    """
    data = getattr(response, "data", None)
    if isinstance(data, dict) and (token := data.pop("refresh", None)):
        attach(response, str(token))
    return response
