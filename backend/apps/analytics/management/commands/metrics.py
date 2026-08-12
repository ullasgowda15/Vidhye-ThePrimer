"""The internal dashboard, in a terminal (PRODUCT.md §22).

    python manage.py metrics [--days 7]

Same numbers as `/admin/metrics`, computed by the same two modules — see
`apps.analytics.internal.as_text`. It exists because the first question after a
week of real families is "what does a session cost", and the answer should not
require a browser, a staff account and a running web process.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.analytics import internal


class Command(BaseCommand):
    help = "Print activation, retention, completion, adventures/week and cost/session."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=internal.DEFAULT_DAYS,
            help=(
                "Window width in days (default: "
                f"{internal.DEFAULT_DAYS}). The North Star is a weekly number, so "
                "anything under 7 cannot contain one."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Plain ASCII section headings in `as_text` on purpose: this prints to a
        # terminal, and the Windows console mangles anything that is not.
        self.stdout.write(internal.as_text(options["days"]))
