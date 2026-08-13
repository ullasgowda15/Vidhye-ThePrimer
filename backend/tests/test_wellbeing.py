"""Anti-over-engagement, enforced (PRODUCT.md §13.6, §12.7).

Day 13's criteria 2 and 3:

> *Session cap ends the current adventure at a **warm** close; the daily time
> window is enforced.*
>
> *There are **zero** child-directed notifications/streaks/FOMO mechanics anywhere
> (prove by code inspection).*

The second one is unusual to write as a test and is the most important test in the
file, because it is the criterion that decays silently. Nobody will ever
deliberately add a streak; somebody will add "just a gentle reminder" in eight
months, and the only thing that catches it is a test that reads the source. So
`TestNoChildDirectedPullMechanics` greps the child-facing surfaces for the
vocabulary of engagement and fails on it — a lint rule for an ethical commitment
(§13.6, §2.6).
"""

import datetime
import re
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from apps.companion.models import Companion
from apps.profiles.models import Child
from apps.story import orchestrator, wellbeing
from apps.story.ai.prompts import story as story_prompts
from apps.story.models import Adventure, Beat, PlaySession

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]


def adventures_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/adventures"


def wellbeing_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/wellbeing"


@pytest.fixture
def child(parent, consent) -> Child:
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs"],
        session_cap_min=15,
    )


@pytest.fixture
def pip(child) -> Companion:
    return Companion.objects.create(
        child=child,
        archetype="dragon",
        name="Pip",
        visual_descriptor="a small round friendly dragon",
    )


def _sitting(adventure: Adventure) -> PlaySession:
    """The adventure's session, narrowed.

    `Adventure.session` is nullable because a retention job may prune it (§17.2),
    so every test that moves the clock has to say out loud that this one exists.
    """
    session = adventure.session
    assert session is not None
    return session


def _started_minutes_ago(session: PlaySession, minutes: float) -> None:
    """Move a session's start back. `auto_now_add` means an UPDATE, not a save."""
    PlaySession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - datetime.timedelta(minutes=minutes)
    )
    session.refresh_from_db()


# ---------------------------------------------------------------------------
# The sitting: several stories in a row are one sitting (§13.6)
# ---------------------------------------------------------------------------
class TestASittingSpansAdventures:
    """The part that would quietly not have worked.

    `create_adventure` opens a session per adventure and the warm close offers
    "Again!", so a cap read off one `PlaySession.started_at` restarts from zero
    every time a child taps for another story — never firing for the child it
    exists to protect.
    """

    def test_back_to_back_sessions_count_as_one_sitting(self, child) -> None:
        # A child who started 40 minutes ago, finished a story a minute ago, and
        # tapped "Again!". That gap is a child carrying on, not a break.
        first = PlaySession.objects.create(child=child)
        _started_minutes_ago(first, 40)
        PlaySession.objects.filter(pk=first.pk).update(
            ended_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        second = PlaySession.objects.create(child=child)

        # ~40 minutes ago, not "just now": the new session inherits the first's
        # start, which is the whole reason the cap can ever fire.
        assert wellbeing.sitting_minutes(child, second) > 35

    def test_a_real_break_starts_a_new_sitting(self, child) -> None:
        earlier = PlaySession.objects.create(child=child)
        _started_minutes_ago(earlier, 120)
        PlaySession.objects.filter(pk=earlier.pk).update(
            ended_at=timezone.now() - datetime.timedelta(minutes=110)
        )
        after_dinner = PlaySession.objects.create(child=child)

        # A child who went and did something else gets their afternoon back —
        # which is the outcome §9.5 actively wants and must not be penalised.
        assert wellbeing.sitting_minutes(child, after_dinner) < 1

    @pytest.mark.parametrize(
        ("gap_min", "joins"),
        [(wellbeing.SITTING_GAP_MIN - 1, True), (wellbeing.SITTING_GAP_MIN + 1, False)],
    )
    def test_the_threshold_is_where_it_says_it_is(self, child, gap_min, joins) -> None:
        """Either side of `SITTING_GAP_MIN`, not exactly on it.

        The boundary itself is genuinely ambiguous — the microseconds between
        writing `ended_at` and reading the clock decide it — and a test that
        asserted on that would be a test that failed on a slow morning. What
        matters is that a short break carries the sitting and a real one does not.
        """
        earlier = PlaySession.objects.create(child=child)
        _started_minutes_ago(earlier, 60)
        PlaySession.objects.filter(pk=earlier.pk).update(
            ended_at=timezone.now() - datetime.timedelta(minutes=gap_min)
        )
        later = PlaySession.objects.create(child=child)

        assert (wellbeing.sitting_minutes(child, later) > 55) is joins

    def test_no_session_row_is_never_treated_as_a_cap(self, child) -> None:
        # A sitting we cannot measure is not a sitting we may cut short on a guess.
        assert wellbeing.sitting_minutes(child, None) == 0.0
        assert not wellbeing.cap_reached(child, None)


# ---------------------------------------------------------------------------
# Criterion 2a: the cap ends the story at a warm close
# ---------------------------------------------------------------------------
class TestTheCapLandsTheStory:
    def test_a_sitting_under_the_cap_is_not_winding_down(self, child, pip, fake_ai) -> None:
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)

        assert not wellbeing.winding_down(Adventure.objects.get(pk=adventure.pk))

    def test_past_the_cap_the_next_beat_becomes_the_close(self, child, pip, fake_ai) -> None:
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)

        fresh = Adventure.objects.get(pk=adventure.pk)
        assert wellbeing.winding_down(fresh)
        # Beat 2 of a five-to-seven beat plan. Without the cap this would be nowhere near
        # the end — which is exactly what makes it the assertion worth having.
        assert orchestrator.is_final_seq(fresh, 2)

    def test_the_close_is_warm_and_asks_the_child_for_nothing(self, child, pip, fake_ai) -> None:
        """The whole ethical content of the feature: landed, never cut off."""
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)

        close = orchestrator.create_beat(Adventure.objects.get(pk=adventure.pk), seq=2)

        assert close.narration, "a cap must produce a beat, never a blank screen"
        # §8.8: the close asks for nothing. A child handed two choice cards under a
        # goodbye would tap one and watch the story end on what looks like a mistake.
        assert close.options == []
        assert (close.interaction or {}).get("type") == "none"
        assert close.safety_status != Beat.SafetyStatus.BLOCKED

    def test_the_close_prompt_carries_the_time_to_go_tone_rule(self, child, pip, fake_ai) -> None:
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)

        context = orchestrator.build_beat_context(Adventure.objects.get(pk=adventure.pk), seq=2)

        # Both rules, not one: the tone note *plus* the landing instructions. A beat
        # told to say goodbye without being told to resolve the story is the failure
        # the cap exists to avoid.
        assert story_prompts.FINAL_BEAT_RULE in context["user"]
        assert story_prompts.CAP_CLOSE_RULE in context["user"]

    def test_a_natural_ending_does_not_get_the_cap_rule(self, child, pip, fake_ai) -> None:
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)

        last = orchestrator.total_beats(adventure)
        context = orchestrator.build_beat_context(adventure, seq=last)

        assert story_prompts.FINAL_BEAT_RULE in context["user"]
        assert story_prompts.CAP_CLOSE_RULE not in context["user"]

    def test_the_cap_never_fires_before_the_first_beat_exists(self, child, pip, fake_ai) -> None:
        """A child who taps one minute after the cap gets one short story, not a
        single beat that is entirely a goodbye — which would read as a refusal."""
        session = PlaySession.objects.create(child=child)
        _started_minutes_ago(session, 40)
        adventure = orchestrator.create_adventure(child, session=session)

        assert not wellbeing.winding_down(adventure)
        assert not orchestrator.is_final_seq(adventure, 1)

    def test_raising_the_cap_mid_sitting_is_honoured_at_once(self, child, pip, fake_ai) -> None:
        # §12.7. A latched flag would keep landing the story after the parent had
        # already changed their mind.
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)
        assert wellbeing.winding_down(Adventure.objects.get(pk=adventure.pk))

        child.session_cap_min = 30
        child.save(update_fields=["session_cap_min"])

        assert not wellbeing.winding_down(Adventure.objects.get(pk=adventure.pk))

    def test_the_decision_is_stable_across_one_beat(self, child, pip, fake_ai) -> None:
        """`is_final_seq` is asked four times while one beat is built. If they
        disagreed the child would get a goodbye with choice cards under it."""
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)

        fresh = Adventure.objects.get(pk=adventure.pk)
        assert {wellbeing.winding_down(fresh) for _ in range(4)} == {True}

    def test_the_sitting_is_recorded_as_ended_at_the_cap(self, child, pip, fake_ai) -> None:
        """The parent's report has to be able to say the control worked (§12.1)."""
        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)
        _started_minutes_ago(_sitting(adventure), 16)

        orchestrator.end_adventure(
            Adventure.objects.get(pk=adventure.pk), reason=PlaySession.EndedReason.COMPLETED
        )

        session = _sitting(adventure)
        session.refresh_from_db()
        assert session.ended_reason == PlaySession.EndedReason.CAP

    def test_a_child_who_wandered_off_is_not_relabelled_as_a_cap(self, child, pip, fake_ai) -> None:
        # Rounding "they left" up to "your limit worked" would be the first place
        # the report stopped being honest.
        adventure = orchestrator.create_adventure(child)
        _started_minutes_ago(_sitting(adventure), 16)

        orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.EXIT)

        session = _sitting(adventure)
        session.refresh_from_db()
        assert session.ended_reason == PlaySession.EndedReason.EXIT

    def test_an_ordinary_sitting_still_ends_as_completed(self, child, pip, fake_ai) -> None:
        adventure = orchestrator.create_adventure(child)
        orchestrator.end_adventure(adventure, reason=PlaySession.EndedReason.COMPLETED)

        session = _sitting(adventure)
        session.refresh_from_db()
        assert session.ended_reason == PlaySession.EndedReason.COMPLETED


# ---------------------------------------------------------------------------
# Criterion 2b: the daily time window is enforced
# ---------------------------------------------------------------------------
def _shut_the_window(child: Child) -> None:
    """A window that certainly does not contain now, whatever time the suite runs.

    A one-minute window an hour ahead: it cannot wrap, and it cannot contain the
    present moment on any machine in any timezone.
    """
    soon = (timezone.localtime() + datetime.timedelta(hours=1)).time()
    end = (timezone.localtime() + datetime.timedelta(hours=1, minutes=1)).time()
    Child.objects.filter(pk=child.pk).update(time_window_start=soon, time_window_end=end)
    child.refresh_from_db()


def _open_the_window(child: Child) -> None:
    """A window that certainly contains now: an hour either side of it."""
    start = (timezone.localtime() - datetime.timedelta(hours=1)).time()
    end = (timezone.localtime() + datetime.timedelta(hours=1)).time()
    Child.objects.filter(pk=child.pk).update(time_window_start=start, time_window_end=end)
    child.refresh_from_db()


class TestTheTimeWindow:
    def test_no_window_means_a_story_may_always_start(self, child) -> None:
        assert wellbeing.gate_for(child).open

    def test_inside_the_window_a_story_may_start(self, child) -> None:
        _open_the_window(child)
        assert wellbeing.gate_for(child).open

    def test_outside_the_window_the_gate_is_shut_with_the_companion_s_own_line(
        self, child, pip
    ) -> None:
        _shut_the_window(child)
        gate = wellbeing.gate_for(child)

        assert not gate.open
        assert gate.reason == wellbeing.CLOSED_TIME_WINDOW
        assert "Pip" in gate.message, "the child hears their friend, not a rule"

    def test_the_message_never_counts_anything_down(self, child, pip) -> None:
        # §13.6 forbids the pull mechanics; a "back in 42 minutes" would be one.
        _shut_the_window(child)
        message = wellbeing.gate_for(child).message

        assert not re.search(r"\d", message), message
        assert "minute" not in message.lower()

    def test_starting_a_new_story_outside_the_window_is_refused(
        self, auth, child, pip, fake_ai
    ) -> None:
        _shut_the_window(child)
        response = auth.post(adventures_url(child.id), {}, format="json")

        assert response.status_code == 403
        assert response.data["detail"]
        assert Adventure.objects.filter(child=child).count() == 0, "no plan may be paid for"

    def test_a_story_already_under_way_is_allowed_to_finish(
        self, auth, child, pip, fake_ai
    ) -> None:
        """The window is a door, not a shutter. Nothing in this product takes
        something away from a child mid-sentence."""
        adventure = orchestrator.create_adventure(child)
        _shut_the_window(child)

        response = auth.post(adventures_url(child.id), {}, format="json")

        assert response.status_code == 200
        assert response.data["adventureId"] == str(adventure.id)

    def test_the_read_endpoint_answers_the_child_client(self, auth, child, pip) -> None:
        _shut_the_window(child)
        response = auth.get(wellbeing_url(child.id))

        assert response.status_code == 200, response.data
        assert response.data["open"] is False
        assert response.data["reason"] == wellbeing.CLOSED_TIME_WINDOW
        assert "Pip" in response.data["message"]

    def test_the_read_endpoint_is_owner_scoped(self, api, other_parent, child, pip) -> None:
        from tests.conftest import LOGIN_URL, PASSWORD

        token = api.post(
            LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # A play window is a fact about a family's evening; another account must
        # not be able to read it.
        assert api.get(wellbeing_url(child.id)).status_code == 403

    def test_the_cap_alone_never_shuts_the_door(self, child, pip) -> None:
        """A cap bounds a sitting; it does not lock the child out. A parent who
        wants a hard daily limit has the window for exactly that."""
        session = PlaySession.objects.create(child=child)
        _started_minutes_ago(session, 90)

        assert wellbeing.cap_reached(child, session)
        assert wellbeing.gate_for(child).open


# ---------------------------------------------------------------------------
# Criterion 3: zero child-directed pull mechanics, anywhere
# ---------------------------------------------------------------------------
class TestNoChildDirectedPullMechanics:
    """§13.6, as a test rather than as a promise.

    Nobody will deliberately add a streak. Somebody will add "just a gentle
    reminder" in eight months, and this is what has to notice.
    """

    #: The vocabulary of engagement. Each is a *word*, matched on a boundary, so
    #: "streaming" does not trip "streak" and "notification" is not caught inside
    #: an unrelated identifier.
    BANNED = (
        "streak",
        "daily bonus",
        "don't miss",
        "dont miss",
        "come back soon",
        "hurry back",
        "keep your",
        "you'll lose",
        "expires",
        "limited time",
        "leaderboard",
        "points earned",
        "badge",
        "push notification",
    )

    #: Where a child's eyes actually land. The parent surface is excluded on
    #: purpose — a parent seeing "sessions this week" is a report, not a pull, and
    #: §12.9 permits parent-directed summaries explicitly.
    #:
    #: `frontend/src/components` is here because of the audit's F-11: `SafeBoundary
    #: .tsx` was added on Day 15 specifically to control what a child sees when the
    #: React tree crashes, and it lives in `components/` rather than
    #: `routes/child/`, so the scan the day's own note called "the most important
    #: test in the day" never read the newest child-facing component in the
    #: product. `SceneTransition.tsx` is the same shape. A directory-shaped scan
    #: decays by directory, which is why the floor below moved up with it.
    CHILD_SURFACES = (
        REPO / "frontend/src/routes/child",
        REPO / "frontend/src/components",
        REPO / "backend/apps/safety/fallbacks.py",
        REPO / "backend/apps/story/ai/prompts",
        REPO / "backend/apps/story/wellbeing.py",
        REPO / "backend/apps/companion",
    )

    def _child_facing_sources(self) -> list[Path]:
        files: list[Path] = []
        for target in self.CHILD_SURFACES:
            if target.is_file():
                files.append(target)
            else:
                files.extend(
                    path
                    for path in target.rglob("*")
                    if path.suffix in {".py", ".tsx", ".ts"}
                    and "test" not in path.name
                    and "__pycache__" not in path.parts
                )
        return files

    def test_the_surfaces_a_child_sees_are_actually_being_read(self) -> None:
        # A guard on the guard: a test that silently scanned nothing would pass
        # forever, which is the failure mode of every source-inspection test.
        #
        # The floor is raised whenever `CHILD_SURFACES` grows, and that is the
        # whole mechanism: `> 12` stayed true after `SafeBoundary.tsx` was added
        # *outside* the scan, so the number proved the scan was running and not
        # that it was complete (the audit's F-11). Naming the files this expects
        # would be brittle; a floor that has to move with the list is the version
        # that catches the next component added somewhere new.
        scanned = self._child_facing_sources()
        assert len(scanned) > 14, sorted(path.name for path in scanned)

        # And the two that F-11 was actually about are in it by name, because
        # "more than fourteen files" would also be satisfied by fourteen of the
        # wrong ones.
        names = {path.name for path in scanned}
        assert {"SafeBoundary.tsx", "SceneTransition.tsx"} <= names, sorted(names)

    #: Words that turn a mention into a prohibition.
    #:
    #: Needed because this codebase talks about §13.6 constantly — `CAP_CLOSE_RULE`
    #: instructs a model *not* to suggest hurrying back, and three module
    #: docstrings promise there is no streak. A scan without this flagged its own
    #: commitments, which is the failure mode that gets a test like this deleted
    #: rather than fixed.
    #:
    #: Be honest about what this costs: a line reading "no streak" passes, so a
    #: sufficiently perverse implementation could hide behind the word "no". That
    #: is an acceptable limit — this test exists to catch the *earnest* addition
    #: ("a gentle reminder so they don't lose their streak"), not an adversary, and
    #: an adversary in this repository has already won.
    NEGATIONS = (
        "no ",
        "not ",
        "never",
        "don't",
        "do not",
        "nothing",
        "without",
        "forbid",
        "must not",
        "cannot",
        "zero",
        "banned",
    )

    def test_no_engagement_vocabulary_reaches_a_child_facing_surface(self) -> None:
        offenders: list[str] = []
        for path in self._child_facing_sources():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                lowered = line.lower()
                if any(negation in lowered for negation in self.NEGATIONS):
                    continue
                for phrase in self.BANNED:
                    # `\b` on both ends where the phrase is a word, so a substring
                    # inside an unrelated identifier is not a false positive.
                    if re.search(rf"\b{re.escape(phrase)}\b", lowered):
                        offenders.append(f"{path.relative_to(REPO)}:{number}: {phrase!r}")

        assert offenders == [], (
            "§13.6 is a hard line: no child-directed streaks, FOMO or re-engagement. "
            f"Found: {offenders}"
        )

    def test_the_scan_would_actually_catch_something(self, tmp_path) -> None:
        """The other guard on the guard.

        A negation filter that swallowed everything would make the test above pass
        against a real streak counter, silently and forever. This proves it still
        bites.
        """
        planted = tmp_path / "TemptingPage.tsx"
        planted.write_text("const label = `Keep your streak going!`;", encoding="utf-8")

        text = planted.read_text(encoding="utf-8").lower()
        assert not any(negation in text for negation in self.NEGATIONS)
        assert re.search(r"\bstreak\b", text)

    def test_nothing_schedules_a_child_directed_notification(self) -> None:
        """The `notifications` app exists and must stay parent-only (§12.9).

        Checked structurally rather than by vocabulary: the thing that would make a
        notification child-directed is a delivery path keyed on a child rather than
        on a parent.
        """
        app = REPO / "backend/apps/notifications"
        sources = [path for path in app.rglob("*.py") if "__pycache__" not in path.parts]
        text = "\n".join(path.read_text(encoding="utf-8") for path in sources)

        assert "child" not in text.lower() or "parent" in text.lower()
        # And there is no scheduler at all yet, which is the strongest form of the
        # guarantee: there is nowhere for a re-engagement job to be turned on.
        assert not any("beat_schedule" in path.read_text(encoding="utf-8") for path in sources)

    def test_the_wellbeing_module_cannot_stop_a_stream(self) -> None:
        """The cap lands the story; it never cuts it off. Asserted against the
        source because the absence of a capability is the guarantee."""
        source = (REPO / "backend/apps/story/wellbeing.py").read_text(encoding="utf-8")

        for capability in ("StreamingHttpResponse", ".close()", "raise "):
            assert capability not in source, f"wellbeing must not be able to {capability}"

    def test_the_wellbeing_payload_carries_no_counters(self, auth, child, pip) -> None:
        """A countdown on a four-year-old's screen is pressure. The API cannot
        deliver one because the serializer has nowhere to put it."""
        _shut_the_window(child)
        body = auth.get(wellbeing_url(child.id)).data

        assert set(body) == {"open", "reason", "message"}
