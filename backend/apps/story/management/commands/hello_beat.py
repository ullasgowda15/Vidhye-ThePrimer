"""End-to-end proof that the AI layer works.

    python manage.py hello_beat

Exits 0 with a schema-valid Beat printed as JSON, or non-zero with a message a
human can act on. Everything here is throwaway — no child data, no persistence.
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.story.ai.prompts import hello_beat as hello_beat_prompt
from apps.story.ai.router import (
    AIConfigurationError,
    AIError,
    BeatValidationError,
    ProviderError,
    generate_beat,
)


class Command(BaseCommand):
    help = "Generate one validated story Beat via the model router and print it as JSON."

    def handle(self, *args: Any, **options: Any) -> None:
        context = {
            "system": hello_beat_prompt.SYSTEM,
            "user": hello_beat_prompt.USER,
        }

        try:
            beat = generate_beat(context)
        except AIConfigurationError as exc:
            # The common case: no API key. Say exactly what to do, don't traceback.
            raise CommandError(
                f"{exc}\n\n"
                "Copy .env.example to .env and set ANTHROPIC_API_KEY, then re-run:\n"
                "    python manage.py hello_beat"
            ) from exc
        except BeatValidationError as exc:
            raise CommandError(
                f"The model never returned a schema-valid beat: {exc}\n"
                "This is a prompt or schema problem, not a configuration one."
            ) from exc
        except ProviderError as exc:
            raise CommandError(f"The AI provider could not complete the request: {exc}") from exc
        except AIError as exc:
            raise CommandError(f"AI layer error: {exc}") from exc

        self.stdout.write(json.dumps(beat.model_dump(), indent=2, ensure_ascii=False))
        self.stdout.write(self.style.SUCCESS("\nBeat validated against the Day-1 schema."))
