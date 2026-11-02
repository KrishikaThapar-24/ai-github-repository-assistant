import time

from dev_assistant.worker.jobs import InMemoryJobRunner


def test_completed_job_history_is_pruned_to_the_configured_limit() -> None:
    runner = InMemoryJobRunner(max_workers=1, max_pending=1, max_history=1)

    first = runner.submit(lambda: "first")
    for _ in range(100):
        status = runner.get(first.job_id)
        if status is not None and status.status == "succeeded":
            break
        time.sleep(0.01)
    assert status is not None and status.status == "succeeded"

    second = runner.submit(lambda: "second")
    for _ in range(100):
        status = runner.get(second.job_id)
        if status is not None and status.status == "succeeded":
            break
        time.sleep(0.01)

    assert status is not None and status.status == "succeeded"
    assert runner.get(first.job_id) is None
