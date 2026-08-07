"""The companion API (PRODUCT.md §18.3, §9.1, §13.7).

End-to-end through DRF: consent, ownership, the 1:1 rule, and the shape the
picker and the greeting scene actually consume.
"""

import datetime
from typing import Any

import pytest

from apps.companion.models import CANONICAL_EXPRESSIONS, AssetStatus, Companion
from apps.profiles.models import Child

CATALOG_URL = "/api/v1/companion-catalog"


def companion_url(child_id: Any) -> str:
    return f"/api/v1/children/{child_id}/companion"


@pytest.fixture(autouse=True)
def _assets_inline(settings) -> None:
    """Run the asset task in-process.

    The endpoint hands the work to Celery so a parent is not held for thirty
    seconds; the tests run it inline so the response and the finished companion
    can both be asserted without a broker.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def aarav(parent, consent) -> Child:
    return Child.objects.create(
        parent=parent,
        first_name="Aarav",
        birthdate=datetime.date(datetime.date.today().year - 5, 5, 4),
    )


@pytest.fixture
def payload() -> dict[str, Any]:
    return {
        "archetype": "dragon",
        "name": "Pip",
        "palette": "ember",
        "personality_preset": "brave_spark",
    }


@pytest.mark.django_db
def test_a_companion_is_created_and_persists(auth, aarav, payload) -> None:
    created = auth.post(companion_url(aarav.id), payload, format="json")
    assert created.status_code == 201, created.data

    body = created.data
    assert body["name"] == "Pip"
    assert body["archetype"] == "dragon"
    # Keys on the wire, colours alongside — never raw hex in, never a bare dict out.
    assert body["palette"] == "ember"
    assert body["palette_colors"]["primary"].startswith("#")
    assert body["personality"]["preset"] == "brave_spark"
    assert body["voice_id"]
    assert body["evolution_state"] == {"stage": 0}

    fetched = auth.get(companion_url(aarav.id))
    assert fetched.status_code == 200
    assert fetched.data["id"] == body["id"]


@pytest.mark.django_db
def test_the_response_carries_the_whole_reference_set(auth, aarav, payload) -> None:
    response = auth.post(companion_url(aarav.id), payload, format="json")
    body = auth.get(companion_url(aarav.id)).data

    assert response.status_code == 201
    assert body["visual_descriptor"]
    assert body["assets_status"] == AssetStatus.READY
    assert body["assets_ready"] is True
    assert body["portrait_url"]
    assert sorted(body["expressions"]) == sorted(CANONICAL_EXPRESSIONS)
    # Captioned as well as spoken (§14.6), and by name (§4.3).
    assert body["greeting"]["audio_url"]
    assert "Aarav" in body["greeting"]["text"]
    assert "Pip" in body["greeting"]["text"]
    assert len(body["reference_asset_ids"]) == len(CANONICAL_EXPRESSIONS) + 2


@pytest.mark.django_db
def test_defaults_alone_produce_a_complete_companion(auth, aarav) -> None:
    # §9.1: a sixty-second setup must yield a lovable companion, so archetype
    # and name are the only things a parent has to answer.
    response = auth.post(
        companion_url(aarav.id), {"archetype": "owl", "name": "Nim"}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["palette"]
    assert response.data["personality"]["traits"]


@pytest.mark.django_db
def test_a_second_companion_is_refused(auth, aarav, payload) -> None:
    assert auth.post(companion_url(aarav.id), payload, format="json").status_code == 201

    again = auth.post(companion_url(aarav.id), payload, format="json")

    assert again.status_code == 409
    assert Companion.objects.filter(child=aarav).count() == 1


@pytest.mark.django_db
def test_unknown_catalogue_keys_are_rejected(auth, aarav) -> None:
    # A palette or archetype we did not write would flow into an image prompt.
    for bad in (
        {"archetype": "tyrannosaur", "name": "Rex"},
        {"archetype": "fox", "name": "Rex", "palette": "neon"},
        {"archetype": "fox", "name": "Rex", "personality_preset": "sarcastic"},
        {"archetype": "fox", "name": "   "},
    ):
        response = auth.post(companion_url(aarav.id), bad, format="json")
        assert response.status_code == 400, (bad, response.data)


@pytest.mark.django_db
def test_consent_is_required_before_a_companion_exists(auth, parent) -> None:
    # A child created under an earlier consent that has since been revoked.
    child = Child.objects.create(
        parent=parent, first_name="Mira", birthdate=datetime.date(2021, 3, 2)
    )
    parent.consents.all().delete()

    response = auth.post(
        companion_url(child.id), {"archetype": "fox", "name": "Rus"}, format="json"
    )

    assert response.status_code == 403
    assert not Companion.objects.filter(child=child).exists()


@pytest.mark.django_db
def test_another_parent_cannot_read_or_create(api, auth, aarav, payload, other_parent) -> None:
    auth.post(companion_url(aarav.id), payload, format="json")

    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(other_parent)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    assert api.get(companion_url(aarav.id)).status_code == 403
    assert api.post(companion_url(aarav.id), payload, format="json").status_code == 403


@pytest.mark.django_db
def test_the_endpoint_requires_a_signed_in_parent(api, aarav) -> None:
    assert api.get(companion_url(aarav.id)).status_code == 401


@pytest.mark.django_db
def test_a_child_without_a_companion_returns_404(auth, aarav) -> None:
    assert auth.get(companion_url(aarav.id)).status_code == 404


@pytest.mark.django_db
def test_the_catalogue_is_the_same_list_the_api_validates_against(auth) -> None:
    response = auth.get(CATALOG_URL)

    assert response.status_code == 200
    keys = {item["key"] for item in response.data["archetypes"]}
    assert "dragon" in keys
    assert response.data["palettes"][0]["primary"].startswith("#")
    assert response.data["personality_presets"]


@pytest.mark.django_db
def test_deleting_the_child_takes_the_companion_and_its_media(auth, aarav, payload) -> None:
    from apps.media.models import Media

    auth.post(companion_url(aarav.id), payload, format="json")
    assert Media.objects.filter(child=aarav).exists()

    assert auth.delete(f"/api/v1/children/{aarav.id}").status_code == 204

    assert not Companion.objects.filter(child_id=aarav.id).exists()
    assert not Media.objects.filter(child_id=aarav.id).exists()
