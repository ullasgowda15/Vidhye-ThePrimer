"""Throttle scopes (PRODUCT.md §18.2, §18.8).

The default anon/user rates protect the API as a whole. Credential endpoints
get their own, much tighter scope: signup/login/refresh are the endpoints worth
brute-forcing, and they are also the ones a legitimate parent hits a handful of
times a week, so a low ceiling costs nothing real.
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthAnonThrottle(AnonRateThrottle):
    """Per-IP limit on the credential endpoints (where the caller has no identity yet)."""

    scope = "auth"


class AuthUserThrottle(UserRateThrottle):
    """Per-parent limit for credential endpoints reached while already signed in."""

    scope = "auth"
