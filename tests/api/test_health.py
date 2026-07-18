from fastapi.testclient import TestClient

from spanvouch.api.app import create_app


def test_health_returns_service_identity() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "agent-failure-clinic",
    }
