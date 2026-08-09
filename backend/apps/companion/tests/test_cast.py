"""Who is in the picture, and whether they are the same person twice (§8.2, §9.6).

Every test here is one sentence from §8.2 made checkable. Characters are
first-class persisted entities; a character appearing in a beat has its canonical
descriptor injected into the illustration prompt; and the point of all of it is
that "Pip" is the same Pip every time — and now that Bramble is the same Bramble
every time too.

The failure being defended against is not a crash. It is a picture that is
*slightly wrong*, which a four-year-old reads as uncanny and which nothing in the
system would otherwise report.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.companion import cast
from apps.companion.models import Character
from apps.companion.scenes import EXPRESSION_CUES, canonical_expression
from apps.companion.service import create_companion, ensure_reference_set
from apps.media.prompts import CharacterRef, build_illustration_prompt, character_signature
from apps.profiles.models import Child

pytestmark = pytest.mark.django_db

BRAMBLE = {
    "key": "bramble",
    "name": "Bramble",
    "visual_descriptor": (
        "a small round frog, moss-green with a paler belly, large friendly amber "
        "eyes, and one cream stripe down its back"
    ),
}


@pytest.fixture
def aarav(db: Any) -> Child:
    parent = get_user_model().objects.create_user(
        email="cast-parent@example.com",
        password="storybook-primer-42",
    )
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 4, 5, 4),
    )


@pytest.fixture
def pip(aarav: Child) -> Any:
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


# ---------------------------------------------------------------------------
# Remembering a character (§8.2, §17.2 `character`)
# ---------------------------------------------------------------------------
def test_a_new_character_is_persisted_with_its_appearance(aarav) -> None:
    remembered = cast.remember(aarav, [BRAMBLE])

    assert [row.key for row in remembered] == ["bramble"]
    row = Character.objects.get(child=aarav, key="bramble")
    assert row.name == "Bramble"
    assert "cream stripe" in row.visual_descriptor


def test_the_first_description_wins_forever(aarav) -> None:
    """The whole mechanism, in one assertion.

    A later beat will describe Bramble again and describe it differently, because
    a model asked twice answers twice. Taking the newer answer would redraw the
    character on every appearance — which is the exact thing a persisted
    descriptor exists to stop, so the second description is discarded.
    """
    cast.remember(aarav, [BRAMBLE])
    cast.remember(aarav, [{**BRAMBLE, "visual_descriptor": "a large blue toad with red spots"}])

    assert Character.objects.filter(child=aarav, key="bramble").count() == 1
    assert "cream stripe" in Character.objects.get(child=aarav, key="bramble").visual_descriptor


def test_a_character_with_no_appearance_is_not_stored(aarav) -> None:
    # Nothing downstream could draw it consistently, which is the only reason the
    # row exists. Half a character is worse than none: it would suppress the next
    # beat's chance to describe it properly.
    cast.remember(aarav, [{"key": "shadow", "name": "Shadow", "visual_descriptor": "  "}])

    assert not Character.objects.filter(child=aarav).exists()


def test_one_family_never_sees_another_family_s_cast(aarav, django_user_model) -> None:
    other_parent = django_user_model.objects.create_user(
        email="other-cast@example.com", password="storybook-primer-42"
    )
    other = Child.objects.create(
        parent=other_parent,
        first_name="Nia",
        birthdate=datetime.date(datetime.date.today().year - 4, 1, 1),
    )
    cast.remember(aarav, [BRAMBLE])

    # Two children who both met "a frog" met different frogs (§17.5).
    assert cast.characters_for(other, ["bramble"]) == []


# ---------------------------------------------------------------------------
# Injecting them into the prompt (§8.2 — criterion 3)
# ---------------------------------------------------------------------------
def test_every_present_character_reaches_the_illustration_prompt(aarav, pip) -> None:
    """Criterion 3: style tokens *and* character references, provably, every time."""
    cast.remember(aarav, [BRAMBLE])

    refs = cast.refs_for(aarav, companion=pip, present=["pip", "bramble"], emotion="playful")
    prompt = build_illustration_prompt("a mossy riverbank at dawn", refs)

    assert "SCENE:" in prompt and "CHARACTERS:" in prompt and "STYLE:" in prompt
    # Both cast members, by name, with the appearance we hold for each.
    assert "Pip: a small round friendly dragon with warm ember scales" in prompt
    assert "Bramble:" in prompt
    assert "cream stripe" in prompt
    # The fixed art direction is on every prompt in the product (§15.9).
    assert "soft painterly children's storybook illustration" in prompt


def test_the_companion_is_in_the_picture_even_when_the_beat_forgets_them(aarav, pip) -> None:
    # A beat whose `characters_present` omits the companion is a beat a model wrote
    # carelessly, not a beat the companion is absent from. Their own story.
    refs = cast.refs_for(aarav, companion=pip, present=["bramble"])

    assert [ref.name for ref in refs] == ["Pip"]


def test_a_character_we_never_met_is_skipped_rather_than_invented(aarav, pip) -> None:
    refs = cast.refs_for(aarav, companion=pip, present=["pip", "a_passing_heron"])

    # Describing it here would be inventing it — and inventing it *differently*
    # next time, which is the defect. The beat's own prose carries it instead.
    assert [ref.name for ref in refs] == ["Pip"]


def test_the_cast_is_bounded(aarav, pip) -> None:
    keys = [f"friend_{index}" for index in range(cast.MAX_CAST + 3)]
    cast.remember(
        aarav,
        [{"key": key, "name": key, "visual_descriptor": f"a small round {key}"} for key in keys],
    )

    refs = cast.refs_for(aarav, companion=pip, present=keys)

    # The companion plus the cap, and no more: every extra descriptor is prompt
    # size, a diluted subject, and a cache key that will never be hit twice.
    assert len(refs) == cast.MAX_CAST + 1


def test_a_character_named_twice_is_one_cast_member(aarav, pip) -> None:
    cast.remember(aarav, [BRAMBLE])

    refs = cast.refs_for(aarav, companion=pip, present=["bramble", "Bramble", "bramble"])

    assert [ref.name for ref in refs] == ["Pip", "Bramble"]


# ---------------------------------------------------------------------------
# Criterion 2: the same character on its second appearance
# ---------------------------------------------------------------------------
def test_a_character_introduced_once_is_identical_when_it_returns(aarav, pip) -> None:
    """Beat 2 introduces Bramble; beat 4 gets *byte-identical* instructions for it.

    Byte-identical is the right bar rather than "similar", because the descriptor
    is hashed into `character_hash`: two renders that differ by a word are two
    cache entries and therefore two different-looking frogs.
    """
    cast.remember(aarav, [BRAMBLE])
    at_beat_2 = cast.refs_for(aarav, companion=pip, present=["pip", "bramble"])

    # ...two beats pass, the process restarts, nothing is held in memory...
    at_beat_4 = cast.refs_for(aarav, companion=pip, present=["bramble", "pip"])

    def bramble(refs: list[CharacterRef]) -> CharacterRef:
        return next(ref for ref in refs if ref.name == "Bramble")

    assert bramble(at_beat_2).render() == bramble(at_beat_4).render()
    # And the cache agrees they are the same cast, whatever order the beat named
    # them in — so the second scene is a genuine hit rather than a fresh render.
    assert character_signature(at_beat_2) == character_signature(at_beat_4)


def test_the_establishing_picture_becomes_the_character_s_anchor(aarav, pip) -> None:
    from apps.companion.service import illustrate

    cast.remember(aarav, [BRAMBLE])
    row = Character.objects.get(child=aarav, key="bramble")
    assert row.reference_asset_ids == []

    media = illustrate(pip, "a mossy riverbank at dawn")
    cast.record_reference([row], media)
    row.refresh_from_db()

    assert row.reference_asset_ids == [media.id]

    # And it is never replaced. The anchor holds the look the first render
    # settled on; letting a later picture take over is character drift, one beat
    # at a time.
    later = illustrate(pip, "a windy beach at sunset")
    cast.record_reference([row], later)
    row.refresh_from_db()
    assert row.reference_asset_ids == [media.id]


# ---------------------------------------------------------------------------
# Canonical expressions (§9.6)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("emotion", "expected"),
    [
        ("happy", "happy"),
        ("playful_curious", "curious"),
        ("warm_proud", "proud"),
        ("gentle_concern", "gentle_concern"),
        ("sleepy_content", "sleepy"),
        ("", None),
        ("brisk_mercantile", None),
    ],
)
def test_an_emotion_maps_onto_a_face_we_actually_hold(emotion, expected) -> None:
    assert canonical_expression(emotion) == expected


@pytest.mark.parametrize(
    "emotion", ["joyful_calm", "warm_peaceful", "quiet_wonder_calm", "restful"]
)
def test_a_calm_moment_does_not_draw_a_yawning_companion(emotion) -> None:
    """The only sleepy face we hold is a mid-yawn, so `calm` must not reach it.

    `calm`, `quiet`, `peaceful` and `restful` were sleepy synonyms, and the
    configured model really does return `joyful_calm` and `warm_peaceful` — 2 of
    the 29 beats in the dev database at the Day-6 review. Each of those asked the
    illustrator for a companion "mid-yawn with drooping eyelids" in the middle of
    a happy scene, which is exactly the uncanny mismatch §8.2 says breaks
    attachment. A calm joy is a joy; a calm nothing-else is no face at all.
    """
    assert canonical_expression(emotion) != "sleepy"


def test_a_calm_emotion_keeps_whatever_face_it_really_names() -> None:
    # Not merely "not sleepy": `joyful_calm` is a joyful beat and the happy face
    # is the right one, while `warm_peaceful` names no face we hold and correctly
    # gets none.
    assert canonical_expression("joyful_calm") == "happy"
    assert canonical_expression("warm_peaceful") is None
    # Explicitly drowsy still is, so the bedtime beat keeps its yawn.
    assert canonical_expression("sleepy_calm") == "sleepy"
    assert canonical_expression("gentle_yawn") == "sleepy"


def test_a_compound_emotion_resolves_to_its_head_word() -> None:
    """`playful_curious` is a kind of curious, not a kind of playful.

    Both words name faces we hold, so a left-to-right scan would confidently pick
    the wrong one — and the beat would get a bouncing companion where it asked for
    a tilted, interested one.
    """
    assert canonical_expression("playful_curious") == "curious"
    assert canonical_expression("curious_playful") == "happy"


def test_the_companion_wears_the_canonical_expression_the_beat_asks_for(aarav, pip) -> None:
    ensure_reference_set(pip)
    pip.refresh_from_db()

    proud = cast.companion_ref(pip, emotion="warm_proud")

    assert proud.expression == EXPRESSION_CUES["proud"]
    assert "In this picture: standing tall" in proud.render()
    # Anchored to the *proud* Pip we already rendered, not the neutral portrait —
    # which is what §9.6 pre-renders the five faces for.
    assert proud.reference_url
    assert proud.reference_url != cast.companion_ref(pip).reference_url


def test_an_unrecognised_emotion_leaves_the_face_alone(aarav, pip) -> None:
    plain = cast.companion_ref(pip, emotion="brisk_mercantile")

    # No clause rather than a guessed one: a face we did not pre-render is a face
    # we cannot promise is the same one next time.
    assert plain.expression == ""
    assert "In this picture:" not in plain.render()


def test_the_expression_changes_the_cache_key_but_its_absence_does_not(aarav, pip) -> None:
    """Two things at once, and both matter.

    A sleepy Pip and a proud Pip in the same clearing must not share a key, or the
    cache hands the second beat the first one's face. And a character with no
    expression must hash exactly as it did before the field existed, or adding it
    silently invalidated every illustration already on disk.
    """
    plain = cast.companion_ref(pip)
    proud = cast.companion_ref(pip, emotion="proud")
    sleepy = cast.companion_ref(pip, emotion="sleepy")

    assert character_signature([proud]) != character_signature([sleepy])
    assert character_signature([plain]) != character_signature([proud])
    assert character_signature([plain]) == f"{plain.key}={plain.descriptor}"
