"""Companion identity and its reference set (PRODUCT.md §9.1, §9.6, §8.2)."""

import pytest

from apps.companion.catalog import ARCHETYPES, PALETTES, PERSONALITY_PRESETS
from apps.companion.models import (
    CANONICAL_EXPRESSIONS,
    GREETING_AUDIO,
    PORTRAIT,
    AssetStatus,
)
from apps.companion.service import (
    build_assets,
    character_ref,
    create_companion,
    ensure_reference_set,
    ensure_visual_descriptor,
    illustrate,
)
from apps.media.models import Media, MediaType, SafetyStatus
from apps.media.service import STATS
from apps.story.ai.router import AIConfigurationError


@pytest.fixture(autouse=True)
def _no_language_model(monkeypatch) -> None:
    """No test may reach a language model.

    The descriptor path is expected to survive this — a companion whose
    descriptor could not be generated still gets one from the catalogue — so
    every test below exercises the fallback unless it says otherwise.
    """

    def unavailable(*args, **kwargs):
        raise AIConfigurationError("no provider in tests")

    monkeypatch.setattr("apps.companion.descriptor.generate_structured", unavailable)
    STATS.reset()


@pytest.fixture
def companion(child):
    return create_companion(
        child=child,
        archetype="dragon",
        name="Pip",
        palette="ember",
        personality_preset="brave_spark",
    )


@pytest.mark.django_db
def test_creation_expands_the_catalogue_keys(companion) -> None:
    assert companion.archetype == "dragon"
    assert companion.palette["key"] == "ember"
    assert companion.palette["primary"] == PALETTES["ember"].primary
    assert companion.personality["preset"] == "brave_spark"
    assert companion.personality["traits"] == list(PERSONALITY_PRESETS["brave_spark"].traits)
    # A voice is assigned at creation and belongs to the friendship (§9.1).
    assert companion.voice_id == ARCHETYPES["dragon"].voice_id
    assert companion.evolution_state == {"stage": 0}
    assert companion.assets_status == AssetStatus.PENDING


@pytest.mark.django_db
def test_creation_is_one_to_one_with_the_child(companion, child) -> None:
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        create_companion(
            child=child, archetype="fox", name="Second", palette="moss", personality_preset=""
        )


@pytest.mark.django_db
def test_a_descriptor_always_exists_even_with_no_language_model(companion) -> None:
    descriptor = ensure_visual_descriptor(companion)

    assert descriptor
    assert "Pip" in descriptor
    # Art-directed rather than generic: the silhouette and palette the catalogue
    # specifies are in the text an image model will read.
    assert "dragon" in descriptor
    assert PALETTES["ember"].description in descriptor

    companion.refresh_from_db()
    assert companion.visual_descriptor == descriptor


@pytest.mark.django_db
def test_the_descriptor_is_generated_once(companion) -> None:
    first = ensure_visual_descriptor(companion)
    companion.visual_descriptor = first
    assert ensure_visual_descriptor(companion) == first


@pytest.mark.django_db
def test_a_generated_descriptor_is_used_when_a_model_answers(companion, monkeypatch) -> None:
    from apps.companion.descriptor import VisualDescriptor

    monkeypatch.setattr(
        "apps.companion.descriptor.generate_structured",
        lambda context, model: VisualDescriptor(
            appearance="A small round dragon the size of a house cat, warm amber scales.",
            signature_features=["one crooked horn", "a leaf-shaped tail tip"],
        ),
    )

    descriptor = ensure_visual_descriptor(companion)
    assert descriptor.startswith("Pip,")
    assert "one crooked horn" in descriptor


@pytest.mark.django_db
def test_the_reference_set_is_the_portrait_plus_five_expressions(companion) -> None:
    manifest = ensure_reference_set(companion)

    assert PORTRAIT in manifest
    for expression in CANONICAL_EXPRESSIONS:
        assert expression in manifest, f"missing canonical expression: {expression}"
    assert len(CANONICAL_EXPRESSIONS) == 5

    rows = Media.objects.filter(id__in=manifest.values())
    assert rows.count() == 6
    for row in rows:
        assert row.type == MediaType.IMAGE
        assert row.safety_status == SafetyStatus.PASSED
        assert row.url


@pytest.mark.django_db
def test_the_manifest_and_the_id_array_stay_in_step(companion) -> None:
    ensure_reference_set(companion)
    companion.refresh_from_db()

    assert len(companion.reference_asset_ids) == len(companion.reference_manifest)
    assert {str(asset_id) for asset_id in companion.reference_asset_ids} == set(
        companion.reference_manifest.values()
    )


@pytest.mark.django_db
def test_building_the_reference_set_twice_costs_nothing(companion) -> None:
    ensure_reference_set(companion)
    generated = STATS.generated

    ensure_reference_set(companion)

    assert STATS.generated == generated
    assert Media.objects.count() == 6


@pytest.mark.django_db
def test_every_reference_is_the_same_character(companion) -> None:
    ensure_reference_set(companion)
    rows = Media.objects.filter(id__in=companion.reference_manifest.values())

    # One character hash across the whole set — the same character, six scenes.
    assert len({row.character_hash for row in rows}) == 1
    # And six distinct scenes, so they are genuinely different pictures.
    assert len({row.scene_hash for row in rows}) == 6


@pytest.mark.django_db
def test_a_story_scene_reuses_the_companion_character_hash(companion) -> None:
    ensure_reference_set(companion)
    portrait = Media.objects.get(id=companion.reference_manifest[PORTRAIT])

    scene = illustrate(companion, "standing at the edge of a mossy clearing at dawn")

    assert scene.character_hash == portrait.character_hash
    assert scene.style_hash == portrait.style_hash
    assert scene.scene_hash != portrait.scene_hash


@pytest.mark.django_db
def test_the_reference_url_rides_along_with_the_character(companion) -> None:
    ensure_reference_set(companion)
    companion.refresh_from_db()

    reference = character_ref(companion)
    assert reference.descriptor == companion.visual_descriptor
    assert reference.reference_url


@pytest.mark.django_db
def test_a_full_build_produces_a_greeting_and_reports_ready(companion) -> None:
    build_assets(companion)
    companion.refresh_from_db()

    assert companion.assets_status == AssetStatus.READY
    assert companion.assets_ready

    greeting = Media.objects.get(id=companion.reference_manifest[GREETING_AUDIO])
    assert greeting.type == MediaType.AUDIO
    assert greeting.safety_status == SafetyStatus.PASSED


@pytest.mark.django_db
def test_a_build_is_idempotent(companion) -> None:
    build_assets(companion)
    generated = STATS.generated

    build_assets(companion)

    assert STATS.generated == generated


@pytest.mark.django_db
def test_a_failing_image_provider_leaves_the_companion_usable(companion, monkeypatch) -> None:
    """One bad expression must not cost the child their friend.

    The portrait and the greeting are what the imprint moment needs; a missing
    `sleepy` expression is `partial` and worth another attempt, not a failure.
    """
    from apps.media.exceptions import MediaProviderError

    calls = {"n": 0}
    original = illustrate

    def flaky(companion_arg, scene):
        calls["n"] += 1
        if calls["n"] > 3:
            raise MediaProviderError("provider fell over")
        return original(companion_arg, scene)

    monkeypatch.setattr("apps.companion.service.illustrate", flaky)
    build_assets(companion)
    companion.refresh_from_db()

    assert companion.assets_status == AssetStatus.PARTIAL
    assert companion.assets_ready
    assert PORTRAIT in companion.reference_manifest
    assert GREETING_AUDIO in companion.reference_manifest
