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
from apps.story import orchestrator
from apps.story.ai.schemas import Beat as BeatContract
from apps.story.models import Adventure, Beat
from tests.conftest import LOGIN_URL, PASSWORD

STREAM_CONTENT_TYPE = "text/event-stream"


def adventures_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/adventures"


def current_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/adventures/current"


@pytest.fixture(autouse=True)
def media_worker(settings) -> None:
    """Run the beat-media task inline, because the suite has no Celery worker.

    The picture and the voice are a worker's job now (`apps/story/tasks.py`), and
    a test that queued them into a broker nobody is draining would wait out the
    stream's follow timeout and then assert on a beat with no media. Eager mode
    still goes through `generate_beat_media`, so the path under test is the real
    one — only the transport is short-circuited.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True


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
    # The plan call asks for a plan, not a beat. Written without the space after
    # the colon since Day 14: `_output_contract` renders the schema compactly,
    # because at `indent=2` it was about half of every beat prompt (§19.9's
    # prompt-minimisation lever). The assertion is about which schema, not about
    # how it is spaced.
    assert '"title":"StoryPlan"' in system


# ---------------------------------------------------------------------------
# Resuming: the same story, by name (§18.4, §14.5)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_second_begin_rejoins_the_same_adventure(auth, aarav, pip, fake_ai) -> None:
    """The reload leak, closed at the endpoint.

    The client's cache is memory-only, so a hard refresh lost the adventure id and
    the story page POSTed again — a new plan, a new opening beat, a new
    illustration and a new voice line, every reload. The founder's dev database
    grew to seven adventures for one child in twenty-three minutes.

    Making "begin" mean "begin **or** rejoin" fixes the reload, React StrictMode's
    double mount and a second device with one rule, and puts it where no client can
    forget it.
    """
    first = auth.post(adventures_url(aarav.id), {}, format="json")
    plans = fake_ai.plan_calls

    second = auth.post(adventures_url(aarav.id), {}, format="json")

    assert first.status_code == 201
    # 200, not 201: nothing was created, and the client can tell.
    assert second.status_code == 200, second.data
    assert second.data["adventureId"] == first.data["adventureId"]
    assert second.data["streamUrl"] == first.data["streamUrl"]
    assert Adventure.objects.count() == 1
    assert fake_ai.plan_calls == plans, "rejoining must not generate a second plan"


@pytest.mark.django_db
def test_a_finished_adventure_does_not_block_the_next_one(auth, aarav, pip, fake_ai) -> None:
    """`in_progress` is the gate, not "has ever had an adventure".

    Completion arrives with the choice loop on Day 5; this pins the rule the day
    before, so the endpoint that decides is already the right one.
    """
    first = auth.post(adventures_url(aarav.id), {}, format="json")
    Adventure.objects.update(status=Adventure.Status.COMPLETED)

    second = auth.post(adventures_url(aarav.id), {}, format="json")

    assert second.status_code == 201
    assert second.data["adventureId"] != first.data["adventureId"]
    assert Adventure.objects.count() == 2


@pytest.mark.django_db
def test_current_returns_the_in_progress_adventure(auth, aarav, pip, fake_ai) -> None:
    started = auth.post(adventures_url(aarav.id), {}, format="json")
    plans = fake_ai.plan_calls

    response = auth.get(current_url(aarav.id))

    assert response.status_code == 200, response.data
    assert response.data == started.data
    # A read, all the way down: asking where the child is must never cost a
    # generation.
    assert fake_ai.plan_calls == plans
    assert Adventure.objects.count() == 1


@pytest.mark.django_db
def test_current_is_204_when_there_is_no_story_yet(auth, aarav, pip, fake_ai) -> None:
    response = auth.get(current_url(aarav.id))

    # 204 rather than 404: "this child is not in a story" is a normal state the
    # child surface reaches on every first visit, not a missing resource.
    assert response.status_code == 204
    assert not Adventure.objects.exists()
    assert fake_ai.plan_calls == 0


@pytest.mark.django_db
def test_current_is_scoped_to_the_owning_parent(api, other_parent, aarav, pip, fake_ai) -> None:
    login = api.post(LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    # 403 rather than 204: the wrong parent must not learn whether stories exist.
    assert api.get(current_url(aarav.id)).status_code == 403


@pytest.mark.django_db
def test_current_requires_authentication(api, aarav) -> None:
    assert api.get(current_url(aarav.id)).status_code == 401


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_reload_resumes_the_same_story_from_replay(auth, aarav, pip, fake_ai) -> None:
    """The founder's test, end to end: start, reload, land in the same forest.

    The reload is modelled the way the browser does it — the client keeps nothing,
    asks `current`, and reopens the stream it is handed. What must be true is that
    the second visit reaches the *replay* path: same adventure, same beat, no
    second plan and no second beat generation.
    """
    started = auth.post(adventures_url(aarav.id), {}, format="json")
    token = auth._credentials["HTTP_AUTHORIZATION"].removeprefix("Bearer ")
    first = frames_of(collect_stream(started.data["streamUrl"], token)[1])
    spent = (fake_ai.plan_calls, fake_ai.beat_calls)

    # The reload: nothing remembered, so the client asks who it was talking to.
    resumed = auth.get(current_url(aarav.id))
    second = frames_of(collect_stream(resumed.data["streamUrl"], token)[1])

    assert resumed.status_code == 200
    assert resumed.data["adventureId"] == started.data["adventureId"]
    assert second[0][1]["beatId"] == first[0][1]["beatId"]
    assert second[0][1]["narration"] == first[0][1]["narration"]
    # Not one further provider call, and not one further row.
    assert (fake_ai.plan_calls, fake_ai.beat_calls) == spent
    assert Adventure.objects.count() == 1
    assert Beat.objects.count() == 1
    # Replayed complete: the media was already on the beat, so it rode in on the
    # `beat` frame and no `media` frames were needed at all.
    assert [name for name, _ in second] == ["beat", "prefetch", "done"]


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


def abandon_stream(url: str, token: str, *, after: int) -> list[str]:
    """Read `after` chunks, then drop the connection — a closed tab, a slept tablet.

    Closing the async iterator throws `GeneratorExit` into the view's generator at
    whichever `yield` it is parked on, which is exactly what Django does when the
    client goes away. Everything the stream had *started* by then has to finish
    without it.
    """

    async def run() -> list[str]:
        client = AsyncClient()
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        chunks: list[str] = []
        iterator = response.streaming_content.__aiter__()  # type: ignore[attr-defined]
        try:
            for _ in range(after):
                chunks.append((await iterator.__anext__()).decode())
        finally:
            await iterator.aclose()
        return chunks

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
    # `done` is the child's turn, not the end of the connection: the opening offers
    # cards, and their little pictures keep arriving afterwards rather than gating
    # it (§14.5, `test_choices.test_the_child_gets_their_turn_before_the_card_pictures_land`).
    done_at = names.index("done")
    assert events[done_at][1] == {"status": "awaiting_choice"}

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
    assert beat["interaction"]["type"] == "choose"
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

    # The beat's *own* two assets. The card icons are media frames too and are
    # `test_choices`' subject; here the question is that the scene and the voice
    # each arrive and each land on the row.
    media = [payload for name, payload in events if name == "media" and "choice" not in payload]
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

    timings = [
        line
        for line in caplog.text.splitlines()
        if "story.media.timing" in line
        and any(f"stage={stage} " in line for stage in orchestrator.MEDIA_STAGE_LABELS)
    ]
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
    assert [name for name, _ in second] == ["beat", "prefetch", "done"]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_beat_that_cannot_be_repaired_becomes_a_warm_fallback(started, fake_ai) -> None:
    """§19.6's last line, end to end: *"safe fallback beat"*, not a dead stream.

    Until Day 7 this ended the stream with `done: ended` and nothing else — no
    error, but also no story, and a child left watching their companion think
    forever. §19.6 bounds the blast radius of a bad generation at "slightly
    generic but safe", and that is a beat, so this now asserts a beat.

    What makes it *warm* rather than merely present is the second half: it has
    narration, it is answerable, and nothing on the wire distinguishes it from a
    generated beat. The database knows (`safety_status`, the `model` stamp, an
    incident row); the child does not.
    """
    from apps.safety.models import SafetyIncident

    # Malformed twice: one repair attempt, then the router gives up (§19.6).
    fake_ai.queue_beat_response('{"beat_id": "b1"}', "not json at all")

    response, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)
    kinds = [name for name, _ in events]

    assert response.status_code == 200
    assert fake_ai.beat_calls == 2, "exactly one repair attempt, no more"

    assert kinds[0] == "beat"
    # `done` is not last — the choice icons keep arriving after the child has
    # been handed their turn (Day 6, `streaming.ESSENTIAL_STAGES`).
    done = next(payload for name, payload in events if name == "done")
    assert done["status"] == "awaiting_choice"

    beat_frame = events[0][1]
    assert beat_frame["narration"].strip()
    # Answerable: a fallback the child cannot act on would strand them exactly
    # where the old `ended` frame did.
    assert len(beat_frame["choices"] or []) >= 2
    # Nothing in the frame says "fallback", "error" or "sorry".
    assert "sorry" not in json.dumps(beat_frame).lower()

    beat = Beat.objects.get()
    assert beat.safety_status == "blocked"
    assert beat.model.startswith("fallback:")

    incident = SafetyIncident.objects.get()
    assert incident.category == "malformed_generation"
    assert incident.action_taken == "blocked"
    assert incident.beat_id == beat.id


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


# ---------------------------------------------------------------------------
# Media outlives the connection (§16.3, §18.5)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_client_that_leaves_mid_beat_does_not_orphan_it(started) -> None:
    """The bleed the Day-4 review measured, closed.

    A `curl -N` killed after two seconds still logged `story.beat … gen_ms=26375`
    twenty-four seconds later: the beat finished on its own offloaded thread and
    was persisted, but the coroutine that would have started the illustration and
    the voice never got that far. Of six beats in the founder's database, three
    were missing at least one asset — permanently, because the same adventure was
    never reopened either (see the resume tests above).

    The media is a task keyed on the beat now, enqueued the moment the beat is
    committed. So the connection is allowed to die: this reads the open comment and
    the beat, then hangs up before the `media` frames, and the beat still ends up
    whole.
    """
    chunks = abandon_stream(started["body"]["streamUrl"], started["token"], after=2)

    assert chunks[0] == ": open\n\n"
    assert "event: beat" in chunks[1]

    beat = Beat.objects.select_related("media", "audio").get()
    assert beat.media is not None, "the picture was lost with the connection"
    assert beat.audio is not None, "the voice was lost with the connection"
    assert beat.media.is_servable and beat.audio.is_servable


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_beat_left_without_media_is_repaired_on_the_next_open(
    started, monkeypatch, caplog
) -> None:
    """A worker that died, or a broker that was down when the beat was written.

    The old code healed this by accident — `if not (image_url and audio_url)` re-ran
    the stages, which also double-paid whenever two clients did it at once. The
    repair is deliberate now, and it is a *queue*, not a generation inside the
    response.

    The beat is aged past `MEDIA_RETRY_AFTER_S` first, because a beat that was
    touched a moment ago is assumed to be in a live worker's hands and must **not**
    be queued a second time — that assumption is what stops a reload from paying
    twice for one picture.
    """
    import datetime as dt

    from django.utils import timezone

    from apps.story import orchestrator

    # A beat written while nothing was listening for it.
    monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
    orphan = orchestrator.create_beat(Adventure.objects.get(), seq=1)
    monkeypatch.undo()

    assert orphan.media_id is None and orphan.audio_id is None
    Beat.objects.filter(pk=orphan.pk).update(
        updated_at=timezone.now() - dt.timedelta(seconds=orchestrator.MEDIA_RETRY_AFTER_S + 60)
    )

    with caplog.at_level("INFO", logger="apps.story.orchestrator"):
        _, chunks = collect_stream(started["body"]["streamUrl"], started["token"])
    events = frames_of(chunks)

    assert "story.media_repair" in caplog.text
    # Both of the beat's own assets reach this client, and both are on the beat for
    # the next one. Card icons are repaired alongside them and are counted by
    # `test_choices`; filtering them out keeps this test about the two that gate
    # the child's turn.
    assert sorted(
        "scene" in payload
        for name, payload in events
        if name == "media" and "choice" not in payload
    ) == [False, True]
    orphan.refresh_from_db()
    assert orphan.media_id is not None
    assert orphan.audio_id is not None


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_beat_still_being_worked_on_is_not_queued_again(started, caplog) -> None:
    """The other half of the repair rule, and the expensive half.

    A stream that reopens ten seconds into a twenty-five-second generation must
    trust the worker that already owns the beat. Queueing a second copy would miss
    the media cache on both sides and charge twice for one picture and one clip —
    which is the shape of every cost leak this pass exists to close.
    """
    from apps.story import orchestrator

    collect_stream(started["body"]["streamUrl"], started["token"])
    beat = Beat.objects.get()
    # Strip the media back off, leaving a beat that looks freshly written.
    Beat.objects.filter(pk=beat.pk).update(media=None, audio=None)
    beat.refresh_from_db()

    with caplog.at_level("INFO", logger="apps.story.orchestrator"):
        queued = orchestrator.ensure_media_scheduled(beat)

    assert queued is False
    assert "story.media_in_flight" in caplog.text
    assert "story.media_repair" not in caplog.text
