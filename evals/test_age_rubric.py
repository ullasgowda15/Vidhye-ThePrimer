"""Age-appropriateness (PRODUCT.md §19.7's (c), §14.8, §2.2).

> *"age-appropriateness graded by a rubric + model-grader + human spot-check"*

All three legs are here, and they are not the same kind of thing, so the file
says plainly which one gates CI:

* **the rubric** — deterministic. Sentence length by age band, vocabulary a
  five-year-old does not have, a question short enough to hold in the head. It
  measures what is measurable and claims nothing else. **This is the gate.**
* **the model-grader** — a second model, scoring against `graders.MODEL_RUBRIC`.
  Judgement, and the only one of the three that can read register and tone. Runs
  under `--live` only; CI has no keys, deliberately.
* **the human spot-check** — `pytest evals/ --spotcheck` writes every graded beat
  to `evals/spotcheck/` as readable markdown. The third leg is somebody reading
  the stories out loud, and no amount of grading substitutes for it.

**Both age bands are played.** The rubric's whole content is that a 3-year-old
and a 7-year-old need different sentences, so grading only one band would be
grading a constant.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

import graders
import thresholds
from harness import Score, play

pytestmark = pytest.mark.django_db


@pytest.fixture
def bands(child: Any) -> list[Any]:
    """The same child, at the two ages the product serves (§10.1's bands)."""
    from apps.profiles.models import AGE_BAND_OLDER, AGE_BAND_YOUNGER

    today = datetime.date.today()
    younger = child
    younger.birthdate = datetime.date(today.year - 4, 5, 4)
    younger.save(update_fields=["birthdate"])
    assert younger.age_band == AGE_BAND_YOUNGER

    from apps.companion.service import create_companion
    from apps.profiles.models import Child
    from django.contrib.auth import get_user_model

    older = Child.objects.create(
        parent=get_user_model().objects.create_user(
            email="evals-older@example.com", password="storybook-primer-42"
        ),
        first_name="Nia",
        birthdate=datetime.date(today.year - 7, 3, 2),
        interests=["rivers"],
        intensity_ceiling=2,
    )
    create_companion(
        child=older,
        archetype="fox",
        name="Ember",
        palette="ember",
        personality_preset="brave_spark",
    )
    assert older.age_band == AGE_BAND_OLDER
    return [younger, older]


def test_every_beat_a_child_hears_fits_their_age(
    bands: list[Any], provider: Any, spotcheck: Any
) -> None:
    """The deterministic rubric, over two whole sittings, one per band."""
    score = Score("age.rubric")
    for subject in bands:
        run = play(subject)
        spotcheck.write(f"age-{subject.age_band}", run)
        for turn in run.turns:
            reason = graders.age_rubric(turn, age_band=subject.age_band)
            score.record(reason is None, reason or "")

    score.assert_at_least(thresholds.gate_for("age.rubric"))


def test_the_rubric_catches_a_beat_written_for_an_adult(child: Any, provider: Any) -> None:
    """The rubric bites.

    Without this, `age.rubric` at 90% is a number that has never once been below
    100%, and a suite that has never failed is a suite nobody knows the shape of.
    """
    if provider is None:
        pytest.skip("scripted mode only")
    provider.regress("too_wordy")
    run = play(child)

    reasons = [graders.age_rubric(turn, age_band=child.age_band) for turn in run.turns]
    assert any(reason is not None for reason in reasons), (
        "the rubric passed a beat full of forty-word sentences and words like "
        "'notwithstanding' addressed to a five-year-old"
    )


def test_the_rubric_covers_the_bands_the_product_actually_has(child: Any) -> None:
    """Two assertions in one, and both have bitten.

    *Every band has a rubric* — keyed by `Child.age_band`'s own constants, so a
    renamed band fails here rather than falling through to a default and
    reporting every beat as too long (which is exactly what happened on this
    file's first run).

    *The bands differ* — a rubric with one setting for both is not a rubric.
    """
    from apps.profiles.models import AGE_BAND_OLDER, AGE_BAND_YOUNGER

    assert set(graders.MAX_SENTENCE_WORDS) == {AGE_BAND_YOUNGER, AGE_BAND_OLDER}
    assert graders.MAX_SENTENCE_WORDS[AGE_BAND_YOUNGER] < graders.MAX_SENTENCE_WORDS[AGE_BAND_OLDER]


@pytest.mark.live
def test_a_second_model_agrees_the_beats_are_age_appropriate(
    child: Any, provider: Any, live: bool, spotcheck: Any
) -> None:
    """§19.7's model-grader. Live only, and it says why it is not the gate.

    A ship gate that depended on a live provider goes red when a vendor has a bad
    afternoon, and a gate that goes red for reasons unrelated to the change is a
    gate somebody eventually disables. So this runs before a release, by a person,
    and the deterministic rubric above is what every commit is held to.

    It grades with `AI_SAFETY_MODEL` rather than the beat model, because a model
    grading its own output is weak (§13.3) and the beat model is the subject here.
    """
    if not live:
        pytest.skip("needs --live and a configured provider")

    run = play(child)
    spotcheck.write("age-model-graded", run)

    score = Score("age.model_grader")
    for turn in run.turns:
        ok, reason = graders.age_by_model(turn.narration, age=str(child.age))
        score.record(ok, f"beat {turn.seq}: {reason}")

    score.assert_at_least(thresholds.gate_for("age.model_grader"))
