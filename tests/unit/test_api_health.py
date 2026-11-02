from dev_assistant.api.main import health_check


def test_health_endpoint_reports_process_status() -> None:
    assert health_check() == {"status": "ok"}
