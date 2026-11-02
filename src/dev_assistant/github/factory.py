"""GitHub integration assembly from application settings."""

from dev_assistant.core.config import Settings
from dev_assistant.github.activity import GitHubActivityClient
from dev_assistant.github.mcp_client import DockerMCPToolInvoker, GitHubMCPActivityClient
from dev_assistant.github.source import GitHubRepositorySource


def create_repository_source(settings: Settings) -> GitHubRepositorySource:
    token = settings.github_token.get_secret_value() if settings.github_token else None
    return GitHubRepositorySource(
        api_url=settings.github_api_url,
        token=token,
        timeout_seconds=settings.github_timeout_seconds,
        max_archive_bytes=settings.github_max_archive_bytes,
    )


def create_activity_client(
    settings: Settings,
) -> GitHubActivityClient | GitHubMCPActivityClient:
    token = settings.github_token.get_secret_value() if settings.github_token else None
    if settings.github_activity_backend == "mcp":
        if token is None:
            raise RuntimeError(
                "GitHub MCP mode needs DEV_ASSISTANT_GITHUB_TOKEN with read-only repository access."
            )
        return GitHubMCPActivityClient(
            DockerMCPToolInvoker(
                token=token,
                command=settings.github_mcp_command,
                image=settings.github_mcp_image,
                timeout_seconds=settings.github_mcp_timeout_seconds,
            )
        )
    return GitHubActivityClient(
        api_url=settings.github_api_url,
        token=token,
        timeout_seconds=settings.github_timeout_seconds,
        max_pull_request_diff_bytes=settings.github_max_pull_request_diff_bytes,
    )


def create_pull_request_review_client(settings: Settings) -> GitHubActivityClient:
    """Use the bounded read-only REST diff endpoint for pull request review input."""

    token = settings.github_token.get_secret_value() if settings.github_token else None
    return GitHubActivityClient(
        api_url=settings.github_api_url,
        token=token,
        timeout_seconds=settings.github_timeout_seconds,
        max_pull_request_diff_bytes=settings.github_max_pull_request_diff_bytes,
    )


def create_issue_investigation_client(settings: Settings) -> GitHubActivityClient:
    """Create the read-only REST adapter used to load issue reports and comments."""

    token = settings.github_token.get_secret_value() if settings.github_token else None
    return GitHubActivityClient(
        api_url=settings.github_api_url,
        token=token,
        timeout_seconds=settings.github_timeout_seconds,
    )
