"""Session and weekly reports (PRODUCT.md §12.1, §12.2, §13.9).

Criterion 5 is the one this file is really about: *report content is grounded in
real session data — no fabrication*. Everything else here is in service of it.

So most of these tests do the same thing: play a real adventure, generate the
report, and then check each claim in it against the beats and choices that were
actually written. A test that asserted a report was non-empty would pass against a
report that was entirely invented, which is the failure §13.9 names.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from django.utils import timezone

from apps.analytics import prompts, reports, skills
from apps.analytics.models import HighlightSource, SessionReport
from apps.analytics.tests.conftest import finish, play, spoke
from apps.learning.demands import demand_for
from apps.story.models import Beat, Choice, PlaySession

pytestmark = pytest.mark.django_db


def _snapshot_last_week(
    child: Any,
    like: SessionReport,
    *,
    mastery: float | None = None,
    status: str | None = None,
) -> SessionReport:
    """A report dated last week, carrying the same objective as `like`.

    Written rather than played, deliberately. §10.3's selector interleaves domains,
    so playing two sittings in a row produces two *different* objectives — which
    makes a played baseline useless for any test about how one objective moved. The
    thing under test in those tests is the comparison, so the code is pinned and
    the sitting is not.
    """
    entry = dict(like.skills[0])
    if mastery is not None:
        entry["mastery"] = mastery
    if status is not None:
        entry["status"] = status
    return SessionReport.objects.create(
        child=child,
        session_key=uuid.uuid4(),
        occurred_at=timezone.now() - datetime.timedelta(days=8),
        ended_reason="completed",
        skills=[entry],
    )


def _snapshot(child: Any, *, days_ago: int, entries: list[dict[str, Any]]) -> SessionReport:
    """A report dated `days_ago`, carrying exactly these skill entries.

    The general form of `_snapshot_last_week`, for the tests that need a *history*
    rather than a single baseline — specifically the ones about an objective the
    child practised weeks ago with other objectives in between, which is the
    ordinary shape of a real child's history and the one §10.3's interleaving
    guarantees.
    """
    return SessionReport.objects.create(
        child=child,
        session_key=uuid.uuid4(),
        occurred_at=timezone.now() - datetime.timedelta(days=days_ago),
        ended_reason="completed",
        skills=entries,
    )


def _entry(code: str, *, mastery: float, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "domain": "numeracy",
        "milestone": "Counting and quantity",
        "asks": 2,
        "unaided": 2,
        "assisted": 0,
        "retried": 0,
        "mastery": mastery,
        "status": status,
        **extra,
    }


def _row_for(weekly: reports.WeeklyReport, code: str) -> dict[str, Any]:
    return next(row for row in weekly.skills if row["objective"] == code)


# ---------------------------------------------------------------------------
# Criterion 1: a report generates, and it is specific
# ---------------------------------------------------------------------------
def test_a_played_sitting_produces_a_report_of_that_sitting(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.child_id == aarav.pk
    assert report.session_key == session.pk
    assert report.beats == Beat.objects.filter(adventure=adventure).count()
    assert report.adventure_key == adventure.pk
    assert report.ended_reason == "completed"


def test_where_they_went_is_where_the_plan_actually_sent_them(aarav, fake_ai) -> None:
    """Not re-derived from the interest graph — read off the plan the story used."""
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.adventure_title == adventure.title
    assert report.world_name == adventure.plan["world_name"]
    assert report.place_name == adventure.plan["place"]


def test_the_key_choice_quotes_a_card_the_child_was_actually_offered(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.key_choice, "a five-beat story branches at least once"
    offered = {
        str(option.get("label"))
        for beat in Beat.objects.filter(adventure=adventure)
        for option in beat.options
    }
    assert any(label and label in report.key_choice for label in offered)


def test_the_key_choice_is_a_card_the_child_took_and_not_one_they_left(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)
    report = reports.generate_session_report(session)

    taken = set()
    for choice in Choice.objects.filter(adventure=adventure).select_related("beat"):
        option = choice.beat.option(choice.chosen_key) or {}
        if label := str(option.get("label") or ""):
            taken.add(label)

    assert any(label in report.key_choice for label in taken)


def test_the_key_choice_says_what_the_story_did_and_not_what_we_told_it_to_do(
    aarav, fake_ai
) -> None:
    """`Choice.consequence_summary` is written for the next generation, not a parent.

    It is in machine register and it carries `Beat.next_hint` — our private
    instruction to the writer. Rendered on the report page it read *"…The beat's
    own steer for what follows: if the child counts -> cross the river"*, which is
    the product's seams showing on the one surface that is a recap.
    """
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.key_choice
    assert "steer for what follows" not in report.key_choice
    assert "At beat" not in report.key_choice
    for beat in Beat.objects.filter(adventure=adventure):
        assert not beat.next_hint or beat.next_hint not in report.key_choice
    # What came of it is the story's own next line, so it is quotable from a beat.
    narrations = " ".join(
        beat.narration for beat in Beat.objects.filter(adventure=adventure).order_by("seq")
    )
    said = report.key_choice.split("What happened next: ")[-1].rstrip("…")
    assert said in narrations


def test_a_sitting_that_never_branched_claims_no_key_choice(aarav, fake_ai) -> None:
    """Silence rather than promoting a "carry on" tap into a decision."""
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    beat = orchestrator.create_beat(adventure, seq=1)
    for option in list(beat.options):
        del option
    beat.interaction = {"type": "tap", "prompt_audio": "tap to carry on", "choices": []}
    beat.save(update_fields=["interaction"])
    orchestrator.record_choice(beat, chosen_key="continue")
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.key_choice == ""


# ---------------------------------------------------------------------------
# The objective, and how it went (§12.1, §10.5)
# ---------------------------------------------------------------------------
def test_the_objective_reported_is_the_one_the_story_practised(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.objective_code == adventure.primary_objective_id
    assert report.milestone_name
    # §10.6: the parent hears the milestone, never the code.
    assert report.objective_code not in report.how_it_went
    assert report.milestone_name != report.objective_code


def test_how_it_went_names_the_ask_that_was_actually_made(aarav, fake_ai) -> None:
    """The number in the report is the number that was in the picture.

    After the Day-10 fix the stamped difficulty describes the *delivered* ask, so
    reconstructing the demand from it gives what the child saw — which is the only
    version of this sentence that is true when an objective's ceiling has clamped
    the ladder down.
    """
    adventure = play(aarav, correct=True)
    session = finish(adventure)
    report = reports.generate_session_report(session)

    asked = []
    for beat in Beat.objects.filter(adventure=adventure).order_by("seq"):
        learning = beat.learning or {}
        if not learning.get("objective_id") or learning["objective_id"] == "none":
            continue
        if (beat.interaction or {}).get("expected") is None:
            continue
        demand = demand_for(
            learning["objective_id"], "numeracy", float(learning.get("difficulty") or 0)
        )
        asked.append(demand.quantity)

    assert asked, "the story must have asked something"
    for quantity in asked:
        assert str(quantity) in report.how_it_went


def test_a_child_who_needed_a_hand_is_not_reported_as_having_done_it_alone(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True, assisted=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert "together" in report.how_it_went
    assert "on their own" not in report.how_it_went


def test_a_child_who_did_not_get_there_is_reported_without_a_failure_word(aarav, fake_ai) -> None:
    """§8.5: there are no wrong answers, and there is no rounding up either."""
    adventure = play(aarav, correct=False)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert "still new" in report.how_it_went
    for word in ("wrong", "incorrect", "failed", "behind"):
        assert word not in report.how_it_went.lower()


def test_the_skill_snapshot_counts_only_what_happened(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.skills, "a story with a learning beat must record it"
    entry = report.skills[0]
    answered = sum(
        1
        for beat in Beat.objects.filter(adventure=adventure)
        if (beat.learning or {}).get("objective_id") == entry["code"]
        and (beat.interaction or {}).get("expected") is not None
        and beat.choices.exists()
    )
    assert entry["asks"] == answered
    assert entry["unaided"] + entry["assisted"] <= entry["asks"]


# ---------------------------------------------------------------------------
# Criterion 5: the highlight is grounded, or it is not used (§13.9)
# ---------------------------------------------------------------------------
def test_an_ungrounded_sentence_is_rejected() -> None:
    specifics = {"Sparkle", "river", "3"}

    assert prompts.is_grounded("Aarav named the dragon Sparkle.", "Aarav", specifics)
    # Beautifully written, about nobody.
    assert not prompts.is_grounded(
        "Aarav showed wonderful imagination throughout.", "Aarav", specifics
    )
    # About the right thing, about the wrong child.
    assert not prompts.is_grounded("Nia chose the river.", "Aarav", specifics)


def test_a_common_word_cannot_ground_a_sentence() -> None:
    """Without `_UNGROUNDING`, "the" would be a specific and the gate a formality."""
    specifics = prompts.specifics_from("Follow the sparkling river")

    assert "the" not in {item.casefold() for item in specifics}
    assert not prompts.is_grounded("Aarav had the best time.", "Aarav", specifics)
    assert prompts.is_grounded("Aarav followed the river.", "Aarav", specifics)


def test_a_model_that_invents_a_moment_never_reaches_the_parent(
    aarav, fake_ai, monkeypatch
) -> None:
    def invent(child: Any, digest: str, specifics: set[str]) -> str:
        raise prompts.HighlightUnavailable("ungrounded")

    monkeypatch.setattr(reports.prompts, "highlight_for", invent)
    adventure = play(aarav, correct=True)
    spoke(adventure, "Sparkle")
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.highlight_source == HighlightSource.DATA
    assert "Sparkle" in report.highlight


def test_a_grounded_model_sentence_is_kept_and_labelled(aarav, fake_ai, monkeypatch) -> None:
    monkeypatch.setattr(
        reports.prompts,
        "highlight_for",
        lambda child, digest, specifics: "Aarav named the little frog Sparkle.",
    )
    adventure = play(aarav, correct=True)
    spoke(adventure, "Sparkle")
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.highlight_source == HighlightSource.MODEL
    assert report.highlight == "Aarav named the little frog Sparkle."


def test_a_quiet_sitting_says_so_rather_than_being_inflated(aarav, fake_ai) -> None:
    """§12.1: honest, never inflated. Most afternoons are ordinary."""
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    orchestrator.create_beat(adventure, seq=1)
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert report.highlight_source == HighlightSource.QUIET
    assert "quiet" in report.highlight.lower()
    for word in ("amazing", "wonderful", "incredible", "brilliant"):
        assert word not in report.highlight.lower()


def test_the_highlight_never_calls_a_provider_when_there_is_nothing_to_point_at(
    aarav, fake_ai
) -> None:
    """No specifics means no call. A model asked about an empty session invents."""
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    session = finish(adventure)

    reports.generate_session_report(session)

    assert fake_ai.highlight_calls == 0


def test_our_own_scenery_is_not_something_the_child_did(aarav, fake_ai, monkeypatch) -> None:
    """A moment has to be the child's (§12.1).

    The setting and the cast are ours. A sentence grounded only on them is a
    sentence about our own prose — and it used to pass the gate, be stored as
    `model`, and be published under a line telling the parent it had been checked
    against what actually happened.

    Asserted where the defect lived: on the specifics `_highlight` hands the model,
    which must contain nothing the child did not put there themselves.
    """
    seen: dict[str, set[str]] = {}

    def spy(child: Any, digest: str, specifics: set[str]) -> str:
        seen["specifics"] = specifics
        return f"{child.first_name} named the little frog Sparkle."

    monkeypatch.setattr(reports.prompts, "highlight_for", spy)
    adventure = play(aarav, correct=True)
    spoke(adventure, "Sparkle")
    session = finish(adventure)

    reports.generate_session_report(session)

    scenery: set[str] = set()
    for beat in Beat.objects.filter(adventure=adventure):
        scene = beat.scene or {}
        scenery.update(str(name) for name in (scene.get("characters_present") or []))
        if setting := str(scene.get("setting") or "").strip():
            scenery.add(setting)
    assert scenery, "the fixture must have scenery for this test to mean anything"
    assert not (scenery & seen["specifics"])

    # …and the gate still rejects a sentence that names only our own prose.
    assert not prompts.is_grounded(
        "Aarav loved the mossy hollow at dawn — full of wonder.", "Aarav", seen["specifics"]
    )
    assert prompts.is_grounded("Aarav named the frog Sparkle.", "Aarav", seen["specifics"])


def test_a_sitting_the_child_put_nothing_into_is_quiet_and_costs_nothing(aarav, fake_ai) -> None:
    """A scene with a setting and a cast in it is still a sitting with no moment."""
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(aarav)
    beat = orchestrator.create_beat(adventure, seq=1)
    assert (beat.scene or {}).get("setting"), "the fixture must have scenery to be worth testing"
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert fake_ai.highlight_calls == 0
    assert report.highlight_source == HighlightSource.QUIET


def test_the_generated_path_runs_end_to_end_against_a_provider(aarav, fake_ai) -> None:
    """The model branch, with nothing stubbed but the provider itself.

    Every other highlight test patches `highlight_for`, which proves the caller's
    behaviour and skips the prompt, the router, the schema and the grounding check.
    This one goes through all of them, so a change that broke the assembly would
    fail here rather than degrade quietly to the fallback in production.
    """
    adventure = play(aarav, correct=True)
    spoke(adventure, "Sparkle")
    session = finish(adventure)

    report = reports.generate_session_report(session)

    assert fake_ai.highlight_calls == 1
    assert report.highlight_source == HighlightSource.MODEL
    assert "Aarav" in report.highlight
    assert "Sparkle" in report.highlight


# ---------------------------------------------------------------------------
# Idempotency and the read-path fallback
# ---------------------------------------------------------------------------
def test_a_second_generation_returns_the_first_report(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    first = reports.generate_session_report(session)
    second = reports.generate_session_report(session)

    assert first.pk == second.pk
    assert SessionReport.objects.filter(session_key=session.pk).count() == 1


def test_asking_for_a_report_the_worker_never_wrote_writes_it(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)
    assert not SessionReport.objects.filter(session_key=session.pk).exists()

    report = reports.report_for_session(aarav, session.pk)

    assert report is not None
    assert SessionReport.objects.filter(session_key=session.pk).count() == 1


def test_a_sitting_still_in_progress_has_no_report(aarav, fake_ai) -> None:
    play(aarav, correct=True)
    session = PlaySession.objects.filter(child=aarav).first()
    assert session is not None and session.ended_at is None

    assert reports.report_for_session(aarav, session.pk) is None


def test_another_familys_sitting_is_not_reportable(aarav, nia, fake_ai) -> None:
    """Owner scoping, one layer below the endpoint that also enforces it."""
    adventure = play(aarav, correct=True)
    session = finish(adventure)

    assert reports.report_for_session(nia, session.pk) is None


# ---------------------------------------------------------------------------
# Criterion 2: the weekly report (§12.2, §18.7)
# ---------------------------------------------------------------------------
def test_the_weekly_report_has_the_shape_18_7_specifies(aarav, fake_ai) -> None:
    adventure = play(aarav, correct=True)
    session = finish(adventure)
    reports.generate_session_report(session)

    weekly = reports.generate_weekly_report(aarav)

    assert "/" in weekly.week
    assert weekly.sessions == 1
    assert weekly.completion_rate == 1.0
    assert weekly.highlight
    assert weekly.skills and {"domain", "objective", "movement", "status"} <= set(weekly.skills[0])
    assert weekly.interests_trend
    assert weekly.recommendation.endswith("?") or "not enough" in weekly.recommendation


def test_completion_is_counted_from_how_sittings_actually_ended(aarav, fake_ai) -> None:
    finished = finish(play(aarav, correct=True))
    reports.generate_session_report(finished)
    left = finish(play(aarav, correct=True), reason=PlaySession.EndedReason.EXIT)
    reports.generate_session_report(left)

    weekly = reports.generate_weekly_report(aarav)

    assert weekly.sessions == 2
    assert weekly.completion_rate == 0.5


def test_a_week_with_no_play_reports_nothing_rather_than_something(aarav) -> None:
    weekly = reports.generate_weekly_report(aarav)

    assert weekly.sessions == 0
    assert weekly.completion_rate == 0.0
    assert weekly.highlight == ""
    assert weekly.skills == []
    assert weekly.milestone is None


def test_movement_is_measured_against_a_stored_snapshot(aarav, fake_ai) -> None:
    """And says "first practised" when there is nothing to measure against.

    The baseline is written by hand rather than played, and the reason is §10.3:
    the selector interleaves domains, so two consecutive sittings deliberately
    practise *different* objectives and a played baseline would be about counting
    while this week is about colours. What is under test is the measurement, so
    the two snapshots are pinned to one code.
    """
    this_week = reports.generate_session_report(finish(play(aarav, correct=True)))
    code = this_week.skills[0]["code"]

    assert reports.generate_weekly_report(aarav).skills[0]["movement"] == (
        "first practised this week"
    )

    _snapshot_last_week(aarav, this_week, mastery=0.05)

    moved = _row_for(reports.generate_weekly_report(aarav), code)
    assert moved["movement"] == "moved on this week"


def test_a_milestone_already_in_hand_is_not_claimed_again(aarav, fake_ai) -> None:
    """§18.7's `milestone` is a claim about *this* week, or it is absent."""
    this_week = reports.generate_session_report(finish(play(aarav, correct=True)))
    this_week.skills = [{**entry, "status": skills.MASTERED} for entry in this_week.skills]
    this_week.save(update_fields=["skills"])
    _snapshot_last_week(aarav, this_week, status=skills.MASTERED)

    # Mastered before the week began, so this week reached nothing.
    assert reports.generate_weekly_report(aarav).milestone is None


def test_a_milestone_reached_this_week_is_reported_in_plain_language(aarav, fake_ai) -> None:
    this_week = reports.generate_session_report(finish(play(aarav, correct=True)))
    code = this_week.skills[0]["code"]
    this_week.skills = [{**entry, "status": skills.MASTERED} for entry in this_week.skills]
    this_week.save(update_fields=["skills"])
    _snapshot_last_week(aarav, this_week, status=skills.PRACTISING)

    milestone = reports.generate_weekly_report(aarav).milestone

    assert milestone is not None
    assert milestone["reached"] is True
    # §10.6: the parent hears the milestone, never the objective code.
    assert code not in milestone["name"]
    assert milestone["step"]


def test_a_skill_practised_weeks_ago_is_not_reported_as_first_practised(aarav) -> None:
    """The baseline is per objective, not "the last report before the week".

    §10.3 interleaves domains, so the most recent report before any given week is
    usually about a *different* objective. Reading that one row as the baseline
    for everything makes a skill the child has practised for a month read as
    brand new.
    """
    _snapshot(
        aarav, days_ago=21, entries=[_entry("count_to_3", mastery=0.55, status=skills.MASTERED)]
    )
    # …and in between, a sitting about something else entirely.
    _snapshot(
        aarav,
        days_ago=9,
        entries=[
            {
                "code": "name_colours",
                "domain": "vocabulary",
                "milestone": "Words for the world",
                "asks": 2,
                "mastery": 0.5,
                "status": skills.PRACTISING,
            }
        ],
    )
    _snapshot(
        aarav, days_ago=0, entries=[_entry("count_to_3", mastery=0.64, status=skills.MASTERED)]
    )

    movement = _row_for(reports.generate_weekly_report(aarav), "count_to_3")["movement"]

    assert movement != "first practised this week"
    assert movement == "moved on this week"


def test_a_milestone_crossed_weeks_ago_is_not_claimed_again_this_week(aarav) -> None:
    """The same defect, in the form that tells a parent something false.

    Mastered three weeks ago, practised again this week, with an unrelated sitting
    in between. Without a per-objective baseline the digest announces "a step
    reached" — every week, for as long as the child keeps touching it.
    """
    _snapshot(
        aarav, days_ago=21, entries=[_entry("count_to_3", mastery=0.64, status=skills.MASTERED)]
    )
    _snapshot(
        aarav,
        days_ago=9,
        entries=[
            {
                "code": "name_colours",
                "domain": "vocabulary",
                "milestone": "Words for the world",
                "asks": 2,
                "mastery": 0.5,
                "status": skills.PRACTISING,
            }
        ],
    )
    _snapshot(
        aarav, days_ago=0, entries=[_entry("count_to_3", mastery=0.64, status=skills.MASTERED)]
    )

    assert reports.generate_weekly_report(aarav).milestone is None


def test_the_weeks_highlight_prefers_the_most_specific_real_moment(
    aarav, fake_ai, monkeypatch
) -> None:
    quiet = reports.generate_session_report(finish(play(aarav, correct=True)))
    quiet.highlight_source = HighlightSource.QUIET
    quiet.highlight = "A quiet session."
    quiet.save(update_fields=["highlight_source", "highlight"])

    monkeypatch.setattr(
        reports.prompts,
        "highlight_for",
        lambda child, digest, specifics: "Aarav counted the frogs on his own.",
    )
    reports.generate_session_report(finish(play(aarav, correct=True)))

    assert reports.generate_weekly_report(aarav).highlight == (
        "Aarav counted the frogs on his own."
    )


def test_the_recommendation_names_something_this_child_actually_loves(aarav, fake_ai) -> None:
    """§12.4: low pressure, grounded, and the parent stays in control."""
    reports.generate_session_report(finish(play(aarav, correct=True)))

    weekly = reports.generate_weekly_report(aarav)

    assert weekly.interests_trend
    assert weekly.interests_trend[0] in weekly.recommendation
    assert "Aarav" in weekly.recommendation
    assert weekly.recommendation.rstrip().endswith("?")


def test_the_week_runs_monday_to_sunday(aarav) -> None:
    start, end = reports.week_bounds(datetime.date(2026, 8, 5))

    assert (start, end) == (datetime.date(2026, 8, 3), datetime.date(2026, 8, 9))
    assert reports.generate_weekly_report(aarav, datetime.date(2026, 8, 5)).week == (
        "2026-08-03/2026-08-09"
    )


# ---------------------------------------------------------------------------
# The interest trend (§11.7, §12.5)
# ---------------------------------------------------------------------------
def test_the_trend_says_nothing_until_there_is_a_history_to_say_it_from(aarav, fake_ai) -> None:
    reports.generate_session_report(finish(play(aarav, correct=True)))

    trend = reports.interest_trend(aarav)

    assert trend["now"], "the live graph always answers"
    assert trend["story"] == "", "one snapshot is not a trend"


def test_a_change_of_heart_becomes_a_sentence(aarav, fake_ai) -> None:
    """§11.7's "this month: space, then dinosaurs, now ocean", from real snapshots."""
    first = reports.generate_session_report(finish(play(aarav, correct=True)))
    first.occurred_at = timezone.now() - datetime.timedelta(days=20)
    first.interests = [{"topic": "dinosaur", "weight": 0.6}]
    first.save(update_fields=["occurred_at", "interests"])

    second = reports.generate_session_report(finish(play(aarav, correct=True)))
    second.interests = [{"topic": "ocean", "weight": 0.7}]
    second.save(update_fields=["interests"])

    trend = reports.interest_trend(aarav)

    assert trend["story"] == "Over the last few weeks: dinosaur, and now ocean."
    assert trend["earlier"] == ["dinosaur"]


# ---------------------------------------------------------------------------
# The wiring (§5.4)
# ---------------------------------------------------------------------------
def test_the_close_enqueues_the_report_and_a_broker_failure_costs_nothing_else(
    aarav, fake_ai, monkeypatch, django_capture_on_commit_callbacks, caplog
) -> None:
    from apps.story import orchestrator

    sent: list[str] = []
    monkeypatch.setattr(
        orchestrator.generate_report, "delay", lambda session_id: sent.append(session_id)
    )
    adventure = play(aarav, correct=True)

    with django_capture_on_commit_callbacks(execute=True):
        orchestrator.end_adventure(adventure)

    assert sent == [str(adventure.session_id)]

    # ...and a broker that refuses it leaves no trace on the child's path.
    def refuse(session_id: str) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(orchestrator.generate_report, "delay", refuse)
    second = play(aarav, correct=True)
    with caplog.at_level("ERROR", logger="apps.story.orchestrator"):
        with django_capture_on_commit_callbacks(execute=True):
            orchestrator.end_adventure(second)

    assert "story.report_enqueue_failed" in caplog.text
    assert second.status == "completed"
