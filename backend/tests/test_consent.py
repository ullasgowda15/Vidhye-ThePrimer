"""Consent recording and the child-creation gate (PRODUCT.md §13.7).

The gate is the legal floor of the product: no child data may be collected
before a parent has consented. It is asserted here from the outside, over HTTP,
because that is the only place the rule can actually be broken.
"""

import pytest

from apps.accounts.models import ConsentRecord
from apps.profiles.models import Child
from tests.conftest import CHILDREN_URL, CONSENT_URL

pytestmark = pytest.mark.django_db


def test_consent_is_recorded_with_server_side_evidence(auth, parent) -> None:
    response = auth.post(
        CONSENT_URL,
        {"consent_type": "data_collection_v1", "method": "checkbox_attestation"},
        format="json",
        HTTP_USER_AGENT="PrimerTest/1.0",
    )

    assert response.status_code == 201, response.data
    assert response.data["granted_at"]

    record = ConsentRecord.objects.get(pk=response.data["id"])
    assert record.parent == parent
    assert record.ip  # captured from the request, not supplied by the client
    assert record.evidence_ref == "PrimerTest/1.0"
    assert record.revoked_at is None


def test_consent_ignores_a_client_supplied_evidence_ref(auth) -> None:
    response = auth.post(
        CONSENT_URL,
        {
            "consent_type": "data_collection_v1",
            "method": "checkbox_attestation",
            "evidence_ref": "i promise i consented",
        },
        format="json",
        HTTP_USER_AGENT="PrimerTest/1.0",
    )

    assert ConsentRecord.objects.get(pk=response.data["id"]).evidence_ref == "PrimerTest/1.0"


def test_consent_requires_authentication(api) -> None:
    response = api.post(
        CONSENT_URL,
        {"consent_type": "data_collection_v1", "method": "checkbox_attestation"},
        format="json",
    )
    assert response.status_code == 401


def test_consent_rejects_an_unknown_version(auth) -> None:
    response = auth.post(
        CONSENT_URL,
        {"consent_type": "data_collection_v0", "method": "checkbox_attestation"},
        format="json",
    )
    assert response.status_code == 400


def test_child_cannot_be_created_without_consent(auth, child_payload) -> None:
    response = auth.post(CHILDREN_URL, child_payload, format="json")

    assert response.status_code == 403, response.data
    assert Child.objects.count() == 0


def test_child_can_be_created_once_consent_exists(auth, consent, child_payload) -> None:
    assert auth.post(CHILDREN_URL, child_payload, format="json").status_code == 201


def test_revoked_consent_no_longer_opens_the_gate(auth, consent, child_payload) -> None:
    from django.utils import timezone

    consent.revoked_at = timezone.now()
    consent.save(update_fields=["revoked_at"])

    assert auth.post(CHILDREN_URL, child_payload, format="json").status_code == 403


def test_another_parents_consent_does_not_open_the_gate(auth, other_parent, child_payload) -> None:
    ConsentRecord.objects.create(
        parent=other_parent,
        consent_type="data_collection_v1",
        method="checkbox_attestation",
    )

    assert auth.post(CHILDREN_URL, child_payload, format="json").status_code == 403


def test_consent_precedes_the_child_in_time(auth, consent, child_payload) -> None:
    # The founder's manual check, asserted: the record must be older than the row
    # it authorised, or the gate was applied after the fact.
    auth.post(CHILDREN_URL, child_payload, format="json")

    # `<=`, not `<`: on a coarse system clock both rows can land in the same
    # tick. What must never happen is a child predating its consent.
    assert consent.granted_at <= Child.objects.get().created_at
