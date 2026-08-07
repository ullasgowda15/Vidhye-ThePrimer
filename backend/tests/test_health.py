"""The health endpoint is what the load balancer and the child UI's status dot
both depend on, so its exact shape is a contract."""

from django.test import Client


def test_health_returns_ok_payload() -> None:
    response = Client().get("/api/health/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "primer",
        "version": "0.1.0",
    }


def test_health_needs_no_auth() -> None:
    # An unauthenticated probe must not redirect to a login page.
    response = Client().get("/api/health/")
    assert response.status_code == 200


def test_openapi_schema_generates() -> None:
    response = Client().get("/api/schema/")
    assert response.status_code == 200
