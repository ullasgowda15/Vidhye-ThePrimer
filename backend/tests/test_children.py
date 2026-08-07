"""Child profile CRUD, ownership and cascade (PRODUCT.md §17.2, §17.5, §18.3)."""

import datetime

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.utils import timezone

from apps.companion.models import Companion
from apps.profiles.models import Child
from apps.story.models import PlaySession
from tests.conftest import CHILDREN_URL

pytestmark = pytest.mark.django_db


def _create(auth, payload) -> dict:
    response = auth.post(CHILDREN_URL, payload, format="json")
    assert response.status_code == 201, response.data
    return response.data


# --- CRUD -------------------------------------------------------------------


def test_create_persists_the_personalization_seeds(auth, consent, child_payload) -> None:
    data = _create(auth, child_payload)

    assert data["interests"] == ["dinosaurs", "the sea"]
    assert data["fears"] == ["the dark"]
    assert data["avoid_topics"] == ["hospitals"]
    assert data["intensity_ceiling"] == 1
    assert data["session_cap_min"] == 20


def test_create_defaults_seeds_to_empty_lists_never_null(auth, consent) -> None:
    data = _create(auth, {"first_name": "Mia", "birthdate": str(datetime.date(2020, 3, 1))})

    assert data["interests"] == []
    assert data["fears"] == []
    assert data["avoid_topics"] == []


def test_create_trims_blank_seeds(auth, consent) -> None:
    data = _create(
        auth,
        {
            "first_name": "Mia",
            "birthdate": str(datetime.date(2020, 3, 1)),
            "interests": ["  frogs  ", "", "   "],
        },
    )
    assert data["interests"] == ["frogs"]


def test_create_derives_age_and_band(auth, consent, child_payload) -> None:
    data = _create(auth, child_payload)

    assert data["age"] == 5
    assert data["age_band"] == "5-7"
    assert data["age_out_of_range"] is False


def test_age_outside_the_design_range_is_flagged_not_rejected(auth, consent) -> None:
    born = timezone.localdate().replace(year=timezone.localdate().year - 9)
    data = _create(auth, {"first_name": "Ilya", "birthdate": str(born)})

    assert data["age_out_of_range"] is True


def test_list_and_retrieve(auth, consent, child_payload) -> None:
    created = _create(auth, child_payload)

    listing = auth.get(CHILDREN_URL)
    assert listing.status_code == 200
    assert [c["id"] for c in listing.data] == [created["id"]]

    detail = auth.get(f"{CHILDREN_URL}/{created['id']}")
    assert detail.status_code == 200
    assert detail.data["first_name"] == "Aarav"


def test_patch_updates_seeds_and_controls(auth, consent, child_payload) -> None:
    created = _create(auth, child_payload)

    response = auth.patch(
        f"{CHILDREN_URL}/{created['id']}",
        {"avoid_topics": ["hospitals", "loud noises"], "intensity_ceiling": 0},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["avoid_topics"] == ["hospitals", "loud noises"]
    assert response.data["intensity_ceiling"] == 0
    # An untouched field must survive a PATCH.
    assert response.data["interests"] == ["dinosaurs", "the sea"]


def test_a_child_cannot_be_reassigned_to_another_parent(
    auth, consent, child_payload, other_parent
) -> None:
    created = _create(auth, child_payload)

    auth.patch(f"{CHILDREN_URL}/{created['id']}", {"parent": str(other_parent.id)}, format="json")

    assert Child.objects.get(pk=created["id"]).parent_id != other_parent.id


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize("ceiling", [-1, 4, 99])
def test_intensity_ceiling_outside_0_3_is_rejected(auth, consent, child_payload, ceiling) -> None:
    response = auth.post(
        CHILDREN_URL, {**child_payload, "intensity_ceiling": ceiling}, format="json"
    )

    assert response.status_code == 400
    assert "intensity_ceiling" in response.data


@pytest.mark.parametrize("cap", [0, 4, 31])
def test_session_cap_outside_5_30_is_rejected(auth, consent, child_payload, cap) -> None:
    response = auth.post(CHILDREN_URL, {**child_payload, "session_cap_min": cap}, format="json")

    assert response.status_code == 400
    assert "session_cap_min" in response.data


def test_intensity_slider_may_not_be_free_text(auth, consent, child_payload) -> None:
    response = auth.post(
        CHILDREN_URL, {**child_payload, "intensity_ceiling": "gentle"}, format="json"
    )
    assert response.status_code == 400


def test_a_future_birthdate_is_rejected(auth, consent, child_payload) -> None:
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    response = auth.post(CHILDREN_URL, {**child_payload, "birthdate": str(tomorrow)}, format="json")

    assert response.status_code == 400
    assert "birthdate" in response.data


def test_seed_lists_are_bounded(auth, consent, child_payload) -> None:
    too_many = {**child_payload, "interests": [f"thing{i}" for i in range(11)]}
    response = auth.post(CHILDREN_URL, too_many, format="json")
    assert response.status_code == 400

    response = auth.post(CHILDREN_URL, {**child_payload, "fears": ["x" * 200]}, format="json")
    assert response.status_code == 400


# --- ownership --------------------------------------------------------------


def test_another_parent_gets_403_not_the_child(
    api, auth, consent, child_payload, other_parent
) -> None:
    from tests.conftest import LOGIN_URL, PASSWORD

    created = _create(auth, child_payload)

    intruder_tokens = api.post(
        LOGIN_URL, {"email": other_parent.email, "password": PASSWORD}, format="json"
    ).data
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {intruder_tokens['access']}")

    assert api.get(f"{CHILDREN_URL}/{created['id']}").status_code == 403
    assert (
        api.patch(
            f"{CHILDREN_URL}/{created['id']}", {"first_name": "Mine"}, format="json"
        ).status_code
        == 403
    )
    assert api.delete(f"{CHILDREN_URL}/{created['id']}").status_code == 403
    # And the list route shows them nothing of ours.
    assert api.get(CHILDREN_URL).data == []


def test_children_require_authentication(api) -> None:
    assert api.get(CHILDREN_URL).status_code == 401


# --- deletion ---------------------------------------------------------------


def _tables_referencing_child() -> list[tuple[str, str]]:
    """Every (table, column) with a foreign key to `profiles_child`.

    Discovered from the live schema rather than listed by hand, so a model added
    on a later day is covered by this test the day it lands.
    """
    child_table = Child._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = %s
            """,
            [child_table],
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


def test_delete_cascades_to_every_table_referencing_the_child(
    auth, consent, parent, child_payload
) -> None:
    created = _create(auth, child_payload)
    child = Child.objects.get(pk=created["id"])

    # Populate every child-linked table that exists today.
    Companion.objects.create(child=child, archetype="dragon", name="Pip")
    PlaySession.objects.create(child=child, device="tablet")
    child.consents.create(
        parent=parent, consent_type="data_collection_v1", method="checkbox_attestation"
    )

    references = _tables_referencing_child()
    assert references, "expected at least one FK to profiles_child"

    assert auth.delete(f"{CHILDREN_URL}/{created['id']}").status_code == 204

    with connection.cursor() as cursor:
        for table, column in references:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', [child.id])
            assert cursor.fetchone()[0] == 0, f"{table}.{column} still references the child"

    assert not Child.objects.filter(pk=child.id).exists()


def test_every_fk_to_child_cascades_at_the_model_level() -> None:
    # The runtime check above only covers rows we remembered to create. This one
    # covers the declaration, so a future `on_delete=PROTECT` is caught even if
    # nobody writes a fixture for it (§17.5).
    offenders = [
        f"{model.__name__}.{field.name}"
        for model in django_apps.get_models()
        for field in model._meta.fields
        if field.related_model is Child
        and field.remote_field is not None
        and field.remote_field.on_delete.__name__ != "CASCADE"
    ]

    assert offenders == []
