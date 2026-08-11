"""The choice loop, end to end (PRODUCT.md §8.5, §8.6, §8.8, §18.6).

The centrepiece is `test_a_whole_adventure_plays_to_a_warm_close`, which plays a
scripted adventure from the opening to the ending the way a child does — open the
stream, read the beat, POST the choice, open the stream again — making different
choices on the way. Everything else in this file is one property of that loop
pinned on its own so that a failure says *which* property broke.

The stream is driven through Django's `AsyncClient` for the reasons
`test_adventures.py` sets out: the view is async and must stay so, and frames are
collected one at a time because "arrived incrementally" is what the endpoint is
for. `transaction=True` for the same reason too — the media stages run on their
own threads with their own connections.
"""

import datetime
from typing import Any

import pytest

from apps.companion.models import Companion
from apps.companion.service import create_companion
from apps.profiles.models import Child
from apps.story import orchestrator
from apps.story.models import Adventure, Beat, Choice, PlaySession
from apps.story.summary import SUMMARY_MAX_CHARS
from tests.conftest import LOGIN_URL, PASSWORD
from tests.test_adventures import collect_stream, frames_of


def adventures_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/adventures"


def choice_url(adventure_id: Any) -> str:
    return f"/api/v1/adventures/{adventure_id}/choice"


def end_url(adventure_id: Any) -> str:
    return f"/api/v1/adventures/{adventure_id}/end"


@pytest.fixture(autouse=True)
def media_worker(settings) -> None:
    """Run the beat-media and advance tasks inline — the suite has no worker.

    Eager mode still goes through the real tasks, so the path under test is the
    real one and only the transport is short-circuited. It does mean the prefetch
    lands *synchronously*, which is stricter than production rather than weaker:
    every assertion about a beat being replayed rather than regenerated has to hold
    with the prefetch having definitely run.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True


@pytest.fixture
def aarav(parent, consent) -> Child:
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
    companion.visual_descriptor = "a small round friendly dragon with warm ember scales"
    companion.save(update_fields=["visual_descriptor"])
    return companion


@pytest.fixture
def started(auth, aarav, pip, fake_ai) -> dict[str, Any]:
    response = auth.post(adventures_url(aarav.id), {}, format="json")
    assert response.status_code == 201, response.data
    return {
        "body": response.data,
        "token": auth._credentials["HTTP_AUTHORIZATION"].removeprefix("Bearer "),
        "child": aarav,
        "adventure": Adventure.objects.get(id=response.data["adventureId"]),
    }


# ---------------------------------------------------------------------------
# Playing one beat: read the stream, answer, read the next
# ---------------------------------------------------------------------------
def read_beat(stream_url: str, token: str) -> tuple[dict[str, Any], str]:
    """Open the stream and return `(the beat frame, the done status)`.

    This is the client's whole loop, minus the rendering: one open delivers one
    beat and ends, and the choice goes back over an ordinary POST (§18.6).
    """
    events = frames_of(collect_stream(stream_url, token)[1])
    beat = next(payload for name, payload in events if name == "beat")
    done = next(payload for name, payload in events if name == "done")
    return beat, done["status"]


def answer(auth, adventure_id: Any, beat: dict[str, Any], *, option: int = 0, **extra: Any) -> Any:
    """Send back what the child did, picking `option` when the beat offers any."""
    options = beat.get("choices") or []
    key = options[option % len(options)]["key"] if options else "continue"
    return auth.post(
        choice_url(adventure_id),
        {"beatId": beat["beatId"], "choiceKey": key, "latencyMs": 2400, **extra},
        format="json",
    )


def play_to_decision(auth, adventure_id: Any, stream_url: str, token: str) -> dict[str, Any]:
    """Play forward until the child is offered cards, and return that beat.

    Walked rather than indexed, because *which* beat is a decision point is the
    Learning Engine's from Day 10 (`orchestrator.learning_seqs`): the beats
    carrying the objective ask for a number instead, and a test that hard-coded
    "beat two" would be asserting about the curriculum rather than about choices.
    """
    for _ in range(8):
        beat, _ = read_beat(stream_url, token)
        if beat.get("choices"):
            return beat
        assert answer(auth, adventure_id, beat).status_code == 202
    raise AssertionError("the adventure never offered the child a choice")


def decision_beat_without_media(auth, adventure, stream_url, token, monkeypatch) -> Beat:
    """Play forward to the first decision point, written with its media suppressed.

    Which is what a beat whose media worker died looks like — the case both tests
    below repair. Celery is eager here (see the fixture at the top of this file),
    so the prefetch generates the *next* beat inside the choice POST; suppressing
    `schedule_beat_media` across that one call is what leaves it bare.
    """
    asks = set(orchestrator.learning_seqs(adventure))
    target = next(seq for seq in range(2, adventure.estimated_beats) if seq not in asks)

    for _ in range(target - 1):
        beat, _ = read_beat(stream_url, token)
        if beat["seq"] == target - 1:
            monkeypatch.setattr(orchestrator, "schedule_beat_media", lambda beat: None)
        assert answer(auth, adventure.id, beat).status_code == 202
    monkeypatch.undo()

    decision = Beat.objects.get(adventure=adventure, seq=target)
    assert decision.options, "the first beat after the objective's asks offers cards"
    return decision


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_whole_adventure_plays_to_a_warm_close(started, auth, fake_ai) -> None:
    """Milestone M1: a real story a child can play from start to finish.

    Plays the loop exactly as the client does — alternating between the options so
    the run makes genuinely *different* choices rather than the same one repeatedly
    — and asserts the four things that make it a story rather than a sequence of
    generations: it reaches the plan's length, every beat after the first reacts to
    what the child picked, the last one is warm and asks for nothing, and `end`
    closes both the adventure and the sitting it happened in.
    """
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]
    expected_beats = adventure.estimated_beats

    seen: list[dict[str, Any]] = []
    picked: list[str] = []
    for _turn in range(expected_beats + 1):
        beat, status = read_beat(stream_url, token)
        seen.append(beat)
        if status == "complete":
            break
        # Indexed by how many decision points we have *taken*, not by beat number:
        # from Day 10 the beats between them ask for a number instead of a pick
        # (`orchestrator.learning_seqs`), and indexing by position would land on
        # the same card at every fork and never actually branch.
        option = len(picked)
        if beat.get("choices"):
            picked.append(beat["choices"][option % len(beat["choices"])]["key"])
        assert answer(auth, adventure.id, beat, option=option).status_code == 202
    else:  # pragma: no cover - only reached if the loop never completes
        pytest.fail(f"the story never reached its close: {[b['seq'] for b in seen]}")

    assert [beat["seq"] for beat in seen] == list(range(1, expected_beats + 1))
    assert len(seen) >= 4, "an adventure must be a story, not an exchange"
    # More than one distinct branch actually taken, not the same card every time.
    assert len(set(picked)) >= 2, picked

    # Every beat after the opening names the choice that led to it (§8.5).
    for beat in seen[1:]:
        assert "Because you chose" in beat["narration"] or beat["isFinal"], beat["narration"]

    close = seen[-1]
    assert close["isFinal"] is True
    assert close["interaction"]["type"] == "none"
    assert close["choices"] is None, "the warm close must not offer a path to nowhere"
    assert "tomorrow" in close["narration"].lower(), "§8.8 wants a reason to come back"

    ended = auth.post(end_url(adventure.id), {}, format="json")
    assert ended.status_code == 200, ended.data
    assert ended.data["status"] == Adventure.Status.COMPLETED
    assert ended.data["beats"] == expected_beats

    adventure.refresh_from_db()
    assert adventure.completed_at is not None
    session = PlaySession.objects.get(pk=adventure.session_id)
    assert session.ended_at is not None
    assert session.duration_s is not None and session.duration_s >= 0
    assert session.ended_reason == PlaySession.EndedReason.COMPLETED


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_play_throughs_with_different_choices_diverge(started, auth, parent, fake_ai) -> None:
    """The founder's manual test, automated: choose differently, get a different
    story.

    A consequence that would have read the same after the other option is not a
    consequence (§8.5) — it is a continuation with a choice screen in front of it,
    which is the failure mode this whole day exists to avoid.
    """
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    decision = play_to_decision(auth, adventure.id, stream_url, token)

    options = decision["choices"]
    assert 2 <= len(options) <= 3, "§14.5: one decision at a time, two or three ways"

    # Take the *second* option here; the sibling below takes the first.
    auth.post(
        choice_url(adventure.id),
        {"beatId": decision["beatId"], "choiceKey": options[1]["key"], "latencyMs": 900},
        format="json",
    )
    took_second, _ = read_beat(stream_url, token)

    # A second child, same plan, first option.
    sibling = Child.objects.create(
        parent=parent,
        first_name="Nia",
        birthdate=datetime.date(datetime.date.today().year - 5, 1, 1),
        interests=["frogs", "rivers"],
    )
    create_companion(
        child=sibling,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )
    hers = auth.post(adventures_url(sibling.id), {}, format="json").data
    her_decision = play_to_decision(auth, hers["adventureId"], hers["streamUrl"], token)
    auth.post(
        choice_url(hers["adventureId"]),
        {"beatId": her_decision["beatId"], "choiceKey": options[0]["key"], "latencyMs": 900},
        format="json",
    )
    took_first, _ = read_beat(hers["streamUrl"], token)

    assert took_second["narration"] != took_first["narration"]
    assert options[1]["label"] in took_second["narration"]
    assert options[0]["label"] in took_first["narration"]


# ---------------------------------------------------------------------------
# Illustrated choices (§14.4)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_decision_beat_offers_illustrated_choices(started, auth) -> None:
    """Pictures, not a text list — a pre-reader cannot participate in a menu."""
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    play_to_decision(auth, adventure.id, stream_url, token)
    # Re-opened rather than read once. The card pictures deliberately arrive *after*
    # the child's turn is handed over (the test below), so the first frame for a
    # decision beat carries null icons by design; a replay is what a client that
    # comes back to an unanswered beat sees, and by then they have landed.
    decision, status = read_beat(stream_url, token)

    assert status == "awaiting_choice"
    assert 2 <= len(decision["choices"]) <= 3
    for option in decision["choices"]:
        assert option["key"] and option["label"]
        assert option["imageUrl"], f"{option['key']} has no picture on its card"

    # And the labels are *said*, in the same clip as the narration, because the
    # child cannot read them off the cards.
    beat = Beat.objects.get(id=decision["beatId"])
    line = orchestrator.spoken_line(beat)
    for option in decision["choices"]:
        assert option["label"] in line


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_choice_icons_arrive_as_their_own_media_frames(started, auth, monkeypatch) -> None:
    """The cards render before their pictures land, and update as they arrive.

    Holding the beat back until every icon is rendered would put three more image
    calls in front of the narration — the exact latency §18.5 streams to avoid.

    The decision beat is written with its media suppressed and then aged, which is
    what a beat whose worker died looks like; the next open repairs it and the
    icons come down the wire one at a time, exactly as they do on a first play.
    """
    import datetime as dt

    from django.utils import timezone

    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    decision = decision_beat_without_media(auth, adventure, stream_url, token, monkeypatch)
    Beat.objects.filter(pk=decision.pk).update(
        updated_at=timezone.now() - dt.timedelta(seconds=orchestrator.MEDIA_RETRY_AFTER_S + 60)
    )

    events = frames_of(collect_stream(stream_url, token)[1])

    beat = next(payload for name, payload in events if name == "beat")
    assert beat["seq"] == decision.seq
    assert all(option["imageUrl"] is None for option in beat["choices"])

    icons = [
        payload["choice"] for name, payload in events if name == "media" and "choice" in payload
    ]
    assert {icon["key"] for icon in icons} == {option["key"] for option in beat["choices"]}
    assert all(icon["imageUrl"] for icon in icons)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_child_gets_their_turn_before_the_card_pictures_land(
    started, auth, monkeypatch
) -> None:
    """`done` waits for the beat's own picture and voice, and for nothing else.

    A card renders without its icon; a child cannot act without the question. So
    gating their turn on three more illustrations would mean one slow icon leaving
    a four-year-old unable to answer — a spinner in everything but name (§14.5).
    """
    import datetime as dt

    from django.utils import timezone

    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    decision = decision_beat_without_media(auth, adventure, stream_url, token, monkeypatch)
    Beat.objects.filter(pk=decision.pk).update(
        updated_at=timezone.now() - dt.timedelta(seconds=orchestrator.MEDIA_RETRY_AFTER_S + 60)
    )

    names = [name for name, _ in frames_of(collect_stream(stream_url, token)[1])]

    assert names[-1] != "done", "the stream ended on the child's turn, icons and all"
    done_at = names.index("done")
    # The two assets the turn waits for arrived first; the icons came after it.
    assert names[:done_at].count("media") == len(orchestrator.MEDIA_STAGE_LABELS)
    assert "media" in names[done_at:]


# ---------------------------------------------------------------------------
# The choice row (§18.6, §10.4)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_choice_is_persisted_with_its_evidence(started, auth) -> None:
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    response = auth.post(
        choice_url(adventure.id),
        {
            "beatId": beat["beatId"],
            "choiceKey": "continue",
            "value": 3,
            "latencyMs": 2400,
            "retries": 1,
            "assisted": True,
        },
        format="json",
    )

    assert response.status_code == 202, response.data
    assert response.data["recorded"] is True
    assert response.data["streamUrl"] == started["body"]["streamUrl"]

    choice = Choice.objects.get(id=response.data["choiceId"])
    assert choice.chosen_key == "continue"
    assert choice.value == 3
    assert choice.latency_ms == 2400
    assert choice.retries == 1
    assert choice.assisted is True
    # A record of what happened, written synchronously so the 202 costs nothing.
    assert "Aarav chose" in choice.consequence_summary
    assert "helped them get there" in choice.consequence_summary


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_double_tap_records_one_choice_and_one_beat(started, auth, fake_ai) -> None:
    """A four-year-old submits twice by double-tapping a big forgiving target.

    Two rows would advance the story two beats at once and the child would watch a
    beat they never played — plus we would have paid for it.
    """
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])
    spent = fake_ai.beat_calls

    first = answer(auth, adventure.id, beat)
    second = answer(auth, adventure.id, beat)

    assert first.status_code == second.status_code == 202
    assert first.data["choiceId"] == second.data["choiceId"]
    assert first.data["recorded"] is True
    assert second.data["recorded"] is False
    assert Choice.objects.count() == 1
    # One consequence generated, not two.
    assert fake_ai.beat_calls - spent == 1
    assert Beat.objects.filter(adventure=adventure).count() == 2


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_an_unrecognised_answer_is_recorded_rather_than_rejected(started, auth) -> None:
    """There is no wrong answer to reject (§8.5).

    Nothing is validated against `interaction.expected`, so a child who counts four
    frogs where there were three is recorded as having said four and the story
    carries on warmly. A 400 here would be a fail state wearing a status code.
    """
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    response = auth.post(
        choice_url(adventure.id),
        {"beatId": beat["beatId"], "choiceKey": "not_an_option", "value": 4, "assisted": True},
        format="json",
    )

    assert response.status_code == 202
    consequence, status = read_beat(started["body"]["streamUrl"], started["token"])
    assert status == "awaiting_choice"
    # Scaffolded, warmly, and never told they were wrong.
    assert "Let's try it together" in consequence["narration"]
    assert not any(
        word in consequence["narration"].lower() for word in ("wrong", "sorry", "try again")
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_scaffolding_signal_reaches_the_next_beat_s_prompt(started, auth, fake_ai) -> None:
    """`assisted` is not just stored — it changes what the companion does next."""
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    auth.post(
        choice_url(adventure.id),
        {"beatId": beat["beatId"], "choiceKey": "continue", "retries": 2, "assisted": True},
        format="json",
    )

    system, user = fake_ai.prompts[-1]
    assert "The companion helped them get there." in system
    assert "Tries before settling on it: 3" in system
    # And the instruction that turns the signal into scaffolding rather than a mark.
    assert "Do not mention a mistake" in user


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_choice_starts_the_consequence_before_the_client_reconnects(started, auth) -> None:
    """The prefetch (§4.9): the beat is written by the time the stream reopens.

    This is what stands between a child tapping a card and twenty seconds of their
    friend thinking about it.
    """
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    answer(auth, adventure.id, beat)

    # Written by the task the choice enqueued, before anybody asked for it.
    assert Beat.objects.filter(adventure=adventure, seq=2).exists()


# ---------------------------------------------------------------------------
# Progress: which beat is the child on
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_prefetched_beat_does_not_skip_the_one_the_child_is_on(started, auth) -> None:
    """The bug a "last row" cursor would have: the child loses a beat to a reload.

    Beat two exists as soon as the choice on beat one is recorded. If "where the
    child is" meant "the newest beat", a reload before they answered beat two would
    jump them to beat three — a beat they never played, and one they paid for.
    """
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    first, _ = read_beat(stream_url, token)
    answer(auth, adventure.id, first)
    second, _ = read_beat(stream_url, token)
    assert second["seq"] == 2

    # The reload, before answering beat two.
    again, _ = read_beat(stream_url, token)
    assert again["beatId"] == second["beatId"]
    assert again["seq"] == 2


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_reopening_after_a_choice_replays_rather_than_regenerates(started, auth, fake_ai) -> None:
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    first, _ = read_beat(stream_url, token)
    answer(auth, adventure.id, first)
    spent = fake_ai.beat_calls

    second, _ = read_beat(stream_url, token)
    again, _ = read_beat(stream_url, token)

    assert again["beatId"] == second["beatId"]
    # The prefetch already paid for beat two; neither open pays again.
    assert fake_ai.beat_calls == spent


# ---------------------------------------------------------------------------
# Ending (§13.6)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_ending_early_records_the_exit_without_calling_it_a_failure(started, auth) -> None:
    adventure = started["adventure"]
    read_beat(started["body"]["streamUrl"], started["token"])

    response = auth.post(end_url(adventure.id), {"reason": "exit"}, format="json")

    assert response.status_code == 200
    adventure.refresh_from_db()
    # Abandoned, not failed — the distinction the parent report reads (§13.6).
    assert adventure.status == Adventure.Status.ABANDONED
    session = PlaySession.objects.get(pk=adventure.session_id)
    assert session.ended_reason == PlaySession.EndedReason.EXIT


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_ending_twice_does_not_rewrite_when_the_sitting_stopped(started, auth) -> None:
    adventure = started["adventure"]
    first = auth.post(end_url(adventure.id), {}, format="json")
    session = PlaySession.objects.get(pk=adventure.session_id)
    stamped = session.ended_at

    second = auth.post(end_url(adventure.id), {}, format="json")

    assert first.status_code == second.status_code == 200
    assert second.data["completedAt"] == first.data["completedAt"]
    session.refresh_from_db()
    assert session.ended_at == stamped


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_finished_adventure_frees_the_child_to_start_another(started, auth) -> None:
    """What `end` is *for*, from the child's side: play again."""
    adventure = started["adventure"]
    auth.post(end_url(adventure.id), {}, format="json")

    again = auth.post(adventures_url(started["child"].id), {}, format="json")

    assert again.status_code == 201
    assert again.data["adventureId"] != str(adventure.id)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_another_parent_cannot_answer_or_end_this_story(started, api, other_parent) -> None:
    login = api.post(LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    submitted = api.post(
        choice_url(adventure.id),
        {"beatId": beat["beatId"], "choiceKey": "continue"},
        format="json",
    )
    assert submitted.status_code == 403
    assert api.post(end_url(adventure.id), {}, format="json").status_code == 403
    assert not Choice.objects.exists()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_beat_from_another_story_cannot_be_answered_here(started, auth, parent, fake_ai) -> None:
    """The beat id is scoped to the adventure in the URL, so it cannot be used to
    write a choice into somebody else's story — or into this child's other one."""
    sibling = Child.objects.create(
        parent=parent,
        first_name="Nia",
        birthdate=datetime.date(datetime.date.today().year - 4, 2, 2),
    )
    create_companion(
        child=sibling,
        archetype="dragon",
        name="Ember",
        palette="ember",
        personality_preset="brave_spark",
    )
    theirs = auth.post(adventures_url(sibling.id), {}, format="json").data
    their_beat, _ = read_beat(theirs["streamUrl"], started["token"])

    response = auth.post(
        choice_url(started["adventure"].id),
        {"beatId": their_beat["beatId"], "choiceKey": "continue"},
        format="json",
    )

    assert response.status_code == 404
    assert not Choice.objects.filter(beat_id=their_beat["beatId"]).exists()


# ---------------------------------------------------------------------------
# Continuity stays cheap (§8.6)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_prompt_does_not_grow_with_the_story(started, auth, fake_ai) -> None:
    """The property compaction exists for, measured on the real prompts.

    Without it the summary is every beat so far pasted into every request: the
    prompt grows linearly, and the warm close — the beat the child remembers — is
    the most expensive thing we generate. The assertion is on the *shape* of the
    growth, not on an exact size, because the fixed layers (identity, safety
    policy, the output schema) dominate and would hide a small regression either
    way; what it catches is the one that matters, a summary that keeps everything.
    """
    adventure = started["adventure"]
    stream_url, token = started["body"]["streamUrl"], started["token"]

    sizes: list[int] = []
    for _ in range(adventure.estimated_beats):
        beat, status = read_beat(stream_url, token)
        sizes.append(len(fake_ai.prompts[-1][0]) + len(fake_ai.prompts[-1][1]))
        if status == "complete":
            break
        answer(auth, adventure.id, beat)

    assert len(sizes) >= 4
    # The last beat's prompt is not meaningfully bigger than the second's. Linear
    # growth over five beats would blow through this by a wide margin.
    assert sizes[-1] <= sizes[1] * 1.2, sizes

    adventure.refresh_from_db()
    assert len(adventure.running_summary) <= SUMMARY_MAX_CHARS
    # And it is a real summary, not an empty one that would pass the bound for the
    # wrong reason.
    assert "Aarav chose" in adventure.running_summary


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_endpoint_that_receives_a_disclosure_escalates_before_it_answers(
    started, auth, caplog
) -> None:
    """§13.10's ingress, at the surface the child's words actually arrive on.

    Day 7's review probed here and found nothing: the distress check lived inside
    `create_beat`, so it only ran when a next beat happened to be generated. This
    asserts the property that has to hold whatever the story does next — by the
    time the `202` is written, the incident exists and the parent-notify path has
    run. Everything downstream (the close, the child leaving, a prefetched beat) is
    then irrelevant to whether the alarm was raised, which is the whole point.
    """
    from apps.safety.models import Action, SafetyIncident, Severity

    adventure = started["adventure"]
    beat, _ = read_beat(started["body"]["streamUrl"], started["token"])

    with caplog.at_level("ERROR"):
        response = auth.post(
            choice_url(adventure.id),
            {"beatId": beat["beatId"], "choiceKey": "continue", "value": "my dad hits me"},
            format="json",
        )

    assert response.status_code == 202, response.data

    incident = SafetyIncident.objects.get(category="child_distress")
    assert incident.severity == Severity.CRITICAL
    assert incident.action_taken == Action.ESCALATED
    assert incident.escalated is True
    assert incident.detail["surface"] == "choice_submitted"
    assert "safety.parent_notify" in caplog.text
    # The incident is a record of what fired, never of what the child said (§13.8).
    assert "dad" not in str(incident.detail).lower()

    # And the child is told nothing at all — the response is the same `202` any
    # other tap gets, with no hint that anything happened (§13.10).
    assert set(response.data) == {"choiceId", "beatId", "recorded", "streamUrl"}
    assert response.data["recorded"] is True
