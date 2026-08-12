"""Settings & controls, and their enforcement (PRODUCT.md §12.7, §13.4, §7.13).

Day 13's first acceptance criterion is *"each setting is honored immediately in
both generation and the safety guard — prove by trying"*, and "by trying" is what
this file does. The avoid-topic tests do not assert that a rule was written into a
prompt; they run a beat through the real orchestrator with a provider that has been
told to say the forbidden word, and assert the child never gets it.

The endpoint tests are the other half: a setting that cannot be changed, or that is
echoed back as something other than what will be enforced, is a control in name
only.
"""

import datetime
import json
from typing import Any

import pytest

from apps.companion.models import Companion
from apps.profiles.models import Child
from apps.profiles.settings import (
    DEFAULT_CEILING,
    MAX_CEILING,
    MAX_SESSION_CAP_MIN,
    MIN_CEILING,
    MIN_SESSION_CAP_MIN,
    Accessibility,
    TimeWindow,
    settings_for,
)
from apps.safety import policy
from apps.safety.prompting.assembler import safety_policy_layer
from apps.story import orchestrator
from apps.story.models import Beat

pytestmark = pytest.mark.django_db


def settings_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/settings"


@pytest.fixture
def child(parent, consent) -> Child:
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
        interests=["frogs"],
    )


@pytest.fixture
def pip_for(child) -> Companion:
    """A companion, so the story path is reachable at all.

    The descriptor only — rendering a reference set here would make every test in
    this file pay for six illustrations, and none of them is about the art.
    """
    return Companion.objects.create(
        child=child,
        archetype="dragon",
        name="Pip",
        visual_descriptor="a small round friendly dragon with warm ember scales",
    )


# ---------------------------------------------------------------------------
# settings_for: one normalised read (§12.7)
# ---------------------------------------------------------------------------
class TestNormalisation:
    def test_the_dataclass_default_matches_the_column_default(self) -> None:
        # Two places name the starting ceiling; a drift between them would mean a
        # child created through the ORM and one created through the API get
        # different safety settings.
        assert DEFAULT_CEILING == Child._meta.get_field("intensity_ceiling").default

    def test_a_fractional_ceiling_truncates_downward(self, child) -> None:
        # Not a value the column can hold, and worth pinning anyway: truncation is
        # the gentler direction, so `int()` is the right coercion here rather than
        # a rounding one.
        child.intensity_ceiling = 2.9
        assert settings_for(child).intensity_ceiling == 2

    @pytest.mark.parametrize("stored", [9, 4, -1, "loud", None])
    def test_an_out_of_range_ceiling_reads_as_the_gentlest_never_the_highest(
        self, child, stored
    ) -> None:
        """The one that must not be a `min(MAX, value)` clamp.

        Clamping `9` toward the top would resolve it to `3` — the *most*
        permissive rung in the product — so a corrupt row would read as a licence.
        An unreadable ceiling is not a parent asking for more.
        """
        child.intensity_ceiling = stored
        assert settings_for(child).intensity_ceiling == MIN_CEILING

    @pytest.mark.parametrize("stored", [0, 1, 2, 3])
    def test_a_ceiling_in_range_is_left_alone(self, child, stored) -> None:
        child.intensity_ceiling = stored
        assert settings_for(child).intensity_ceiling == stored

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [(0, MIN_SESSION_CAP_MIN), (999, MAX_SESSION_CAP_MIN), ("x", MIN_SESSION_CAP_MIN)],
    )
    def test_the_cap_clamps_into_range_both_ways(self, child, stored, expected) -> None:
        # Unlike the ceiling, both ends of this clamp are the conservative
        # direction: every value out of range becomes a *shorter* sitting.
        child.session_cap_min = stored
        assert settings_for(child).session_cap_min == expected

    def test_topics_are_trimmed_deduplicated_and_bounded(self, child) -> None:
        child.avoid_topics = ["  hospitals ", "HOSPITALS", "", "   ", "hospitals", "x" * 200]
        topics = settings_for(child).avoid_topics

        assert topics[0] == "hospitals"
        assert len(topics) == 2, "case-insensitive duplicates and blanks must be dropped"
        assert len(topics[1]) == 60, "one topic is a phrase, not a paragraph"

    def test_a_non_list_avoid_topics_is_read_as_empty_not_as_characters(self, child) -> None:
        # A string is iterable, and iterating it would turn "spiders" into seven
        # one-character rules — each of which matches half the alphabet.
        child.avoid_topics = "spiders"
        assert settings_for(child).avoid_topics == ()


class TestTimeWindow:
    def test_no_window_is_always_open(self) -> None:
        assert TimeWindow().always_open
        assert TimeWindow().contains(datetime.time(3, 0))

    def test_a_daytime_window_contains_only_its_hours(self) -> None:
        window = TimeWindow(datetime.time(16, 0), datetime.time(18, 0))

        assert window.contains(datetime.time(16, 0))
        assert window.contains(datetime.time(17, 59))
        assert not window.contains(datetime.time(18, 0))
        assert not window.contains(datetime.time(9, 0))

    def test_a_window_that_wraps_past_midnight_is_the_union_not_the_gap(self) -> None:
        """The bedtime-story shape, and the reason this is not `start <= t < end`."""
        window = TimeWindow(datetime.time(19, 0), datetime.time(7, 0))

        assert window.contains(datetime.time(20, 0))
        assert window.contains(datetime.time(2, 0))
        assert window.contains(datetime.time(6, 59))
        assert not window.contains(datetime.time(12, 0))

    def test_equal_ends_read_as_open_never_as_a_window_nobody_can_play_in(self) -> None:
        window = TimeWindow(datetime.time(9, 0), datetime.time(9, 0))
        assert window.always_open
        assert window.contains(datetime.time(3, 0))


class TestAccessibilityBlob:
    def test_an_empty_blob_reads_as_the_accessible_defaults(self) -> None:
        access = Accessibility.from_json({})
        assert access.captions is True
        assert access.audio_volume == 1.0
        assert (access.reduced_motion, access.high_contrast, access.dyslexia_font) == (
            False,
            False,
            False,
        )

    @pytest.mark.parametrize("blob", [None, "captions", 7, {"audio_volume": "loud"}])
    def test_an_unreadable_blob_never_raises_on_the_child_path(self, blob) -> None:
        # This is read while a beat is being delivered. A hand-edited row must
        # produce a stale experience, never a stack trace between a child and
        # their story (§13).
        assert Accessibility.from_json(blob).captions is True

    def test_volume_is_clamped_because_an_out_of_range_value_throws_in_the_browser(self) -> None:
        assert Accessibility.from_json({"audio_volume": 4}).audio_volume == 1.0
        assert Accessibility.from_json({"audio_volume": -2}).audio_volume == 0.0


class TestWordsOnScreen:
    """Accessibility outranks preference, and silence outranks both (§14.6)."""

    def test_captions_keep_the_words_on_screen_when_text_is_switched_off(self, child) -> None:
        child.text_on = False
        child.accessibility = {"captions": True}
        assert settings_for(child).words_on_screen

    def test_switching_the_voice_off_forces_the_words_on(self, child) -> None:
        # A beat delivered as neither sound nor text is not a beat.
        child.text_on = False
        child.voice_on = False
        child.accessibility = {"captions": False}
        assert settings_for(child).words_on_screen

    def test_a_parent_may_switch_the_paragraph_off_when_the_voice_is_on(self, child) -> None:
        child.text_on = False
        child.voice_on = True
        child.accessibility = {"captions": False}
        assert not settings_for(child).words_on_screen


# ---------------------------------------------------------------------------
# The endpoint (§12.7)
# ---------------------------------------------------------------------------
class TestTheSettingsEndpoint:
    def test_a_parent_reads_their_own_child_s_settings(self, auth, child) -> None:
        response = auth.get(settings_url(child.id))

        assert response.status_code == 200, response.data
        assert response.data["intensity_ceiling"] == DEFAULT_CEILING
        assert response.data["accessibility"]["captions"] is True
        assert response.data["time_window_start"] is None

    def test_another_family_is_refused(self, api, other_parent, child) -> None:
        from tests.conftest import LOGIN_URL, PASSWORD

        token = api.post(
            LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        assert api.get(settings_url(child.id)).status_code == 403
        assert (
            api.patch(settings_url(child.id), {"intensity_ceiling": 3}, format="json").status_code
            == 403
        )

    def test_unauthenticated_is_refused(self, api, child) -> None:
        assert api.get(settings_url(child.id)).status_code == 401

    def test_a_patch_changes_one_setting_and_leaves_the_rest(self, auth, child) -> None:
        response = auth.patch(settings_url(child.id), {"session_cap_min": 10}, format="json")

        assert response.status_code == 200, response.data
        child.refresh_from_db()
        assert child.session_cap_min == 10
        assert child.intensity_ceiling == DEFAULT_CEILING

    def test_the_response_is_what_will_be_enforced_not_what_was_stored(self, auth, child) -> None:
        """A dashboard that echoed a stored `9` back while the guard clamped it to
        `0` would be lying about a safety setting on the page that sells honesty."""
        Child.objects.filter(pk=child.pk).update(intensity_ceiling=9)

        assert auth.get(settings_url(child.id)).data["intensity_ceiling"] == MIN_CEILING

    def test_accessibility_is_merged_so_one_flag_changes_one_flag(self, auth, child) -> None:
        auth.patch(settings_url(child.id), {"accessibility": {"captions": False}}, format="json")
        response = auth.patch(
            settings_url(child.id), {"accessibility": {"high_contrast": True}}, format="json"
        )

        assert response.data["accessibility"]["high_contrast"] is True
        # The one that would break if the column were replaced rather than merged:
        # a parent turning on contrast must not silently switch captions back on
        # for a child who needs them off.
        assert response.data["accessibility"]["captions"] is False

    def test_an_unknown_accessibility_key_is_refused_rather_than_dropped(self, auth, child) -> None:
        """DRF drops unknown fields silently; here that would be a toggle a parent
        flips that changes nothing and reports success (§12.7)."""
        response = auth.patch(
            settings_url(child.id),
            {"accessibility": {"reduce_motion": True}},
            format="json",
        )

        assert response.status_code == 400
        assert "reduce_motion" in str(response.data)

    def test_a_half_set_window_is_rejected_rather_than_guessed_at(self, auth, child) -> None:
        response = auth.patch(settings_url(child.id), {"time_window_start": "16:00"}, format="json")

        assert response.status_code == 400
        assert "time_window_end" in response.data

    def test_clearing_both_ends_is_how_any_time_is_expressed(self, auth, child) -> None:
        auth.patch(
            settings_url(child.id),
            {"time_window_start": "16:00", "time_window_end": "18:00"},
            format="json",
        )
        response = auth.patch(
            settings_url(child.id),
            {"time_window_start": None, "time_window_end": None},
            format="json",
        )

        assert response.status_code == 200, response.data
        child.refresh_from_db()
        assert settings_for(child).time_window.always_open

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("intensity_ceiling", MAX_CEILING + 1),
            ("intensity_ceiling", MIN_CEILING - 1),
            ("session_cap_min", MAX_SESSION_CAP_MIN + 1),
            ("session_cap_min", 0),
            ("accessibility", {"audio_volume": 3}),
        ],
    )
    def test_out_of_range_values_are_refused_at_the_edge(self, auth, child, field, value) -> None:
        assert auth.patch(settings_url(child.id), {field: value}, format="json").status_code == 400

    def test_the_settings_route_cannot_change_the_birthdate(self, auth, child) -> None:
        """The age band is what every safety decision is keyed on, so the settings
        page is not allowed to move it — a control that could silently re-band a
        child would be the one setting that loosens the others."""
        before = child.birthdate
        auth.patch(settings_url(child.id), {"birthdate": "2024-01-01"}, format="json")

        child.refresh_from_db()
        assert child.birthdate == before


# ---------------------------------------------------------------------------
# Criterion 1: honored in generation AND in the guard — proved by trying
# ---------------------------------------------------------------------------
class TestTheAvoidListIsHonouredInGeneration:
    def test_the_topic_reaches_the_prompt_as_a_rule(self, child) -> None:
        child.avoid_topics = ["hospitals"]
        layer = safety_policy_layer(child)

        assert "Never depict, name, or allude to any of these topics" in layer
        assert "hospitals" in layer

    def test_a_setting_changed_now_is_in_the_very_next_prompt(self, child) -> None:
        """§12.7's "honored immediately", asserted rather than assumed. There is no
        cache between the column and the prompt, and this is what says so."""
        assert "spiders" not in safety_policy_layer(child)

        child.avoid_topics = ["spiders"]
        child.save(update_fields=["avoid_topics"])

        assert "spiders" in safety_policy_layer(Child.objects.get(pk=child.pk))


class TestTheAvoidListIsHonouredInTheGuard:
    """The half that does not depend on a model choosing to obey."""

    def test_a_beat_naming_an_avoid_topic_is_blocked(self, child) -> None:
        child.avoid_topics = ["hospitals"]
        findings = policy.check_text("Pip took the frog to the hospital.", child)

        assert [finding.category for finding in findings] == [policy.AVOID_TOPIC]
        assert findings[0].action == policy.BLOCK

    def test_a_topic_typed_in_the_plural_still_catches_the_singular(self, child) -> None:
        child.avoid_topics = ["spiders"]
        assert policy.check_text("A friendly spider waved hello.", child)

    def test_the_marker_records_the_rule_and_never_the_refused_beat(self, child) -> None:
        child.avoid_topics = ["hospitals"]
        (finding,) = policy.check_text("Pip went to the hospital wing.", child)

        assert finding.marker == "topic:hospitals"
        assert "wing" not in finding.marker

    def test_a_normalised_topic_is_what_the_guard_enforces(self, child) -> None:
        # Untouched by the serializer — written straight to the column, as a
        # fixture or a shell would. The guard has to be right about these too.
        #
        # The capitals are the point rather than decoration: `removesuffix("s")`
        # compares exactly, so before Day 13 a parent who typed HOSPITALS in caps
        # got a rule that matched "hospitals" and let "hospital" through. The match
        # was always case-insensitive, which is what made the asymmetry invisible.
        Child.objects.filter(pk=child.pk).update(avoid_topics=["  HOSPITALS  "])
        assert policy.check_text("They passed the hospital.", Child.objects.get(pk=child.pk))

    @pytest.mark.parametrize("typed", ["Spiders", "SPIDERS", "spiders", "spider"])
    def test_a_topic_catches_both_numbers_however_it_was_capitalised(self, child, typed) -> None:
        child.avoid_topics = [typed]
        assert policy.check_text("A friendly spider waved.", child), "singular"
        assert policy.check_text("Two spiders waved.", child), "plural"


class TestTheCeilingIsHonouredInBothHalves:
    def test_the_prompt_states_the_rung_the_parent_chose(self, child) -> None:
        child.intensity_ceiling = 0
        assert "Intensity ceiling: 0 of 3" in safety_policy_layer(child)
        assert "gentlest" in safety_policy_layer(child)

    def test_frightening_language_above_the_ceiling_is_refused(self, child) -> None:
        child.intensity_ceiling = 1
        findings = policy.check_text("The monster loomed out of the pitch black.", child)

        assert findings, "a monster is above a ceiling of 1"
        assert findings[0].category == policy.FRIGHTENING
        assert findings[0].action == policy.REWRITE

    def test_the_same_beat_passes_for_a_child_whose_parent_allowed_it(self, child) -> None:
        child.intensity_ceiling = 2
        assert not policy.check_text("The monster waved a friendly claw.", child)

    def test_a_corrupt_ceiling_tightens_the_guard_rather_than_loosening_it(self, child) -> None:
        """The other side of `clamped_ceiling`. A stored `9` must make the guard
        stricter, not permit everything."""
        Child.objects.filter(pk=child.pk).update(intensity_ceiling=9)
        stored = Child.objects.get(pk=child.pk)

        assert policy.check_text("The monster waved a friendly claw.", stored)


class TestAnAvoidTopicNeverReachesTheChild:
    """Criterion 1 and criterion 7, end to end.

    The provider is told to write the forbidden word. The assertion is not that a
    rule was in the prompt — it is that the beat which reaches the database, and
    therefore the child, does not contain it.
    """

    @pytest.fixture
    def defiant_ai(self, fake_ai) -> Any:
        """A model that ignores the safety policy layer entirely.

        Which is the case the output guard exists for (§13.3): the prompt is a
        request, and a guard that only worked against a compliant model would be a
        guard that never has to work.
        """
        fake_ai.queue_beat_response(
            json.dumps(
                {
                    "beat_id": "b1",
                    "narration": "Pip and you walked into the hospital to visit the frogs.",
                    "scene": {
                        "setting": "a hospital corridor",
                        "illustration_prompt": ("a bright hospital corridor with a nurse frog"),
                        "characters_present": ["pip"],
                        "new_characters": [],
                    },
                    "interaction": {
                        "type": "choose",
                        "prompt_audio": "Shall we visit the hospital ward?",
                        "expected": None,
                        "choices": [
                            {
                                "key": "go_in",
                                "label": "Go into the hospital",
                                "icon_prompt": "a door",
                            },
                            {"key": "wait", "label": "Wait outside", "icon_prompt": "a bench"},
                        ],
                    },
                    "learning": {
                        "objective_id": "count_to_3",
                        "difficulty": 0.4,
                        "skill_evidence_key": "k",
                    },
                    "emotion": "playful_curious",
                    "continuity_refs": [],
                    "next_hint": "carry on",
                }
            )
        )
        return fake_ai

    def test_the_child_gets_a_safe_fallback_instead_of_the_forbidden_word(
        self, child, pip_for, defiant_ai
    ) -> None:
        child.avoid_topics = ["hospitals"]
        child.save(update_fields=["avoid_topics"])

        adventure = orchestrator.create_adventure(child)
        beat = orchestrator.create_beat(adventure, seq=1)

        # The whole of what a child sees or hears, checked as one string — the same
        # concatenation the guard itself grades.
        spoken = " ".join(
            [beat.narration, orchestrator.spoken_line(beat), str(beat.scene), str(beat.interaction)]
        ).lower()
        assert "hospital" not in spoken

        # And it is a *beat*, not an error: illustrated, voiced, answerable. The
        # child cannot tell (§13.3).
        assert beat.safety_status == Beat.SafetyStatus.BLOCKED
        assert beat.narration
        assert beat.scene["illustration_prompt"]

    def test_the_refusal_is_recorded_for_the_parent_without_copying_the_beat(
        self, child, pip_for, defiant_ai
    ) -> None:
        from apps.safety.models import SafetyIncident

        child.avoid_topics = ["hospitals"]
        child.save(update_fields=["avoid_topics"])

        adventure = orchestrator.create_adventure(child)
        orchestrator.create_beat(adventure, seq=1)

        incident = SafetyIncident.objects.filter(child=child, category=policy.AVOID_TOPIC).first()
        assert incident is not None, "a refusal a parent cannot see is a refusal nobody audits"
        assert "hospital corridor" not in str(incident.detail)
