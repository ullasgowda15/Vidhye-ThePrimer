"""Throttle scopes (PRODUCT.md §18.2, §18.8).

The default anon/user rates protect the API as a whole. Endpoints that need
something different get their own scope here.

**A scope is sized for one kind of call, so a scope must not span two.** That
sounds obvious and is the exact mistake this module now exists to prevent: the
companion endpoint originally throttled its GET and its POST together, at the
rate the POST needed, and the POST is a handful of paid image calls while the GET
is one indexed row lookup that the greeting scene *polls*. The read burned the
write's whole hourly budget in forty seconds and every later poll got a 429 —
which, since the poll is what tells the child their companion has woken up, meant
a companion that never woke up. Rate limits protecting a resource from its own UI
is the failure mode to watch for.
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthAnonThrottle(AnonRateThrottle):
    """Per-IP limit on the credential endpoints (where the caller has no identity yet)."""

    scope = "auth"


class AuthUserThrottle(UserRateThrottle):
    """Per-parent limit for credential endpoints reached while already signed in."""

    scope = "auth"


class CompanionCreateThrottle(UserRateThrottle):
    """Per-parent limit on *creating* a companion (§16.6).

    The expensive one: six illustrations and a voice line, paid for on a provider.
    A family does this once per child, so a low ceiling costs nothing real and
    caps what a loop can spend.
    """

    scope = "companion"


class CompanionReadThrottle(UserRateThrottle):
    """Per-parent limit on *reading* a companion.

    Sized for polling, because polling is what this endpoint is for: the greeting
    scene asks it repeatedly while the art renders (§4.3). Per hour rather than
    per day, so a window left open all afternoon cannot exhaust a daily budget and
    strand the next visit.

    Not unlimited. A client with a runaway timer should still hit a ceiling — it
    just has to be a ceiling the product's own polling never reaches.
    """

    scope = "companion_read"
