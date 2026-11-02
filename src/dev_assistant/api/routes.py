"""HTTP endpoints used by the local clients."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from dev_assistant.api.schemas import (
    AskRequest,
    AskResponse,
    CodeSearchHitResponse,
    CodeSearchRequest,
    CodeSearchResponse,
    ConnectRepositoryRequest,
    IssueInvestigationRequest,
    IssueInvestigationResponse,
    JobResponse,
    PullRequestReviewRequest,
    PullRequestReviewResponse,
    ServiceStatusResponse,
)
from dev_assistant.core.config import Settings, get_settings
from dev_assistant.github.activity import (
    format_latest_commit,
    format_latest_issue,
    format_latest_pull_request,
    latest_activity_kind,
)
from dev_assistant.github.factory import (
    create_activity_client,
    create_issue_investigation_client,
    create_pull_request_review_client,
    create_repository_source,
)
from dev_assistant.github.pull_request_diff import pull_request_diff_evidence
from dev_assistant.worker.jobs import InMemoryJobRunner

router = APIRouter(prefix="/api/v1")
jobs = InMemoryJobRunner()


@router.get("/status", response_model=ServiceStatusResponse)
def get_service_status(settings: Settings = Depends(get_settings)) -> ServiceStatusResponse:
    """Report configured capabilities without returning any credential values."""

    llm_configured = (
        settings.openai_api_key is not None
        if settings.llm_provider == "openai"
        else settings.anthropic_api_key is not None
    )
    return ServiceStatusResponse(
        api_status="ready",
        llm_provider=settings.llm_provider,
        llm_configured=llm_configured,
        github_activity_backend=settings.github_activity_backend,
        github_token_configured=settings.github_token is not None,
    )


@lru_cache(maxsize=1)
def _code_search_service():
    from dev_assistant.agents.factory import create_search_service

    return create_search_service(get_settings())


@lru_cache(maxsize=1)
def _qa_graph():
    from dev_assistant.agents.factory import create_code_qa_graph

    return create_code_qa_graph(get_settings(), search_service=_code_search_service())


def _ingest_repository(repository_id: str, ref: str, settings: Settings) -> dict[str, object]:
    from dev_assistant.code_intel.dependencies import SQLiteDependencyIndex
    from dev_assistant.ingestion.service import IngestionService

    ingestion = IngestionService(
        create_repository_source(settings),
        _code_search_service(),
        dependency_indexer=SQLiteDependencyIndex(settings.dependency_database_path),
    )
    report = ingestion.ingest(repository_id, ref)
    return asdict(report)


@router.post(
    "/repositories/connect", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED
)
def connect_repository(
    request: ConnectRepositoryRequest,
    settings: Settings = Depends(get_settings),
) -> JobResponse:
    """Start a read-only repository snapshot and indexing job."""

    job = jobs.submit(
        lambda: _ingest_repository(request.repository_id, request.ref, settings),
        dedupe_key=(request.repository_id, request.ref),
    )
    return JobResponse(**asdict(job))


@router.get("/repositories/jobs/{job_id}", response_model=JobResponse)
def get_repository_job(job_id: str) -> JobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Repository job was not found.")
    return JobResponse(**asdict(job))


@router.post("/assistant/search", response_model=CodeSearchResponse)
def search_code(
    request: CodeSearchRequest,
) -> CodeSearchResponse:
    """Return ranked hybrid-search matches with exact source locations."""

    try:
        results = _code_search_service().search(
            request.repository_id,
            request.commit_sha,
            request.query,
            top_k=request.top_k,
            path=request.path,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CodeSearchResponse(
        repository_id=request.repository_id,
        commit_sha=request.commit_sha,
        query=request.query,
        results=[
            CodeSearchHitResponse(
                citation_id=f"S{index}",
                repository_id=result.repository_id,
                commit_sha=result.commit_sha,
                path=result.path,
                start_line=result.start_line,
                end_line=result.end_line,
                symbol_name=result.symbol_name,
                symbol_kind=result.symbol_kind,
                language=result.language,
                snippet=result.content,
                score=result.score,
            )
            for index, result in enumerate(results, start=1)
        ],
    )


@router.post("/assistant/ask", response_model=AskResponse)
def ask_assistant(
    request: AskRequest,
    settings: Settings = Depends(get_settings),
) -> AskResponse:
    """Answer a codebase or repository activity question."""

    activity_kind = latest_activity_kind(request.question)
    if activity_kind:
        try:
            client = create_activity_client(settings)
            item = getattr(client, f"latest_{activity_kind}")(request.repository_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        formatters = {
            "pull_request": format_latest_pull_request,
            "issue": format_latest_issue,
            "commit": format_latest_commit,
        }
        return AskResponse(
            repository_id=request.repository_id,
            commit_sha=request.commit_sha,
            question=request.question,
            answer=formatters[activity_kind](request.repository_id, item),
            citations=[],
        )

    try:
        response = _qa_graph().ask(
            request.repository_id,
            request.commit_sha,
            request.question,
            path=request.path,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AskResponse(
        repository_id=response.repository_id,
        commit_sha=response.commit_sha,
        question=response.question,
        answer=response.answer,
        citations=[asdict(citation) for citation in response.citations],
    )


@router.post("/assistant/review", response_model=PullRequestReviewResponse)
def review_pull_request(
    request: PullRequestReviewRequest,
    settings: Settings = Depends(get_settings),
) -> PullRequestReviewResponse:
    """Review a GitHub pull request diff without posting a review or changing GitHub."""

    try:
        pull_request = create_pull_request_review_client(settings).pull_request_for_review(
            request.repository_id, request.pull_request_number
        )
        evidence, truncated = pull_request_diff_evidence(request.repository_id, pull_request)
        if not evidence:
            raise HTTPException(
                status_code=422,
                detail="This pull request has no supported text changes to review.",
            )
        question = (
            f"Review the changed code in pull request #{pull_request.number}. "
            "Identify only actionable correctness, security, or reliability findings, "
            "and distinguish confirmed issues from uncertainty."
        )
        if truncated:
            question += (
                " The supplied diff evidence is bounded and may omit later hunks; disclose "
                "that the review is partial."
            )
        response = _qa_graph().ask(
            request.repository_id,
            pull_request.base_sha,
            question,
            extra_evidence=evidence,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="GitHub could not provide the pull request diff."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PullRequestReviewResponse(
        repository_id=request.repository_id,
        pull_request_number=pull_request.number,
        title=pull_request.title,
        url=pull_request.url,
        base_sha=pull_request.base_sha,
        head_sha=pull_request.head_sha,
        answer=response.answer,
        citations=[asdict(citation) for citation in response.citations],
    )


@router.post("/assistant/investigate-issue", response_model=IssueInvestigationResponse)
def investigate_issue(
    request: IssueInvestigationRequest,
    settings: Settings = Depends(get_settings),
) -> IssueInvestigationResponse:
    """Investigate a read-only GitHub issue against an indexed code snapshot."""

    try:
        issue = create_issue_investigation_client(settings).issue_for_investigation(
            request.repository_id, request.issue_number
        )
        retrieval_hint = issue.context[:3_000]
        question = (
            f"Investigate GitHub issue #{issue.number} using the supplied issue report. "
            "Trace the described behavior through the repository, rank likely root causes, "
            "and distinguish findings from hypotheses."
        )
        if issue.truncated:
            question += " The issue context was truncated; disclose that limitation."
        response = _qa_graph().ask(
            request.repository_id,
            request.commit_sha,
            question,
            retrieval_hint=retrieval_hint,
            external_context=issue.context,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="GitHub could not provide the issue report."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IssueInvestigationResponse(
        repository_id=request.repository_id,
        commit_sha=request.commit_sha,
        issue_number=issue.number,
        title=issue.title,
        url=issue.url,
        context_truncated=issue.truncated,
        answer=response.answer,
        citations=[asdict(citation) for citation in response.citations],
    )
