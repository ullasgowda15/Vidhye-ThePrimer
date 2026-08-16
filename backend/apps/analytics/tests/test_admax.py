"""`/admax` — the two-factor ops dashboard (`apps.analytics.admax`).

The assertions that matter most are the **negative** ones. A dashboard behind a
login is only as good as the things the login refuses, and every refusal here is
a rule that would be invisible if it silently stopped working: a password alone
must not be enough, a spent code must not work twice, a revoked staff flag must
take effect on the next click, and an `/admax` session must not open the Django
admin. Each of those has a test whose failure mode is "somebody got in".

`TestTheRFCVectors` checks the TOTP arithmetic against RFC 6238's own published
vectors rather than against our own implementation, which is the only way to know
that a real authenticator app will agree with us.
"""

from __future__ import annotations

import base64
import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from apps.analytics import admax, totp
from apps.analytics.models import AdmaxCredential

pytestmark = pytest.mark.django_db

PASSWORD = "storybook-primer-42"
LOGIN = "/admax/login"
VERIFY = "/admax/verify"
DASHBOARD = "/admax"


@pytest.fixture
def operator(db: Any) -> Any:
    """An enrolled staff account — the only kind that can sign in."""
    user = get_user_model().objects.create_user(
        email="ops@example.com", password=PASSWORD, is_staff=True
    )
    AdmaxCredential.objects.create(user=user, secret=totp.new_secret())
    return user


def _code(user: Any) -> str:
    credential = AdmaxCredential.objects.get(user=user)
    return totp.code_for(credential.secret, totp.step_at())


def _sign_in(client: Any, user: Any) -> None:
    """Walk both steps, the way a person does."""
    assert client.post(LOGIN, {"email": user.email, "password": PASSWORD}).status_code == 302
    assert client.post(VERIFY, {"code": _code(user)}).status_code == 302


class TestTheRFCVectors:
    """RFC 6238 Appendix B, so a real authenticator app and this module agree.

    The vectors are published as eight-digit codes; ours are the low six of each,
    which is what truncating to `10**6` produces. Checking against the RFC rather
    than against our own output is the whole point — an implementation that is
    self-consistently wrong passes every test it writes for itself.
    """

    #: The RFC's SHA-1 seed is the ASCII string "12345678901234567890".
    SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
            (20000000000, "353130"),
        ],
    )
    def test_the_published_vectors(self, moment: int, expected: str) -> None:
        assert totp.code_for(self.SECRET, totp.step_at(moment)) == expected

    def test_a_seed_survives_being_typed_by_a_human(self) -> None:
        """Authenticator apps show seeds in groups of four and people paste them
        back that way. Rejecting that is a support ticket, not a control."""
        spaced = " ".join(self.SECRET[i : i + 4] for i in range(0, len(self.SECRET), 4))
        assert totp.code_for(spaced.lower(), totp.step_at(59)) == "287082"

    def test_drift_is_accepted_but_bounded(self) -> None:
        now = 1111111111.0
        one_step_ago = totp.code_for(self.SECRET, totp.step_at(now) - 1)
        four_steps_ago = totp.code_for(self.SECRET, totp.step_at(now) - 4)

        assert totp.verify(self.SECRET, one_step_ago, moment=now) is not None
        # Two and a half minutes of leeway would be a stolen code's whole life.
        assert totp.verify(self.SECRET, four_steps_ago, moment=now) is None

    def test_rubbish_is_refused_without_arithmetic(self) -> None:
        for code in ("", "abcdef", "12345", "1234567", "  "):
            assert totp.verify(self.SECRET, code, moment=59) is None


class TestBothStepsAreRequired:
    """The property the whole feature exists for."""

    def test_a_password_alone_does_not_open_the_page(self, client: Any, operator: Any) -> None:
        """The single most important assertion in this file.

        Step 1 succeeding must leave the session *unprivileged*. If this ever
        fails, the second factor is decorative.
        """
        assert (
            client.post(LOGIN, {"email": operator.email, "password": PASSWORD}).status_code == 302
        )

        landing = client.get(DASHBOARD)
        assert landing.status_code == 302
        assert landing.headers["Location"] == LOGIN
        assert admax.VERIFIED_USER not in client.session

    def test_both_steps_together_do(self, client: Any, operator: Any) -> None:
        _sign_in(client, operator)

        page = client.get(DASHBOARD)
        assert page.status_code == 200
        assert "The Primer" in page.content.decode()

    def test_step_two_cannot_be_reached_without_step_one(self, client: Any) -> None:
        assert client.get(VERIFY).status_code == 302
        assert client.post(VERIFY, {"code": "000000"}).status_code == 302

    def test_a_wrong_password_says_nothing_about_why(self, client: Any, operator: Any) -> None:
        """One message for four different failures. Distinguishing them turns this
        form into a tool for discovering who has production access."""
        get_user_model().objects.create_user(
            email="parent@example.com", password=PASSWORD, is_staff=False
        )

        attempts = [
            {"email": operator.email, "password": "wrong-password"},
            {"email": "nobody@example.com", "password": PASSWORD},
            {"email": "parent@example.com", "password": PASSWORD},
        ]
        bodies = set()
        for attempt in attempts:
            response = client.post(LOGIN, attempt)
            assert response.status_code == 401
            bodies.add(admax.BAD_SIGN_IN in response.content.decode())

        assert bodies == {True}
        assert admax.PENDING_USER not in client.session

    def test_a_staff_account_with_no_second_factor_cannot_sign_in(self, client: Any) -> None:
        """Enrolment is what grants access to this page, and it needs a shell."""
        get_user_model().objects.create_user(
            email="new-ops@example.com", password=PASSWORD, is_staff=True
        )

        assert (
            client.post(LOGIN, {"email": "new-ops@example.com", "password": PASSWORD}).status_code
            == 401
        )


class TestTheCode:
    """What step 2 accepts, and what it refuses."""

    def test_a_wrong_code_is_refused(self, client: Any, operator: Any) -> None:
        client.post(LOGIN, {"email": operator.email, "password": PASSWORD})

        # `000000` collides with a real code once in a million; derive a wrong one.
        wrong = "000000" if _code(operator) != "000000" else "111111"
        assert client.post(VERIFY, {"code": wrong}).status_code == 401
        assert admax.VERIFIED_USER not in client.session

    def test_a_code_works_exactly_once(self, client: Any, operator: Any) -> None:
        """The replay guard. A code stays arithmetically valid for its whole step
        plus the drift either side, which is ample time for anyone reading the
        wire to send it again."""
        code = _code(operator)
        client.post(LOGIN, {"email": operator.email, "password": PASSWORD})
        assert client.post(VERIFY, {"code": code}).status_code == 302

        # A second browser, holding the same intercepted code, within its window.
        replay = Client()
        replay.post(LOGIN, {"email": operator.email, "password": PASSWORD})
        assert replay.post(VERIFY, {"code": code}).status_code == 401
        assert admax.VERIFIED_USER not in replay.session

    def test_the_account_locks_after_too_many_wrong_codes(self, client: Any, operator: Any) -> None:
        """Six digits is a million guesses. Without this the second factor is a
        speed bump for an unattended script."""
        client.post(LOGIN, {"email": operator.email, "password": PASSWORD})

        wrong = "000000" if _code(operator) != "000000" else "111111"
        for _ in range(admax.MAX_ATTEMPTS):
            client.post(VERIFY, {"code": wrong})

        credential = AdmaxCredential.objects.get(user=operator)
        assert credential.locked_until is not None

        # And the correct code does not get you in while locked.
        assert client.post(VERIFY, {"code": _code(operator)}).status_code == 429
        assert admax.VERIFIED_USER not in client.session

    def test_a_good_code_clears_the_failure_count(self, client: Any, operator: Any) -> None:
        client.post(LOGIN, {"email": operator.email, "password": PASSWORD})
        wrong = "000000" if _code(operator) != "000000" else "111111"
        client.post(VERIFY, {"code": wrong})
        client.post(VERIFY, {"code": _code(operator)})

        credential = AdmaxCredential.objects.get(user=operator)
        assert credential.failed_attempts == 0
        assert credential.last_seen_at is not None


class TestTheGateIsCheckedEveryRequest:
    """Access is a fact about the account now, not about the cookie's age."""

    def test_revoking_staff_takes_effect_on_the_next_click(
        self, client: Any, operator: Any
    ) -> None:
        """The reason `current_user` re-reads the row instead of trusting the
        session. Revocation has to be one flag, not the expiry of a cookie
        somebody is already holding."""
        _sign_in(client, operator)
        assert client.get(DASHBOARD).status_code == 200

        operator.is_staff = False
        operator.save(update_fields=["is_staff"])

        assert client.get(DASHBOARD).status_code == 302

    def test_deactivating_the_account_does_too(self, client: Any, operator: Any) -> None:
        _sign_in(client, operator)
        operator.is_active = False
        operator.save(update_fields=["is_active"])

        assert client.get(DASHBOARD).status_code == 302

    def test_a_stale_session_is_asked_for_both_factors_again(
        self, client: Any, operator: Any
    ) -> None:
        _sign_in(client, operator)

        session = client.session
        session[admax.VERIFIED_AT] = (
            timezone.now() - admax.SESSION_TTL - datetime.timedelta(minutes=1)
        ).isoformat()
        session.save()

        assert client.get(DASHBOARD).status_code == 302

    def test_signing_out_drops_the_session(self, client: Any, operator: Any) -> None:
        _sign_in(client, operator)
        client.post("/admax/logout")

        assert client.get(DASHBOARD).status_code == 302
        assert admax.VERIFIED_USER not in client.session


class TestItIsNotTheDjangoAdmin:
    """ "Don't unlock the admin" as a property of the system, not a promise."""

    def test_an_admax_session_does_not_open_the_django_admin(
        self, client: Any, operator: Any
    ) -> None:
        """Nothing in `admax` calls `django.contrib.auth.login`, so the session it
        writes carries no `_auth_user_id` and the admin does not know this
        visitor."""
        _sign_in(client, operator)

        assert "_auth_user_id" not in client.session
        # The admin bounces an unauthenticated visitor to its own login.
        assert client.get("/admin/", follow=False).status_code == 302

    def test_the_page_needs_no_static_files(self, client: Any, operator: Any) -> None:
        """The reason this page exists at all: production collects no static
        assets, so a dashboard that linked a stylesheet would break exactly the way
        the admin already does."""
        _sign_in(client, operator)
        body = client.get(DASHBOARD).content.decode()

        assert "<style>" in body
        assert "<link" not in body
        assert "<script" not in body


class TestThePageExplainsItself:
    """Every number is next to what it means — that is a requirement, not polish."""

    def test_every_section_is_present_with_its_explanation(
        self, client: Any, operator: Any
    ) -> None:
        _sign_in(client, operator)
        body = client.get(f"{DASHBOARD}?days=90").content.decode()

        for section in (
            "The five headline metrics",
            "Where families stop",
            "How many children are still here",
            "Is retention improving?",
            "Adventures per child per week",
            "What children actually pick",
            "What it costs",
            "Wellbeing guardrails",
            "Is the pipeline healthy?",
            "Glossary",
        ):
            assert section in body, section

        # The explanations themselves, not just the headings.
        assert "How to read this page" in body
        assert "Delighted Weekly Active" in body
        assert "not a zero" in body

    def test_the_window_selector_changes_the_window(self, client: Any, operator: Any) -> None:
        _sign_in(client, operator)
        assert client.get(f"{DASHBOARD}?days=30").status_code == 200
        # An arbitrary window is a full-table scan behind a login; it falls back.
        assert client.get(f"{DASHBOARD}?days=100000").status_code == 200

    def test_no_child_is_named_on_it(self, client: Any, operator: Any, aarav: Any) -> None:
        """§13.8. A staff page that could be pointed at one named four-year-old is
        a raw behavioural record with a nicer font."""
        _sign_in(client, operator)
        assert aarav.first_name not in client.get(DASHBOARD).content.decode()


class TestTheEnrolmentCommand:
    """The only writer of a seed, and it needs a shell."""

    def test_it_enrols_a_staff_account_and_prints_the_seed_once(self, capsys: Any) -> None:
        user = get_user_model().objects.create_user(
            email="new@example.com", password=PASSWORD, is_staff=True
        )
        call_command("admax_enroll", "new@example.com")

        credential = AdmaxCredential.objects.get(user=user)
        printed = capsys.readouterr().out
        assert credential.secret in printed
        assert "otpauth://totp/" in printed

    def test_it_refuses_a_non_staff_account(self) -> None:
        """Granting staff is a different decision from issuing a second factor."""
        get_user_model().objects.create_user(
            email="parent@example.com", password=PASSWORD, is_staff=False
        )
        with pytest.raises(CommandError, match="not staff"):
            call_command("admax_enroll", "parent@example.com")

    def test_it_refuses_to_silently_replace_a_seed(self, operator: Any) -> None:
        with pytest.raises(CommandError, match="already enrolled"):
            call_command("admax_enroll", operator.email)

    def test_reset_issues_a_new_seed_and_clears_the_lockout(self, operator: Any) -> None:
        """A lost phone. The old seed stops working at once, and the new one must
        not inherit a lockout or a replay marker belonging to the old one."""
        before = AdmaxCredential.objects.get(user=operator)
        before.locked_until = timezone.now() + datetime.timedelta(minutes=10)
        before.last_step = totp.step_at()
        before.save(update_fields=["locked_until", "last_step"])

        call_command("admax_enroll", operator.email, "--reset")

        after = AdmaxCredential.objects.get(user=operator)
        assert after.secret != before.secret
        assert after.locked_until is None
        assert after.last_step is None

    def test_it_refuses_an_unknown_address(self) -> None:
        with pytest.raises(CommandError, match="No account"):
            call_command("admax_enroll", "nobody@example.com")
