"""Give a staff account its `/admax` second factor.

    python manage.py admax_enroll ops@example.com
    python manage.py admax_enroll ops@example.com --reset

The only writer of `AdmaxCredential.secret`. There is no enrolment page and there
should not be one: enrolling is the act of *creating* an administrator, and any
web form that can do it is a web form an attacker would rather have than the
dashboard itself. Requiring a shell means requiring the deploy credentials.

The seed is printed **once**, to a terminal, and never stored anywhere it can be
read back — `--reset` issues a new one rather than recovering the old one,
because a seed that can be recovered is a seed that can be recovered by whoever
gets the database.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.analytics import totp
from apps.analytics.models import AdmaxCredential


class Command(BaseCommand):
    help = "Issue a TOTP second factor for a staff account so it can sign in to /admax."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("email", help="The staff account's email address.")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Replace an existing seed (a lost phone). The old one stops working at once.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = options["email"].strip()
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No account with email {email!r}.")
        if not user.is_staff:
            # Refused rather than fixed. Granting staff is a different decision
            # from issuing a second factor, and a command that quietly did both
            # would make `is_staff` something this command can hand out.
            raise CommandError(
                f"{email} is not staff. Grant that first — enrolling does not grant access."
            )

        credential = AdmaxCredential.objects.filter(user=user).first()
        if credential is not None and not options["reset"]:
            raise CommandError(
                f"{email} is already enrolled. Use --reset to issue a new seed "
                "(the current one stops working immediately)."
            )

        secret = totp.new_secret()
        if credential is None:
            credential = AdmaxCredential.objects.create(user=user, secret=secret)
        else:
            credential.secret = secret
            # The replay guard and the lockout both reset with the seed: they are
            # facts about the old secret, and carrying them over would mean a new
            # phone starts life locked out or unable to use its first code.
            credential.last_step = None
            credential.failed_attempts = 0
            credential.locked_until = None
            credential.save(
                update_fields=[
                    "secret",
                    "last_step",
                    "failed_attempts",
                    "locked_until",
                    "updated_at",
                ]
            )

        self.stdout.write(
            "\n".join(
                [
                    "",
                    f"  /admax second factor for {email}",
                    "",
                    "  Add this to an authenticator app (Google Authenticator, 1Password, Authy):",
                    "",
                    f"    secret : {secret}",
                    f"    uri    : {totp.provisioning_uri(secret, account=email)}",
                    "",
                    "  This is shown once. It is not recoverable — if it is lost,",
                    "  run this command again with --reset.",
                    "",
                ]
            )
        )
