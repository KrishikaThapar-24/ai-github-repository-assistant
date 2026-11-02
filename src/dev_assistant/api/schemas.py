"""Transport models for the versioned API."""

from typing import Literal

from pydantic import BaseModel, Field


class ServiceStatusResponse(BaseModel):
    api_status: Literal["ready"]
    llm_provider: Literal["openai", "anthropic"]
    llm_configured: bool
    github_activity_backend: Literal["rest", "mcp"]
    github_token_configured: bool


class ConnectRepositoryRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ref: str = Field(default="HEAD", min_length=1, max_length=255)


class JobResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    result: dict[str, object] | None = None
    error: str | None = None


class AskRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(min_length=7, max_length=64)
    question: str = Field(min_length=3, max_length=4_000)
    path: str | None = Field(default=None, max_length=1_000)


class CodeSearchRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(min_length=7, max_length=64)
    query: str = Field(min_length=2, max_length=2_000)
    path: str | None = Field(default=None, max_length=1_000)
    top_k: int = Field(default=8, ge=1, le=20)


class PullRequestReviewRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pull_request_number: int = Field(ge=1, le=1_000_000_000)


class IssueInvestigationRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(min_length=7, max_length=64)
    issue_number: int = Field(ge=1, le=1_000_000_000)


class CitationResponse(BaseModel):
    citation_id: str
    repository_id: str
    commit_sha: str
    path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: str


class CodeSearchHitResponse(CitationResponse):
    language: str
    snippet: str
    score: float


class CodeSearchResponse(BaseModel):
    repository_id: str
    commit_sha: str
    query: str
    results: list[CodeSearchHitResponse]


class AskResponse(BaseModel):
    repository_id: str
    commit_sha: str
    question: str
    answer: str
    citations: list[CitationResponse]


class PullRequestReviewResponse(BaseModel):
    repository_id: str
    pull_request_number: int
    title: str
    url: str
    base_sha: str
    head_sha: str
    answer: str
    citations: list[CitationResponse]


class IssueInvestigationResponse(BaseModel):
    repository_id: str
    commit_sha: str
    issue_number: int
    title: str
    url: str
    context_truncated: bool
    answer: str
    citations: list[CitationResponse]
