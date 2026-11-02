"""Read-only adapter for GitHub's official MCP server over Docker stdio."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Protocol

from dev_assistant.github.activity import (
    CommitSummary,
    GitHubActivityClient,
    IssueSummary,
    PullRequestSummary,
)
from dev_assistant.github.source import GitHubSourceError

READ_ONLY_TOOLS = frozenset({"list_issues", "list_pull_requests", "list_commits", "get_commit"})


def _docker_arguments(image: str) -> list[str]:
    return [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "-e",
        "GITHUB_READ_ONLY",
        "-e",
        "GITHUB_TOOLSETS",
        "-e",
        "GITHUB_TOOLS",
        image,
    ]


def _docker_environment(token: str) -> dict[str, str]:
    return {
        "GITHUB_PERSONAL_ACCESS_TOKEN": token,
        "GITHUB_READ_ONLY": "1",
        "GITHUB_TOOLSETS": "repos,issues,pull_requests",
        "GITHUB_TOOLS": ",".join(sorted(READ_ONLY_TOOLS)),
    }


class ReadToolInvoker(Protocol):
    def invoke_read_tool(self, tool_name: str, arguments: dict[str, object]) -> object: ...


class DockerMCPToolInvoker:
    """Launch the upstream server for one operation with write tools disabled."""

    def __init__(
        self,
        *,
        token: str,
        command: str = "docker",
        image: str = "ghcr.io/github/github-mcp-server:latest",
        timeout_seconds: int = 90,
    ) -> None:
        self.token = token
        self.command = command
        self.image = image
        self.timeout_seconds = timeout_seconds

    def invoke_read_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        if tool_name not in READ_ONLY_TOOLS:
            raise ValueError(f"GitHub MCP tool '{tool_name}' is not on the read-only allowlist.")
        return asyncio.run(self._invoke(tool_name, arguments))

    async def _invoke(self, tool_name: str, arguments: dict[str, object]) -> object:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError(
                "Install GitHub MCP support with `uv sync --extra github-mcp`."
            ) from exc

        server = StdioServerParameters(
            command=self.command,
            args=_docker_arguments(self.image),
            env=_docker_environment(self.token),
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with stdio_client(server) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        available_tools = await session.list_tools()
                        if tool_name not in {tool.name for tool in available_tools.tools}:
                            raise GitHubSourceError(
                                f"The configured GitHub MCP server does not provide '{tool_name}'."
                            )
                        result = await session.call_tool(tool_name, arguments)
        except GitHubSourceError:
            raise
        except Exception as exc:
            raise GitHubSourceError(
                "Could not communicate with the GitHub MCP server. Check Docker, the token, and the server image."
            ) from exc
        if result.isError:
            raise GitHubSourceError(f"GitHub MCP tool '{tool_name}' returned an error.")
        if result.structuredContent is not None:
            return result.structuredContent
        text_blocks = [
            block.text for block in result.content if getattr(block, "type", "") == "text"
        ]
        if not text_blocks:
            return None
        return _parse_text_result("\n".join(text_blocks))


class GitHubMCPActivityClient:
    """Map common activity requests onto a strict allowlist of read-only MCP tools."""

    def __init__(self, invoker: ReadToolInvoker) -> None:
        self.invoker = invoker

    def latest_pull_request(self, repository_id: str) -> PullRequestSummary | None:
        owner, repo = GitHubActivityClient._parse_repository_id(repository_id)
        payload = self.invoker.invoke_read_tool(
            "list_pull_requests",
            {
                "owner": owner,
                "repo": repo,
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "perPage": 1,
            },
        )
        items = _items(payload, "pullRequests", "pull_requests")
        if not items:
            return None
        return GitHubActivityClient._pull_request_summary(items[0])

    def latest_issue(self, repository_id: str) -> IssueSummary | None:
        owner, repo = GitHubActivityClient._parse_repository_id(repository_id)
        payload = self.invoker.invoke_read_tool(
            "list_issues",
            {
                "owner": owner,
                "repo": repo,
                "state": "all",
                "orderBy": "CREATED_AT",
                "direction": "DESC",
                "perPage": 100,
            },
        )
        items = _items(payload, "issues")
        for item in items:
            if not any(key in item for key in ("pull_request", "pullRequest")) and not item.get(
                "isPullRequest"
            ):
                return GitHubActivityClient._issue_summary(item)
        if len(items) >= 100:
            raise GitHubSourceError(
                "The GitHub MCP result included only pull requests on its first page; issue ordering could not be confirmed."
            )
        return None

    def latest_commit(self, repository_id: str) -> CommitSummary | None:
        owner, repo = GitHubActivityClient._parse_repository_id(repository_id)
        payload = self.invoker.invoke_read_tool(
            "list_commits", {"owner": owner, "repo": repo, "perPage": 1}
        )
        items = _items(payload, "commits")
        if not items:
            return None
        commit = items[0]
        sha = str(commit.get("sha", ""))
        if not sha:
            raise GitHubSourceError("GitHub MCP returned a commit without a SHA.")
        detail = self.invoker.invoke_read_tool(
            "get_commit", {"owner": owner, "repo": repo, "sha": sha, "detail": "stats"}
        )
        if not isinstance(detail, Mapping):
            detail = commit
        return GitHubActivityClient._commit_summary(dict(detail))


def _items(payload: object, *collection_names: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for name in (*collection_names, "items", "data", "results"):
            value = payload.get(name)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
        if "number" in payload or "sha" in payload:
            return [dict(payload)]
    return []


def _parse_text_result(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min(
            (position for position in (text.find("["), text.find("{")) if position >= 0), default=-1
        )
        if start < 0:
            raise GitHubSourceError("GitHub MCP returned an unreadable result.")
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError as exc:
            raise GitHubSourceError("GitHub MCP returned an unreadable result.") from exc
