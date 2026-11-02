import pytest

from dev_assistant.github.mcp_client import (
    READ_ONLY_TOOLS,
    DockerMCPToolInvoker,
    GitHubMCPActivityClient,
    _docker_arguments,
    _docker_environment,
)


class FakeInvoker:
    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke_read_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.calls.append((tool_name, arguments))
        return self.results[tool_name]


def test_mcp_activity_client_requests_only_latest_pull_request() -> None:
    invoker = FakeInvoker(
        {
            "list_pull_requests": [
                {
                    "number": 12,
                    "title": "Fix checkout retries",
                    "state": "closed",
                    "user": {"login": "octocat"},
                    "created_at": "2026-10-31T09:00:00Z",
                    "updated_at": "2026-10-31T10:00:00Z",
                    "merged_at": None,
                    "html_url": "https://github.com/acme/demo/pull/12",
                }
            ]
        }
    )

    result = GitHubMCPActivityClient(invoker).latest_pull_request("acme/demo")

    assert result is not None
    assert result.number == 12
    assert invoker.calls == [
        (
            "list_pull_requests",
            {
                "owner": "acme",
                "repo": "demo",
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "perPage": 1,
            },
        )
    ]


def test_mcp_activity_client_reads_latest_commit_detail() -> None:
    invoker = FakeInvoker(
        {
            "list_commits": [{"sha": "deadbeef123456"}],
            "get_commit": {
                "sha": "deadbeef123456",
                "html_url": "https://github.com/acme/demo/commit/deadbeef123456",
                "commit": {
                    "message": "Fix checkout retries",
                    "committer": {"name": "Octocat", "date": "2026-10-31T09:00:00Z"},
                },
                "files": [],
            },
        }
    )

    result = GitHubMCPActivityClient(invoker).latest_commit("acme/demo")

    assert result is not None
    assert result.message == "Fix checkout retries"
    assert [tool for tool, _ in invoker.calls] == ["list_commits", "get_commit"]
    assert invoker.calls[1][1]["detail"] == "stats"


def test_mcp_invoker_refuses_write_tool_names_before_starting_docker() -> None:
    invoker = DockerMCPToolInvoker(token="read-only-token")

    with pytest.raises(ValueError, match="read-only allowlist"):
        invoker.invoke_read_tool("create_issue", {"owner": "acme", "repo": "demo"})

    assert READ_ONLY_TOOLS == {
        "list_issues",
        "list_pull_requests",
        "list_commits",
        "get_commit",
    }


def test_mcp_server_starts_read_only_with_a_narrow_toolset() -> None:
    args = _docker_arguments("ghcr.io/github/github-mcp-server:test")
    environment = _docker_environment("secret-from-settings")

    assert args[0:3] == ["run", "-i", "--rm"]
    assert args[-1] == "ghcr.io/github/github-mcp-server:test"
    assert environment["GITHUB_READ_ONLY"] == "1"
    assert environment["GITHUB_TOOLSETS"] == "repos,issues,pull_requests"
    assert set(environment["GITHUB_TOOLS"].split(",")) == READ_ONLY_TOOLS
    assert "secret-from-settings" not in " ".join(args)


def test_mcp_backend_requires_a_configured_github_token() -> None:
    from dev_assistant.core.config import Settings
    from dev_assistant.github.factory import create_activity_client

    with pytest.raises(RuntimeError, match="read-only repository access"):
        create_activity_client(Settings(github_activity_backend="mcp"))
