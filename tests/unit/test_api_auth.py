from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import SecretStr

from dev_assistant.api import main
from dev_assistant.core.config import Settings


def _request(path: str, authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
    )


def _complete_without_io(coroutine):
    """Run a middleware coroutine that has no actual async I/O in its test path."""

    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Expected the middleware test path to complete without I/O")


def test_api_routes_require_bearer_token_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        Settings(api_access_token=SecretStr("server-token"), _env_file=None),
    )

    async def call_next(_request):
        return Response(status_code=200)

    missing = _complete_without_io(
        main.require_api_access_token(_request("/api/v1/status"), call_next)
    )
    incorrect = _complete_without_io(
        main.require_api_access_token(
            _request("/api/v1/status", "Bearer incorrect"), call_next
        )
    )
    accepted = _complete_without_io(
        main.require_api_access_token(
            _request("/api/v1/status", "Bearer server-token"), call_next
        )
    )

    assert isinstance(missing, JSONResponse)
    assert missing.status_code == 401
    assert incorrect.status_code == 401
    assert accepted.status_code == 200


def test_health_check_remains_public_for_host_health_probes(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        Settings(api_access_token=SecretStr("server-token"), _env_file=None),
    )

    async def call_next(_request):
        return Response(status_code=200)

    response = _complete_without_io(
        main.require_api_access_token(_request("/health"), call_next)
    )

    assert response.status_code == 200
