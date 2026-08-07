"""`manage.py hello_beat` end to end, with Claude stubbed out.

The live run needs a real key and is the founder's manual check. What CI must
guarantee is the wiring around it: the router picks the Anthropic provider when
a key is present, the response is validated, valid JSON reaches stdout, and a
missing key produces a message rather than a traceback.
"""

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.story.ai.router import AnthropicProvider
from apps.story.ai.schemas import Beat
from tests.test_ai_router import VALID_BEAT


@pytest.fixture(autouse=True)
def _anthropic_only(settings):
    """Pin the provider order these tests assert about.

    Without this the developer's own `.env` decides which provider the router
    picks, so a machine configured for OpenRouter runs the "no Anthropic key"
    test against a *live* provider — passing or failing for reasons that have
    nothing to do with the code under test. CI never noticed because CI has no
    `.env`.
    """
    settings.AI_PROVIDER_ORDER = ["anthropic"]


def test_prints_valid_beat_json(monkeypatch, settings) -> None:
    settings.ANTHROPIC_API_KEY = "test-key"

    def fake_generate_json(self, **kwargs: Any) -> str:
        # The command must send the hello-beat prompt, not an empty one.
        assert "The Primer" in kwargs["system"]
        assert "frogs" in kwargs["messages"][0]["content"]
        return json.dumps(VALID_BEAT)

    monkeypatch.setattr(AnthropicProvider, "generate_json", fake_generate_json)

    out = StringIO()
    call_command("hello_beat", stdout=out)

    printed = out.getvalue()
    payload = json.loads(printed[: printed.rindex("}") + 1])
    assert Beat.model_validate(payload).beat_id == "b1"


def test_missing_key_fails_with_a_readable_message(settings) -> None:
    settings.ANTHROPIC_API_KEY = ""

    with pytest.raises(CommandError, match="ANTHROPIC_API_KEY"):
        call_command("hello_beat", stdout=StringIO())
