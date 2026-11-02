"""Read-only repository activity queries for pull requests, issues, and commits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from dev_assistant.github.source import GitHubSourceError


@dataclass(frozen=True, slots=True)
class PullRequestSummary:
    number: int
    title: str
    state: str
    author: str
    created_at: str
    updated_at: str
    merged_at: str | None
    url: str


@dataclass(frozen=True, slots=True)
class PullRequestReviewContext:
    number: int
    title: str
    body: str
    url: str
    base_sha: str
    head_sha: str
    diff: str


@dataclass(frozen=True, slots=True)
class IssueSummary:
    number: int
    title: str
    state: str
    author: str
    created_at: str
    url: str


@dataclass(frozen=True, slots=True)
class IssueInvestigationContext:
    number: int
    title: str
    url: str
    context: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class CommitFileSummary:
    path: str
    status: str
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class CommitSummary:
    sha: str
    message: str
    author: str
    committed_at: str
    url: str
    files: tuple[CommitFileSummary, ...]


class GitHubActivityClient:
    """Small, read-only REST adapter for repository activity."""

    def __init__(
        self,
        *,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        timeout_seconds: int = 30,
        max_pull_request_diff_bytes: int = 2 * 1024 * 1024,
        client_factory: Any = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_pull_request_diff_bytes = max_pull_request_diff_bytes
        self._client_factory = client_factory or self._make_client

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_seconds)

    def latest_pull_request(self, repository_id: str) -> PullRequestSummary | None:
        owner, repository = self._parse_repository_id(repository_id)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with self._client_factory() as client:
            response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}/pulls",
                params={"state": "all", "sort": "created", "direction": "desc", "per_page": 1},
                headers=headers,
            )
        self._raise_for_github(response)
        items = response.json()
        if not items:
            return None
        return self._pull_request_summary(items[0])

    def pull_request_for_review(
        self, repository_id: str, pull_request_number: int
    ) -> PullRequestReviewContext:
        """Fetch PR metadata and a bounded unified diff using read-only requests."""

        if pull_request_number < 1:
            raise ValueError("Pull request number must be positive")
        owner, repository = self._parse_repository_id(repository_id)
        pull_request_url = f"{self.api_url}/repos/{owner}/{repository}/pulls/{pull_request_number}"
        headers = self._headers()
        with self._client_factory() as client:
            metadata_response = client.get(pull_request_url, headers=headers)
            self._raise_for_github(metadata_response)
            metadata = metadata_response.json()
            with client.stream(
                "GET",
                pull_request_url,
                headers={**headers, "Accept": "application/vnd.github.diff"},
            ) as diff_response:
                self._raise_for_github(diff_response)
                content_length = diff_response.headers.get("content-length")
                if content_length and int(content_length) > self.max_pull_request_diff_bytes:
                    raise GitHubSourceError(
                        "Pull request diff exceeds the configured review size limit."
                    )
                diff_buffer = bytearray()
                for chunk in diff_response.iter_bytes():
                    if len(diff_buffer) + len(chunk) > self.max_pull_request_diff_bytes:
                        raise GitHubSourceError(
                            "Pull request diff exceeds the configured review size limit."
                        )
                    diff_buffer.extend(chunk)
        base_sha = str((metadata.get("base") or {}).get("sha", ""))
        head_sha = str((metadata.get("head") or {}).get("sha", ""))
        if not base_sha or not head_sha:
            raise GitHubSourceError(
                "GitHub did not return the pull request's base and head commits."
            )
        return PullRequestReviewContext(
            number=int(metadata.get("number", pull_request_number)),
            title=str(metadata.get("title", "")),
            body=str(metadata.get("body") or "")[:10_000],
            url=str(metadata.get("html_url", "")),
            base_sha=base_sha,
            head_sha=head_sha,
            diff=diff_buffer.decode("utf-8", errors="replace"),
        )

    def latest_issue(self, repository_id: str) -> IssueSummary | None:
        owner, repository = self._parse_repository_id(repository_id)
        headers = self._headers()
        with self._client_factory() as client:
            for page in range(1, 11):
                response = client.get(
                    f"{self.api_url}/repos/{owner}/{repository}/issues",
                    params={
                        "state": "all",
                        "sort": "created",
                        "direction": "desc",
                        "per_page": 100,
                        "page": page,
                    },
                    headers=headers,
                )
                self._raise_for_github(response)
                items = response.json()
                for item in items:
                    if "pull_request" not in item:
                        return self._issue_summary(item)
                if len(items) < 100:
                    return None
        raise GitHubSourceError(
            "Could not confirm the newest issue within the bounded GitHub page limit."
        )

    def issue_for_investigation(
        self, repository_id: str, issue_number: int
    ) -> IssueInvestigationContext:
        """Fetch a bounded issue report and comments for read-only bug investigation."""

        if issue_number < 1:
            raise ValueError("Issue number must be positive")
        owner, repository = self._parse_repository_id(repository_id)
        issue_url = f"{self.api_url}/repos/{owner}/{repository}/issues/{issue_number}"
        headers = self._headers()
        with self._client_factory() as client:
            issue = self._bounded_json_get(client, issue_url, headers)
            if not isinstance(issue, dict):
                raise GitHubSourceError("GitHub returned an unreadable issue response.")
            if "pull_request" in issue:
                raise ValueError(
                    "This URL identifies a pull request. Use the pull request review workflow instead."
                )
            comment_response = self._bounded_json_get(
                client,
                f"{issue_url}/comments",
                headers,
                params={"per_page": 11, "page": 1},
            )
        comments = comment_response if isinstance(comment_response, list) else []
        context_parts = [
            f"Issue title:\n{issue.get('title', '')}",
            f"Issue description:\n{issue.get('body') or '(no description provided)'}",
        ]
        truncated = len(comments) > 10
        for comment in comments[:10]:
            if not isinstance(comment, dict):
                continue
            body = str(comment.get("body") or "")
            if len(body) > 2_000:
                body = body[:2_000]
                truncated = True
            author = str((comment.get("user") or {}).get("login", "unknown"))
            context_parts.append(f"Comment by @{author}:\n{body}")
        context = "\n\n".join(context_parts)
        if len(context) > 16_000:
            context = context[:16_000]
            truncated = True
        return IssueInvestigationContext(
            number=int(issue.get("number", issue_number)),
            title=str(issue.get("title", "")),
            url=str(issue.get("html_url", "")),
            context=context,
            truncated=truncated,
        )

    def _bounded_json_get(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        *,
        params: dict[str, int] | None = None,
        max_bytes: int = 1_000_000,
    ) -> object:
        with client.stream("GET", url, headers=headers, params=params) as response:
            self._raise_for_github(response)
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise GitHubSourceError("GitHub issue context exceeds the configured size limit.")
            payload = bytearray()
            for chunk in response.iter_bytes():
                if len(payload) + len(chunk) > max_bytes:
                    raise GitHubSourceError(
                        "GitHub issue context exceeds the configured size limit."
                    )
                payload.extend(chunk)
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GitHubSourceError("GitHub returned invalid issue JSON.") from exc

    def latest_commit(self, repository_id: str) -> CommitSummary | None:
        owner, repository = self._parse_repository_id(repository_id)
        headers = self._headers()
        with self._client_factory() as client:
            repository_response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}", headers=headers
            )
            self._raise_for_github(repository_response)
            branch = repository_response.json().get("default_branch")
            if not branch:
                raise GitHubSourceError("GitHub did not return the repository's default branch.")
            commits_response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}/commits",
                params={"sha": branch, "per_page": 1},
                headers=headers,
            )
            self._raise_for_github(commits_response)
            commits = commits_response.json()
            if not commits:
                return None
            sha = str(commits[0].get("sha", ""))
            if not sha:
                raise GitHubSourceError("GitHub returned a commit without a SHA.")
            detail_response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}/commits/{quote(sha, safe='')}",
                headers=headers,
            )
            self._raise_for_github(detail_response)
        return self._commit_summary(detail_response.json())

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _issue_summary(item: dict[str, Any]) -> IssueSummary:
        user = item.get("user") or {}
        return IssueSummary(
            number=int(item["number"]),
            title=str(item.get("title", "")),
            state=str(item.get("state", "unknown")),
            author=str(user.get("login", "unknown")),
            created_at=str(item.get("created_at", "")),
            url=str(item.get("html_url", "")),
        )

    @staticmethod
    def _commit_summary(item: dict[str, Any]) -> CommitSummary:
        commit = item.get("commit") or {}
        committer = commit.get("committer") or {}
        author = committer.get("name", "unknown")
        files = tuple(
            CommitFileSummary(
                path=str(file.get("filename", "")),
                status=str(file.get("status", "unknown")),
                additions=int(file.get("additions", 0)),
                deletions=int(file.get("deletions", 0)),
            )
            for file in item.get("files", [])
        )
        return CommitSummary(
            sha=str(item.get("sha", "")),
            message=str(commit.get("message", "")).splitlines()[0],
            author=str(author),
            committed_at=str(committer.get("date", "")),
            url=str(item.get("html_url", "")),
            files=files,
        )

    @staticmethod
    def _pull_request_summary(item: dict[str, Any]) -> PullRequestSummary:
        user = item.get("user") or {}
        return PullRequestSummary(
            number=int(item["number"]),
            title=str(item.get("title", "")),
            state=str(item.get("state", "unknown")),
            author=str(user.get("login", "unknown")),
            created_at=str(item.get("created_at", "")),
            updated_at=str(item.get("updated_at", "")),
            merged_at=item.get("merged_at"),
            url=str(item.get("html_url", "")),
        )

    @staticmethod
    def _parse_repository_id(repository_id: str) -> tuple[str, str]:
        pieces = repository_id.split("/")
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        if len(pieces) != 2 or any(
            not piece or piece in {".", ".."} or any(char not in allowed for char in piece)
            for piece in pieces
        ):
            raise ValueError("Repository must be given as owner/repository")
        return quote(pieces[0], safe=""), quote(pieces[1], safe="")

    @staticmethod
    def _raise_for_github(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise GitHubSourceError(
                "GitHub denied access. Check repository visibility and the token's read-only Contents, Issues, or Pull requests permission."
            )
        if response.status_code == 404:
            raise GitHubSourceError(
                "Repository was not found or the configured token cannot access it."
            )
        raise GitHubSourceError(
            f"GitHub returned HTTP {response.status_code} while reading activity."
        )


def is_latest_pull_request_question(question: str) -> bool:
    """Recognize common natural-language questions about the newest PR."""

    lowered = question.lower()
    asks_for_pull_request = bool(re.search(r"\b(pull requests?|prs?)\b", lowered))
    asks_for_latest = bool(re.search(r"\b(latest|last|newest|most recent|when)\b", lowered))
    return asks_for_pull_request and asks_for_latest


def latest_activity_kind(question: str) -> str | None:
    """Route explicit latest-item questions to the matching GitHub read tool."""

    lowered = question.lower()
    if is_latest_pull_request_question(question):
        return "pull_request"
    latest = bool(re.search(r"\b(latest|last|newest|most recent|when)\b", lowered))
    if latest and re.search(r"\b(issues?|bugs?)\b", lowered):
        return "issue"
    if latest and re.search(r"\b(commits?)\b", lowered):
        return "commit"
    return None


def format_latest_pull_request(repository_id: str, pull_request: PullRequestSummary | None) -> str:
    if pull_request is None:
        return f"No pull requests were found in {repository_id}."
    answer = (
        f"The most recently opened pull request is **#{pull_request.number}: "
        f"{pull_request.title}** by @{pull_request.author}. It was opened at "
        f"{pull_request.created_at} (UTC) and last updated at {pull_request.updated_at} (UTC)."
    )
    if pull_request.merged_at:
        answer += f" It was merged at {pull_request.merged_at} (UTC)."
    if pull_request.url:
        answer += f"\n\n[View pull request]({pull_request.url})"
    return answer


def format_latest_issue(repository_id: str, issue: IssueSummary | None) -> str:
    if issue is None:
        return f"No issues were found in {repository_id}."
    answer = (
        f"The most recently opened issue is **#{issue.number}: {issue.title}** by "
        f"@{issue.author}. It was opened at {issue.created_at} (UTC) and is {issue.state}."
    )
    if issue.url:
        answer += f"\n\n[View issue]({issue.url})"
    return answer


def format_latest_commit(repository_id: str, commit: CommitSummary | None) -> str:
    if commit is None:
        return f"No commits were found on the default branch of {repository_id}."
    answer = (
        f"The latest commit on the default branch is `{commit.sha[:12]}` by {commit.author} "
        f"at {commit.committed_at} (UTC): {commit.message}."
    )
    if commit.files:
        changed = ", ".join(file.path for file in commit.files[:8])
        answer += f" It changed {len(commit.files)} files: {changed}."
        additions = sum(file.additions for file in commit.files)
        deletions = sum(file.deletions for file in commit.files)
        answer += f" The diff reports +{additions}/-{deletions} lines."
    if commit.url:
        answer += f"\n\n[View commit]({commit.url})"
    return answer
