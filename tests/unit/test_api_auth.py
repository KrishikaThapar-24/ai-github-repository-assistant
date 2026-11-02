from fastapi.testclient import TestClient
from pydantic import SecretStr

from dev_assistant.api import main
from dev_assistant.core.config import Settings


def test_api_routes_require_bearer_token_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        Settings(api_access_token=SecretStr("server-token"), _env_file=None),
    )
    client = TestClient(main.app)

    assert client.get("/api/v1/status").status_code == 401
    assert client.get(
        "/api/v1/status", headers={"Authorization": "Bearer incorrect"}
    ).status_code == 401
    assert client.get(
        "/api/v1/status", headers={"Authorization": "Bearer server-token"}
    ).status_code == 200


def test_health_check_remains_public_for_host_health_probes(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        Settings(api_access_token=SecretStr("server-token"), _env_file=None),
    )

    assert TestClient(main.app).get("/health").status_code == 200
