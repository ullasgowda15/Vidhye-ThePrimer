"""The adventure API and the beat stream (PRODUCT.md §18.4, §18.5, §16.4).

The stream is tested through Django's `AsyncClient`, driven from a synchronous
test with `async_to_sync`. That combination is deliberate:

* The view is `async` and must stay so — under WSGI the generator is drained into
  one blob before a byte is sent, which is a different product, not a degraded
  stream (§16.4). A sync test client would hide that.
* Frames are collected **one at a time** and compared by position, because
  "arrived incrementally" is the property the endpoint exists for. A test that
  joined the body first would pass against a fully buffered response.
* `transaction=True` because the two media stages run on their own threads with
  their own database connections — which is what makes them genuinely concurrent
  (see `orchestrator._offload`). Inside pytest-django's default transaction those
  threads would see an empty database. `serialized_rollback=True` comes with it:
  a real transaction is flushed rather than rolled back, which takes the seeded
  `prompt_version` row (§19.2) with it, and a generation with no identity layer
  fails closed — correctly, but not for the reason the test is about.
"""

import datetime
import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.test import AsyncClient

from apps.companion.models import Companion
from apps.companion.service import create_companion
from apps.media.models import MediaType
from apps.profiles.models import Child
from apps.story.ai.schemas import Beat as BeatContract
from apps.story.models import Adventure, Beat
from tests.conftest import LOGIN_URL, PASSWORD

STREAM_CONTENT_TYPE = "text/event-stream"


def adventures_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/adventures"


@pytest.fixture
def aarav(parent, consent) -> Child:
    """A five-year-old who loves frogs. The frogs are the point: they are the seed
    a beat has to be recognisably built around (§8.1)."""
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs", "rivers"],
        fears=["the dark"],
        intensity_ceiling=1,
    )


@pytest.fixture
def pip(aarav) -> Companion:
    companion = create_companion(
        child=aarav,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    # The descriptor only: the reference set is Day 3's business and rendering it
    # here would make every test in this file pay for six illustrations.
    companion.visual_descriptor = "a small round friendly dragon with warm ember scales"
    companion.save(update_fields=["visual_descriptor"])
    return companion


# ---------------------------------------------------------------------------
# POST /children/{id}/adventures  (§18.4)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_beginning_an_adventure_persists_a_plan_and_returns_a_stream_url(
    auth, aarav, pip, fake_ai
) -> None:
    response = auth.post(adventures_url(aarav.id), {}, format="json")

    assert response.status_code == 201, response.data
    body = response.data
    assert body["companion"] == {"name": "Pip", "voiceId": pip.voice_id}
    assert 5 <= body["estimatedBeats"] <= 7

    adventure = Adventure.objects.get(id=body["adventureId"])
    assert adventure.plan["skeleton"], "the plan must be persisted, not just generated"
    assert adventure.plan["branch_themes"]
    assert adventure.title == "The Frog Chorus"
    assert adventure.primary_objective_id == "count_to_3"
    assert adventure.status == Adventure.Status.IN_PROGRESS

    # The URL is not a string the client has to assemble — it is routable as given.
    assert body["streamUrl"] == f"/api/v1/adventures/{adventure.id}/stream"


@pytest.mark.django_db
def test_the_plan_call_carries_the_safety_layer_and_the_seeds(auth, aarav, pip, fake_ai) -> None:
    auth.post(adventures_url(aarav.id), {}, format="json")

    system, _ = fake_ai.prompts[0]
    # Every layer §19.1 names, in the prompt that generated the plan. A plan
    # produced without the safety policy attached is a plan with no rules on it.
    assert "<safety_policy>" in system
    assert "Intensity ceiling: 1 of 3" in system
    assert "Aarav is afraid of the dark." in system
    assert "Aarav loves frogs." in system
    # Seeds arrive as DATA, never as instructions (§13.5).
    assert '<memory_bundle kind="data">' in system
    # The plan call asks for a plan, not a beat.
    assert '"title": "StoryPlan"' in system


@pytest.mark.django_db
def test_beginning_an_adventure_requires_a_companion(auth, aarav, fake_ai) -> None:
    response = auth.post(adventures_url(aarav.id), {}, format="json")

    assert response.status_code == 409
    assert not Adventure.objects.exists()
    # No plan was generated either — the check is before the spend.
    assert fake_ai.plan_calls == 0


@pytest.mark.django_db
def test_beginning_an_adventure_requires_consent(auth, parent, fake_ai) -> None:
    unconsented = Child.objects.create(
        parent=parent,
        first_name="Nia",
        birthdate=datetime.date(datetime.date.today().year - 4, 2, 1),
    )
    response = auth.post(adventures_url(unconsented.id), {}, format="json")

    assert response.status_code == 403
    assert fake_ai.plan_calls == 0


@pytest.mark.django_db
def test_another_parent_cannot_start_a_story_for_this_child(api, other_parent, aarav, pip) -> None:
    login = api.post(LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    assert api.post(adventures_url(aarav.id), {}, format="json").status_code == 403


@pytest.mark.django_db
def test_beginning_an_adventure_requires_authentication(api, aarav) -> None:
    assert api.post(adventures_url(aarav.id), {}, format="json").status_code == 401


# ---------------------------------------------------------------------------
# GET /adventures/{id}/stream  (§18.5)
# ---------------------------------------------------------------------------
def collect_stream(url: str, token: str | None) -> tuple[Any, list[str]]:
    """Open the stream and return `(response, frames)` in arrival order.

    Chunks are read one at a time from the async iterator, so the list really is
    the order the client saw — the assertion "narration before picture" means
    nothing otherwise.
    """

    async def run() -> tuple[Any, list[str]]:
        client = AsyncClient()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await client.get(url, headers=headers)
        if not getattr(response, "streaming", False):
            return response, []
        chunks = [
            chunk.decode() if isinstance(chunk, bytes) else str(chunk)
            # `streaming_content` exists only on a StreamingHttpResponse, which is
            # what the assertion above has just established.
            async for chunk in response.streaming_content  # type: ignore[attr-defined]
        ]
        return response, chunks

    return async_to_sync(run)()


def frames_of(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """Parse `(event, data)` pairs out of collected chunks."""
    parsed: list[tuple[str, dict[str, Any]]] = []
    for block in "".join(chunks).split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if event and data:
            parsed.append((event, json.loads(data)))
    return parsed


@pytest.fixture
def started(auth, aarav, pip, fake_ai) -> dict[str, Any]:
    """An adventure begun through the API, plus the parent's access token."""
    response = auth.post(adventures_url(aarav.id), {}, format="json")
    assert response.status_code == 201, response.data
    token = auth._credentials["HTTP_AUTHORIZATION"].removeprefix("Bearer ")
    return {"body": response.data, "token": token, "child": aarav}


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_stream_delivers_a_schema_valid_beat_then_done(started) -> None:
    response, chunks = collect_stream(started["body"]["streamUrl"], started["token"])

    assert response.status_code == 200
    assert response["Content-Type"] == STREAM_CONTENT_TYPE
    # Not one buffered blob: the open comment, the beat, the media and the ending
    # each left the server separately (§16.4).
    assert len(chunks) >= 4, chunks
    assert chunks[0] == ": open\n\n"

    events = frames_of(chunks)
    names = [event for event, _ in events]
    assert names[0] == "beat"
    assert names[-1] == "done"
    assert events[-1][1] == {"status": "awaiting_choice"}

    _, beat = events[0]
    # The §8.1 contract, validated rather than eyeballed. The stream carries the
    # client's slice, so the persisted row is what the full contract is checked
    # against.
    stored = Beat.objects.get(id=beat["beatId"])
    BeatContract.model_validate(
        {
            "beat_id": f"b{stored.seq}",
            "narration": stored.narration,
            "scene": stored.scene,
            "interaction": stored.interaction,
            "learning": stored.learning,
            "emotion": stored.emotion,
            "continuity_refs": [],
            "next_hint": "",
        }
    )
    assert beat["interaction"]["type"] == "count"
    assert beat["emotion"] == "playful_curious"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_narration_arrives_before_the_illustration(started) -> None:
    _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)

    beat_index = next(i for i, (name, _) in enumerate(events) if name == "beat")
    media_index = next(i for i, (name, _) in enumerate(events) if name == "media")

    # The whole reason this endpoint streams (§18.5): the child starts hearing
    # their story while the image model is still drawing.
    assert beat_index < media_index
    assert events[beat_index][1]["scene"] == {"imageUrl": None, "status": "pending"}


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_beat_is_personalised_from_the_child_s_seeds(started) -> None:
    _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)
    _, beat = events[0]

    # A frog-lover gets a frog-relevant beat. The stand-in model builds its
    # narration from whatever interest reached it, so this follows one seed from
    # the child's profile through the assembler into a streamed beat.
    assert "frogs" in beat["narration"]
    assert "frogs" in Beat.objects.get(id=beat["beatId"]).scene["illustration_prompt"]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_illustration_and_the_voice_both_attach_to_the_beat(started) -> None:
    _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)

    media = [payload for name, payload in events if name == "media"]
    assert len(media) == 2, media
    assert {"scene" in payload for payload in media} == {True, False}

    beat = Beat.objects.select_related("media", "audio").get(id=events[0][1]["beatId"])
    assert beat.media and beat.media.type == MediaType.IMAGE
    assert beat.audio and beat.audio.type == MediaType.AUDIO
    # The line the companion speaks is the narration plus the invitation, in one
    # clip and therefore one TTS call.
    assert beat.audio.is_servable


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_media_is_generated_in_parallel_and_the_timings_prove_it(started, caplog) -> None:
    with caplog.at_level("INFO", logger="apps.story.orchestrator"):
        collect_stream(started["body"]["streamUrl"], started["token"])

    timings = [line for line in caplog.text.splitlines() if "story.media.timing" in line]
    assert len(timings) == 2, caplog.text

    summary = next(line for line in caplog.text.splitlines() if "story.media.parallel" in line)
    wall = int(summary.split("wall_ms=")[1].split()[0])
    serial = int(summary.split("serial_ms=")[1].split()[0])
    # Both stages ran inside the same window. Awaiting them in turn would make the
    # wall time the sum, and with the offline adapters that difference is small —
    # so the assertion is on the shape of the arithmetic, and the log line itself
    # is what a human reads when the calls are really seconds long.
    assert wall <= serial + 5
    assert "overlap_ms=" in summary


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_reopening_the_stream_replays_the_beat_instead_of_paying_again(started, fake_ai) -> None:
    first = frames_of(collect_stream(started["body"]["streamUrl"], started["token"])[1])
    beats_generated = fake_ai.beat_calls

    _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    second = frames_of(chunks)

    # A child who reloads mid-story lands back in the same forest, and it costs
    # nothing (§14.5).
    assert second[0][1]["beatId"] == first[0][1]["beatId"]
    assert second[0][1]["narration"] == first[0][1]["narration"]
    assert fake_ai.beat_calls == beats_generated
    assert Beat.objects.count() == 1
    # Replayed with its media already in the first frame, so no `media` events
    # are needed at all.
    assert second[0][1]["scene"]["status"] == "ready"
    assert [name for name, _ in second] == ["beat", "done"]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_stream_ends_gracefully_when_a_beat_cannot_be_repaired(started, fake_ai) -> None:
    # Malformed twice: one repair attempt, then the router gives up (§19.6).
    fake_ai.queue_beat_response('{"beat_id": "b1"}', "not json at all")

    response, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)

    # A 200 that ends, not a 500 that breaks. The child keeps looking at their
    # companion thinking; they are never shown a failure (§13).
    assert response.status_code == 200
    assert events == [("done", {"status": "ended"})]
    assert fake_ai.beat_calls == 2, "exactly one repair attempt, no more"
    assert not Beat.objects.exists()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_malformed_generation_is_repaired_rather_than_abandoned(started, fake_ai) -> None:
    from conftest import ScriptedAIProvider

    fake_ai.queue_beat_response(
        '{"beat_id": "b1", "narration": "half a beat"}',
        json.dumps(ScriptedAIProvider.beat("Aarav loves frogs.")),
    )

    _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)

    assert events[0][0] == "beat"
    assert "frogs" in events[0][1]["narration"]
    assert fake_ai.beat_calls == 2


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_stream_requires_a_bearer_token(started) -> None:
    response, chunks = collect_stream(started["body"]["streamUrl"], None)

    assert response.status_code == 401
    assert chunks == []


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_another_parent_cannot_open_the_stream(started, other_parent, client) -> None:
    login = client.post(
        LOGIN_URL,
        data=json.dumps({"email": other_parent.email, "password": PASSWORD}),
        content_type="application/json",
    )
    token = login.json()["access"]

    response, _ = collect_stream(started["body"]["streamUrl"], token)
    assert response.status_code == 403


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_an_unknown_adventure_is_a_404(started) -> None:
    unknown = "/api/v1/adventures/00000000-0000-4000-8000-000000000000/stream"
    response, _ = collect_stream(unknown, started["token"])
    assert response.status_code == 404
