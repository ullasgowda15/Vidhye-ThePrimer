"""`/admax` — the ops dashboard that actually works in production (§5.6, §22).

**Why this page exists.** `/admin/metrics` is the same numbers, and in production
it is unreachable in practice: it is gated by `staff_member_required`, which
bounces you to the Django admin login, and the admin's stylesheets are never
collected into the image (`infra/Dockerfile.backend` runs no `collectstatic`, and
`staticfiles_urlpatterns()` is `DEBUG`-only in `config/urls.py`). The login page
renders as unstyled HTML on a good day. The fix is *not* to start serving admin
assets: that would mean spending effort making the one surface we least want
people wandering around in more inviting. So `/admax` is a second door — one
page, its own login, its own session, no dependency on `django.contrib.admin` at
all.

**Two locks on that door (the "2-step verification" this was asked for).**

1. *Something known* — a staff account's email and password, checked through
   Django's own hasher via `authenticate`.
2. *Something held* — a six-digit TOTP code from an authenticator app, checked
   against `AdmaxCredential.secret` (`apps.analytics.totp`).

Both are required, in that order, every time a session is established. The reason
this is worth the ceremony is what the page shows: every §22 number for every
family at once, plus what the product spends. It is the highest-value single page
in the product to anyone who should not have it, and a password is one phishing
email away.

**An `/admax` session is not a Django auth session, deliberately.** Nothing here
calls `django.contrib.auth.login`. A successful sign-in writes three keys of this
module's own into the session and nothing else, so the cookie it produces
authenticates you to this page and to nothing at `/admin/` — which is what makes
"don't unlock the admin" a property of the system rather than a promise. It also
means an `/admax` cookie stolen from a laptop cannot be replayed against the
parent API, which authenticates by bearer token and never reads the session.

**No enrolment endpoint.** Seeds are provisioned by `manage.py admax_enroll`,
which needs a shell on the box. An enrolment page is a way to *become* an
administrator, and there is no version of it that is safer than a shell.

**Nothing on this page is per-child**, exactly as on `/admin/metrics` (§13.8).
Every figure is an aggregate across families; the only place one child is
legitimately readable is that child's own parent's dashboard.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from django.contrib.auth import authenticate
from django.db.models import F
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.analytics import internal, totp
from apps.analytics.models import AdmaxCredential

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# --- session keys ----------------------------------------------------------
# Namespaced so they cannot collide with Django's own (`_auth_user_id`), which is
# the key that would make this a real admin session. That collision is the one
# bug in this file that would be genuinely dangerous, so the names are far away
# from it.
PENDING_USER = "admax.pending_user"
PENDING_SINCE = "admax.pending_since"
VERIFIED_USER = "admax.user"
VERIFIED_AT = "admax.verified_at"

#: How long step 1 stays good while you reach for your phone. Five minutes is
#: long enough to unlock a phone and find the app, short enough that a password
#: typed on a shared machine and abandoned does not leave a half-open door.
PENDING_TTL = datetime.timedelta(minutes=5)

#: How long a completed sign-in lasts before both factors are asked for again.
#: Twelve hours: one working day, so nobody is re-authenticating over lunch, and
#: no session survives to the next morning unattended.
SESSION_TTL = datetime.timedelta(hours=12)

#: Wrong codes before the account is locked, and for how long. Six digits is a
#: million guesses; without a lockout an unattended script gets through in hours,
#: which would make the second factor decorative.
MAX_ATTEMPTS = 5
LOCKOUT = datetime.timedelta(minutes=15)

#: One message for every way step 1 can fail. Wrong password, unknown email, a
#: non-staff account and an unenrolled one are four different facts, and telling
#: them apart is how an attacker turns a login form into a list of who has
#: access. The server log distinguishes them; the page does not.
BAD_SIGN_IN = "Those details did not work."


def _client(request: HttpRequest) -> str:
    """The requester, for the log line. Never rendered."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0] or request.META.get("REMOTE_ADDR", "")).strip() or "-"


def _redirect(name: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(reverse(name))


def _locked(credential: AdmaxCredential, *, now: datetime.datetime) -> bool:
    return credential.locked_until is not None and credential.locked_until > now


def _register_failure(credential: AdmaxCredential, *, now: datetime.datetime) -> None:
    """Count a wrong code and lock the account if it has run out of attempts.

    `F()` on the counter so two racing attempts cannot both read 4 and both write
    5 — the exact race a distributed brute-force produces, and the one that would
    let the lockout be outrun by opening more connections.
    """
    AdmaxCredential.objects.filter(pk=credential.pk).update(
        failed_attempts=F("failed_attempts") + 1
    )
    credential.refresh_from_db(fields=["failed_attempts"])

    if credential.failed_attempts >= MAX_ATTEMPTS:
        credential.locked_until = now + LOCKOUT
        credential.failed_attempts = 0
        credential.save(update_fields=["locked_until", "failed_attempts", "updated_at"])
        logger.warning("admax.locked user=%s until=%s", credential.user_id, credential.locked_until)


def current_user(request: HttpRequest) -> User | None:
    """The signed-in staff user, or `None` if this session may not see the page.

    The single gate. Every rule that decides whether a request is allowed through
    is enforced here and nowhere else, so there is one place to read and one place
    to get wrong: the session must carry a verification, the verification must be
    inside `SESSION_TTL`, and the account it names must still exist, still be
    active and still be staff.

    That last clause is why this re-reads the row on every request rather than
    trusting what the session says. Revoking someone's access has to be one
    `is_staff = False`, taking effect on their next click — not on the expiry of a
    cookie they are already holding.
    """
    user_key = request.session.get(VERIFIED_USER)
    verified_at = request.session.get(VERIFIED_AT)
    if not user_key or not verified_at:
        return None

    try:
        moment = datetime.datetime.fromisoformat(verified_at)
    except ValueError:
        return None
    if timezone.now() - moment > SESSION_TTL:
        return None

    credential = AdmaxCredential.objects.select_related("user").filter(user_id=user_key).first()
    if credential is None or not credential.user.is_active or not credential.user.is_staff:
        return None
    return credential.user


def requires_admax(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Send anyone without a verified session to step 1.

    A redirect rather than a 403, because every legitimate visitor to this page is
    a person who can sign in, and a 403 with no link is a dead end for them and no
    obstacle at all to anyone else.
    """

    def guard(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if current_user(request) is None:
            return _redirect("admax-login")
        return view(request, *args, **kwargs)

    guard.__name__ = view.__name__
    guard.__doc__ = view.__doc__
    return guard


# ---------------------------------------------------------------------------
# Step 1 — something known
# ---------------------------------------------------------------------------
@never_cache
@require_http_methods(["GET", "POST"])
def login(request: HttpRequest) -> HttpResponse:
    """Email and password. On success, hands over to step 2 — never to the page.

    `never_cache` on all three auth views: a shared proxy that cached this would
    serve one operator's form state, and one operator's CSRF token, to the next.
    """
    if request.method == "GET":
        # An already-verified visitor gets the dashboard rather than a form asking
        # them to prove again what the session already says.
        if current_user(request) is not None:
            return _redirect("admax")
        return render(request, "analytics/admax_login.html", {"error": None})

    email = (request.POST.get("email") or "").strip()
    user = authenticate(request, email=email, password=request.POST.get("password") or "")

    # Four distinct failures, one message (see `BAD_SIGN_IN`). The reason is
    # logged so an operator locked out by a missing enrolment can be told why by
    # somebody reading the logs, rather than by the login form telling everybody.
    reason = None
    if user is None:
        reason = "credentials"
    elif not user.is_staff or not user.is_active:
        reason = "not_staff"
    elif not AdmaxCredential.objects.filter(user=user).exists():
        reason = "not_enrolled"

    # `user is None` is already covered by `reason`; it is restated so the reader
    # (and the type checker) can see that nothing below this line runs without a
    # real account.
    if reason is not None or user is None:
        logger.warning(
            "admax.sign_in_refused reason=%s email=%s ip=%s", reason, email, _client(request)
        )
        return render(request, "analytics/admax_login.html", {"error": BAD_SIGN_IN}, status=401)

    # The password is right, and that is *all* it is. Nothing about this session
    # is privileged yet — `current_user` reads `VERIFIED_USER`, which is only
    # written on the far side of step 2.
    request.session[PENDING_USER] = str(user.pk)
    request.session[PENDING_SINCE] = timezone.now().isoformat()
    return _redirect("admax-verify")


# ---------------------------------------------------------------------------
# Step 2 — something held
# ---------------------------------------------------------------------------
@never_cache
@require_http_methods(["GET", "POST"])
def verify(request: HttpRequest) -> HttpResponse:
    """The six-digit code. Only this view may write `VERIFIED_USER`."""
    credential = _pending_credential(request)
    if credential is None:
        return _redirect("admax-login")

    now = timezone.now()
    if _locked(credential, now=now):
        return render(
            request,
            "analytics/admax_verify.html",
            {"error": "Too many wrong codes. Try again in a few minutes.", "locked": True},
            status=429,
        )

    if request.method == "GET":
        return render(request, "analytics/admax_verify.html", {"error": None, "locked": False})

    step = totp.verify(
        credential.secret,
        request.POST.get("code") or "",
        # The replay guard. A code that is arithmetically correct but was already
        # spent is refused here, which is the difference between a code being
        # valid and a code being usable.
        after=credential.last_step,
    )
    if step is None:
        _register_failure(credential, now=now)
        logger.warning("admax.code_refused user=%s ip=%s", credential.user_id, _client(request))
        return render(
            request,
            "analytics/admax_verify.html",
            {"error": "That code was not right.", "locked": False},
            status=401,
        )

    # Both factors are in. Rotate the session key *before* writing the verified
    # marker: the id in the browser at this moment is one that existed while the
    # session was unprivileged, so anything that saw it — a fixated cookie set
    # before sign-in, a proxy log — must not be holding a key that now opens the
    # page.
    request.session.cycle_key()
    request.session[VERIFIED_USER] = str(credential.user_id)
    request.session[VERIFIED_AT] = now.isoformat()
    request.session.pop(PENDING_USER, None)
    request.session.pop(PENDING_SINCE, None)

    credential.last_step = step
    credential.failed_attempts = 0
    credential.locked_until = None
    credential.last_seen_at = now
    credential.save(
        update_fields=["last_step", "failed_attempts", "locked_until", "last_seen_at", "updated_at"]
    )

    logger.info("admax.signed_in user=%s ip=%s", credential.user_id, _client(request))
    return _redirect("admax")


def _pending_credential(request: HttpRequest) -> AdmaxCredential | None:
    """The account that passed step 1, if step 1 is still recent enough to count.

    Every condition that could let step 2 be reached without step 1 is checked
    here: no marker, an unparseable one, an expired one, or one naming an account
    that has since stopped being staff. A caller that gets `None` is sent back to
    the beginning.
    """
    user_key = request.session.get(PENDING_USER)
    since = request.session.get(PENDING_SINCE)
    if not user_key or not since:
        return None

    try:
        started = datetime.datetime.fromisoformat(since)
    except ValueError:
        return None
    if timezone.now() - started > PENDING_TTL:
        return None

    credential = AdmaxCredential.objects.select_related("user").filter(user_id=user_key).first()
    if credential is None or not credential.user.is_active or not credential.user.is_staff:
        return None
    return credential


@never_cache
@require_http_methods(["GET", "POST"])
def logout(request: HttpRequest) -> HttpResponse:
    """Drop the session entirely.

    `flush` rather than deleting this module's four keys: it clears the store and
    issues a new session id, so nothing about the old one is left to replay. There
    is no confirmation step because there is nothing to lose by signing out.
    """
    request.session.flush()
    return _redirect("admax-login")


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
@never_cache
@requires_admax
def dashboard(request: HttpRequest) -> HttpResponse:
    """`/admax` — every §22 number, rendered from `internal.dashboard_context`.

    The context is built by `internal.dashboard_context`, which `/admin/metrics`
    also uses, so the two pages are two renderings of one set of numbers rather
    than two dashboards that will eventually disagree.
    """
    context = internal.dashboard_context(internal.days_from(request))
    # Whose session this is, shown in the header. An operator who is looking at
    # production data should be able to see which account they are doing it as.
    context["operator"] = current_user(request)
    return render(request, "analytics/admax.html", context)
