"""Fixtures shared by every test in the backend.

Two things are enforced here for the whole suite rather than remembered per
test file:

* **No test ever writes into the repository.** The media backend is pointed at
  a per-test temporary directory, so a run that generates a hundred
  illustrations leaves nothing behind.
* **No test ever calls a paid provider.** The image and voice adapters are
  pinned to their offline `local` implementations. CI has no keys, but a
  developer's shell does — and a suite that quietly spends money on someone's
  machine and not in CI is the worst kind of test.
"""

import datetime
import json
import re
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture(autouse=True)
def no_live_providers(settings: Any) -> None:
    """Blank every credential for the duration of a test.

    CI has no keys, but a developer's `.env` does. Without this, running the
    suite locally would make real calls to Anthropic and to an image provider —
    money spent, and worse, tests that pass on one machine and fail on another.
    Tests that need a provider set their own key explicitly.
    """
    settings.ANTHROPIC_API_KEY = ""
    settings.OPENROUTER_API_KEY = ""
    settings.CLOUDFLARE_ACCOUNT_ID = ""
    settings.CLOUDFLARE_API_TOKEN = ""
    settings.ELEVENLABS_API_KEY = ""
    settings.ELEVENLABS_VOICE_ID = ""
    settings.ELEVENLABS_VOICE_MAP = {}


@pytest.fixture(autouse=True)
def offline_media(tmp_path: Path, settings: Any) -> Path:
    """Local storage, local providers, for every test."""
    settings.MEDIA_ROOT = str(tmp_path)
    settings.STORAGES = {
        **settings.STORAGES,
        "media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path), "base_url": "/media/"},
        },
    }
    settings.MEDIA_CDN_BASE_URL = ""
    settings.IMAGE_PROVIDER = "local"
    settings.TTS_PROVIDER = "local"
    return tmp_path


class ScriptedAIProvider:
    """Stands in for Claude, answering by contract and recording every prompt.

    It dispatches on the schema it is handed — `StoryPlan` or `Beat` — rather than
    on call order, so a test that only cares about the beat does not have to know
    that a plan is generated first.

    Four things it does deliberately:

    * **It echoes the child's seeds back.** The narration is built around the first
      "loves X" line it can find in the prompt, so a test can follow one seed from
      the child's profile all the way into a streamed beat. What that proves is the
      *plumbing* — that interests reach the model at all; whether a real model uses
      them well is what `manage.py smoke_story` is for.
    * **It echoes the child's choice back too.** When the assembled prompt carries
      a `Chose:` line, the narration names it. That is what lets a test assert the
      thing §8.5 actually requires — that a consequence beat is recognisably about
      *this* choice — rather than merely that some beat came back.
    * **It reads its own position out of the prompt.** Beat one is a counting beat,
      the middle beats offer illustrated choices, and the beat the prompt calls the
      last one closes warmly with a hook. A scripted adventure therefore has the
      same *shape* as a generated one, which is what makes playing one end to end
      in CI worth anything.
    * **It can be told to answer badly.** `queue_beat_response` pushes raw strings,
      which is how the router's validate-and-repair path (§19.6) is exercised
      end-to-end instead of only in the router's own unit tests.
    """

    name = "scripted"
    output_mode = "json_schema"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.queued: list[str] = []
        self.plan_calls = 0
        self.beat_calls = 0

    def queue_beat_response(self, *raw: str) -> None:
        """Answer the next beat call(s) with exactly these strings."""
        self.queued.extend(raw)

    def is_configured(self) -> bool:
        return True

    def generate_json(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        user = str(messages[-1].get("content", ""))
        self.prompts.append((system, user))

        if schema.get("title") == "StoryPlan":
            self.plan_calls += 1
            return json.dumps(self.plan())

        self.beat_calls += 1
        if self.queued:
            return self.queued.pop(0)
        return json.dumps(self.beat(system, user))

    @staticmethod
    def plan() -> dict[str, Any]:
        return {
            "title": "The Frog Chorus",
            "skeleton": [
                "Pip and the child arrive at the riverbank at dawn.",
                "The frog chorus has lost one of its singers.",
                "They follow the sound upstream.",
                "The missing frog is found under a leaf.",
                "The whole chorus sings, and Pip says thank you.",
            ],
            "branch_themes": ["follow the river", "ask the dragonflies", "sing along"],
        }

    #: The options a scripted decision beat offers. Two, with distinct keys and
    #: distinct icons, so a test can make genuinely *different* choices across two
    #: play-throughs and see the story diverge.
    OPTIONS = (
        ("follow_river", "Follow the sparkling river", "a sparkling river between mossy stones"),
        ("ask_dragonflies", "Ask the dragonflies", "a cloud of blue dragonflies over reeds"),
        ("sing_along", "Sing along with the chorus", "a ring of lily pads under warm light"),
    )

    @staticmethod
    def beat(system: str, user: str = "") -> dict[str, Any]:
        loved = re.search(r"loves ([^.\n]+)\.", system)
        subject = loved.group(1) if loved else "the woods"

        seq = int(match.group(1)) if (match := re.search(r"Write beat (\d+)", user)) else 1
        is_final = "last beat of the sitting" in user
        # The assembled `current_turn` layer, read back. A real model would read it
        # as context; this reads it so the test can prove it arrived.
        chosen = re.search(r"^Chose: (.+)$", system, re.MULTILINE)
        helped = "The companion helped them get there." in system

        opening = f"Pip peeks over the log. Three little {subject} sit in a row!"
        if is_final:
            narration = (
                f"Pip yawns happily. The {subject} are all home safe, and the river is quiet. "
                "'Come back tomorrow — I found a map to somewhere new.'"
            )
        elif chosen:
            # Naming the choice is the whole point: §8.5's consequence must be
            # recognisably about what *this* child picked.
            narration = f"Because you chose {chosen.group(1)}, the {subject} lead you onward!"
            if helped:
                narration = f"'Let's try it together,' says Pip. {narration}"
        else:
            narration = opening

        interaction: dict[str, Any]
        if is_final:
            interaction = {
                "type": "none",
                "prompt_audio": None,
                "expected": None,
                "choices": None,
            }
        elif seq == 1:
            interaction = {
                "type": "count",
                "prompt_audio": "How many can you count?",
                "expected": 3,
                "choices": None,
            }
        else:
            interaction = {
                "type": "choose",
                "prompt_audio": "Which way shall we go?",
                "expected": None,
                "choices": [
                    {"key": key, "label": label, "icon_prompt": icon}
                    for key, label, icon in ScriptedAIProvider.OPTIONS[:2]
                ],
            }

        return {
            "beat_id": f"b{seq}",
            "narration": narration,
            "scene": {
                "setting": "mossy riverbank at dawn",
                "illustration_prompt": f"a mossy riverbank at dawn with three {subject} on a log",
                "characters_present": ["pip"],
                # The companion and nobody else, so the default scripted adventure
                # introduces no recurring cast. Tests that want one queue a beat
                # that names it (`TestARecurringCharacterIsTheSameCharacter`) —
                # keeping it out of the default keeps every other test's prompt and
                # character-hash stable.
                "new_characters": [],
            },
            "interaction": interaction,
            "learning": {
                "objective_id": "count_to_3",
                "difficulty": 0.4,
                "skill_evidence_key": "count_riverbank",
            },
            "emotion": "playful_curious",
            "continuity_refs": ["companion:pip"],
            "next_hint": "if the child counts -> cross the river",
        }


@pytest.fixture
def fake_ai(monkeypatch: Any) -> ScriptedAIProvider:
    """Replace provider *selection* only, so the router's own logic still runs.

    Patching `from_settings` rather than the module-level `generate_beat` seam
    keeps validate-and-repair (§19.6) in the path being tested. A test that stubbed
    the whole router would pass while repair was broken.
    """
    from apps.story.ai.router import ModelRouter

    provider = ScriptedAIProvider()
    monkeypatch.setattr(
        ModelRouter,
        "from_settings",
        classmethod(lambda cls: ModelRouter(providers=[provider])),
    )
    return provider


@pytest.fixture
def child(db: Any) -> Any:
    """A child with a parent, for tests that need one and nothing else.

    Deliberately independent of the API fixtures in `tests/conftest.py`: unit
    tests for the media and companion services should not have to sign anybody
    in to render a picture.
    """
    from apps.profiles.models import Child

    parent = User.objects.create_user(email="unit-parent@example.com", password="primer-42-test")
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )
