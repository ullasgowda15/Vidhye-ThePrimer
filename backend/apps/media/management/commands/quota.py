"""Show or clear the media providers' quota cooldowns (§16.6).

    python manage.py quota                 # who is resting, and for how long
    python manage.py quota --clear google  # a topped-up account, back in service
    python manage.py quota --clear all

`media.quota` guesses each vendor's reset window from what it meters, and a guess
is exactly the thing an operator needs to be able to overrule: an ElevenLabs
balance that was topped up an hour into a 24-hour cooldown is a paid-for provider
sitting idle, and the only alternative to this command is waiting or flushing
Redis by hand.

Read-only by default, because the first question is always "is the fallback I am
looking at deliberate?" — and answering it should not change anything.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.media import quota

#: The providers worth reporting on: everything that meters us. Named here rather
#: than derived from the adapter registries because those hold unimplemented
#: seams too, and a row saying "together: available" would be a lie about a
#: provider that cannot be called at all.
WATCHED = ("cloudflare", "google", "elevenlabs")


class Command(BaseCommand):
    help = "Show or clear media provider quota cooldowns."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--clear",
            metavar="PROVIDER",
            help="Forget one provider's cooldown, or 'all' to forget every one.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        target = options.get("clear")
        if target:
            self._clear(str(target))
            return

        self.stdout.write("")
        for name in WATCHED:
            remaining = quota.remaining_s(name)
            if remaining:
                self.stdout.write(
                    self.style.WARNING(
                        f"  RESTING  {name:<12} {_humanise(remaining)} left on its cooldown"
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"  OK       {name:<12} available"))
        self.stdout.write("")
        self.stdout.write(
            "A resting provider is skipped, not removed: if everything ahead of it "
            "fails it is still tried, so a wrong guess costs a place in the queue "
            "and never a picture."
        )

    def _clear(self, target: str) -> None:
        names = WATCHED if target == "all" else (target,)
        if target != "all" and target not in WATCHED:
            raise CommandError(f"Unknown provider '{target}'. Known: {', '.join(WATCHED)}, all.")
        for name in names:
            quota.clear(name)
            self.stdout.write(self.style.SUCCESS(f"cleared {name}"))


def _humanise(seconds: int) -> str:
    """`5h 12m`, because a cooldown is read by a person deciding whether to wait."""
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds % 60:02d}s"
