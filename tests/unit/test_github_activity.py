import httpx
import pytest

from dev_assistant.github.activity import (
    GitHubActivityClient,
    latest_activity_kind,
)
from dev_assistant.github.source import GitHubSourceError


def test_pull_request_review_fetches_metadata_and_diff_with_read_only_gets() -> None:
    requests: list[httpx.Request] = []
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n-x=1\n+x=2\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("accept") == "application/vnd.github.diff":
            return httpx.Response(200, text=diff)
        return httpx.Response(
            200,
            json={
                "number": 23,
                "title": "Harden checkout",
                "body": "Review note",
                "html_url": "https://github.com/acme/demo/pull/23",
                "base": {"sha": "base12345678"},
                "head": {"sha": "head12345678"},
            },
        )

    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    review = client.pull_request_for_review("acme/demo", 23)

    assert review.number == 23
    assert review.base_sha == "base12345678"
    assert review.head_sha == "head12345678"
    assert review.diff == diff
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)


def test_pull_request_review_rejects_diff_over_configured_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("accept") == "application/vnd.github.diff":
            return httpx.Response(200, text="x" * 32)
        return httpx.Response(
            200,
            json={"base": {"sha": "base12345678"}, "head": {"sha": "head12345678"}},
        )

    client = GitHubActivityClient(
        max_pull_request_diff_bytes=16,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(GitHubSourceError, match="size limit"):
        client.pull_request_for_review("acme/demo", 23)


def test_issue_investigation_fetches_issue_and_comments_with_read_only_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/comments"):
            return httpx.Response(
                200,
                json=[
                    {"user": {"login": "octocat"}, "body": "Retry reproduces it."},
                    {"user": {"login": "dev"}, "body": "Only on empty carts."},
                ],
            )
        return httpx.Response(
            200,
            json={
                "number": 17,
                "title": "Checkout fails on retry",
                "body": "Retries fail when the cart is empty.",
                "html_url": "https://github.com/acme/demo/issues/17",
            },
        )

    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    issue = client.issue_for_investigation("acme/demo", 17)

    assert issue.number == 17
    assert issue.title == "Checkout fails on retry"
    assert "Retries fail when the cart is empty." in issue.context
    assert "Retry reproduces it." in issue.context
    assert "Only on empty carts." in issue.context
    assert not issue.truncated
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)
    assert requests[1].url.params["per_page"] == "11"


def test_issue_investigation_rejects_pull_request_issue_records() -> None:
    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"pull_request": {}})
            )
        )
    )

    with pytest.raises(ValueError, match="pull request"):
        client.issue_for_investigation("acme/demo", 17)


def test_latest_pull_request_uses_created_time_and_returns_distinct_timestamps() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "title": "Handle retry errors",
                    "state": "closed",
                    "user": {"login": "octocat"},
                    "created_at": "2026-10-31T09:00:00Z",
                    "updated_at": "2026-10-31T11:00:00Z",
                    "merged_at": "2026-10-31T10:00:00Z",
                    "html_url": "https://github.com/acme/demo/pull/42",
                }
            ],
        )

    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    pull_request = client.latest_pull_request("acme/demo")

    assert pull_request is not None
    assert pull_request.number == 42
    assert pull_request.created_at == "2026-10-31T09:00:00Z"
    assert pull_request.updated_at == "2026-10-31T11:00:00Z"
    assert pull_request.merged_at == "2026-10-31T10:00:00Z"
    assert requests[0].url.params["sort"] == "created"
    assert requests[0].url.params["direction"] == "desc"
    assert requests[0].url.params["per_page"] == "1"


def test_latest_pull_request_returns_none_for_empty_repository() -> None:
    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        )
    )

    assert client.latest_pull_request("acme/empty") is None


def test_latest_issue_skips_pull_requests_in_the_issues_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/issues")
        return httpx.Response(
            200,
            json=[
                {"number": 18, "title": "A PR is also an issue result", "pull_request": {}},
                {
                    "number": 17,
                    "title": "Checkout fails on retry",
                    "state": "open",
                    "user": {"login": "octocat"},
                    "created_at": "2026-10-31T09:00:00Z",
                    "html_url": "https://github.com/acme/demo/issues/17",
                },
            ],
        )

    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    issue = client.latest_issue("acme/demo")

    assert issue is not None
    assert issue.number == 17
    assert issue.title == "Checkout fails on retry"


def test_latest_commit_uses_default_branch_and_returns_changed_files() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/repos/acme/demo":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "deadbeef1234"}])
        return httpx.Response(
            200,
            json={
                "sha": "deadbeef1234",
                "html_url": "https://github.com/acme/demo/commit/deadbeef1234",
                "commit": {
                    "message": "Fix checkout retry\n\nMore details",
                    "committer": {"name": "Octocat", "date": "2026-10-31T09:00:00Z"},
                },
                "files": [
                    {
                        "filename": "checkout/retry.py",
                        "status": "modified",
                        "additions": 4,
                        "deletions": 2,
                    }
                ],
            },
        )

    client = GitHubActivityClient(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    commit = client.latest_commit("acme/demo")

    assert commit is not None
    assert commit.sha == "deadbeef1234"
    assert commit.message == "Fix checkout retry"
    assert commit.files[0].path == "checkout/retry.py"
    assert seen[1].url.params["sha"] == "main"


def test_activity_questions_are_classified_without_misrouting_code_questions() -> None:
    assert latest_activity_kind("When was the latest PR opened?") == "pull_request"
    assert latest_activity_kind("What is the last issue filed?") == "issue"
    assert latest_activity_kind("What changed in the latest commit?") == "commit"
    assert latest_activity_kind("Explain the commit function") is None
