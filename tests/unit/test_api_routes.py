import time
from dataclasses import make_dataclass
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from dev_assistant.api import routes
from dev_assistant.api.schemas import (
    AskRequest,
    CodeSearchRequest,
    ConnectRepositoryRequest,
    IssueInvestigationRequest,
    PullRequestReviewRequest,
)
from dev_assistant.core.config import Settings


def test_connect_repository_returns_a_pollable_job(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "_ingest_repository",
        lambda repository_id, ref, settings: {
            "repository_id": repository_id,
            "ref": ref,
            "commit_sha": "deadbeef123",
            "file_count": 5,
            "chunk_count": 8,
        },
    )

    accepted = routes.connect_repository(
        ConnectRepositoryRequest(repository_id="acme/demo"), Settings()
    )
    assert accepted.status == "queued"

    for _ in range(100):
        job = routes.get_repository_job(accepted.job_id)
        if job.status in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    assert job.status == "succeeded"
    assert job.result["commit_sha"] == "deadbeef123"


def test_repository_ingestion_uses_the_cached_search_service(monkeypatch) -> None:
    from dev_assistant.code_intel import dependencies
    from dev_assistant.ingestion import service
    from dev_assistant.ingestion.service import IngestionReport

    search_service = object()
    source = object()
    dependency_index = object()

    class FakeIngestionService:
        def __init__(self, actual_source, indexer, *, dependency_indexer):
            assert actual_source is source
            assert indexer is search_service
            assert dependency_indexer is dependency_index

        def ingest(self, repository_id, ref):
            return IngestionReport(repository_id, ref, "deadbeef123", 2, 3, 1, 0)

    monkeypatch.setattr(routes, "_code_search_service", lambda: search_service)
    monkeypatch.setattr(routes, "create_repository_source", lambda settings: source)
    monkeypatch.setattr(dependencies, "SQLiteDependencyIndex", lambda path: dependency_index)
    monkeypatch.setattr(service, "IngestionService", FakeIngestionService)

    result = routes._ingest_repository("acme/demo", "main", Settings())

    assert result["chunk_count"] == 3
    assert result["commit_sha"] == "deadbeef123"


def test_search_endpoint_returns_ranked_source_locations(monkeypatch) -> None:
    from dev_assistant.search.models import SearchResult

    class FakeSearch:
        def search(self, repository_id, commit_sha, query, *, top_k, path):
            assert repository_id == "acme/demo"
            assert commit_sha == "deadbeef123"
            assert query == "checkout retry"
            assert top_k == 3
            assert path == "src/checkout.py"
            return [
                SearchResult(
                    "chunk-1",
                    repository_id,
                    commit_sha,
                    "src/checkout.py",
                    "python",
                    "def retry_checkout(): ...",
                    12,
                    18,
                    "retry_checkout",
                    "function",
                    0.8,
                )
            ]

    monkeypatch.setattr(routes, "_code_search_service", lambda: FakeSearch())

    response = routes.search_code(
        CodeSearchRequest(
            repository_id="acme/demo",
            commit_sha="deadbeef123",
            query="checkout retry",
            path="src/checkout.py",
            top_k=3,
        )
    )

    assert response.results[0].path == "src/checkout.py"
    assert response.results[0].start_line == 12
    assert response.results[0].symbol_name == "retry_checkout"


def test_unknown_job_raises_not_found() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        routes.get_repository_job("not-a-job")

    assert error.value.status_code == 404


def test_ask_endpoint_returns_answer_and_source_citations(monkeypatch) -> None:
    class FakeGraph:
        def ask(self, repository_id, commit_sha, question, *, path=None):
            Citation = make_dataclass(
                "Citation",
                [
                    ("citation_id", str),
                    ("repository_id", str),
                    ("commit_sha", str),
                    ("path", str),
                    ("start_line", int),
                    ("end_line", int),
                    ("symbol_name", str),
                    ("symbol_kind", str),
                ],
            )
            citation = Citation(
                "S1", repository_id, commit_sha, "auth.py", 12, 18, "verify", "function"
            )
            return SimpleNamespace(
                repository_id=repository_id,
                commit_sha=commit_sha,
                question=question,
                answer="The function checks the token [S1].",
                citations=(citation,),
            )

    monkeypatch.setattr(routes, "_qa_graph", lambda: FakeGraph())
    response = routes.ask_assistant(
        AskRequest(
            repository_id="acme/demo",
            commit_sha="deadbeef123",
            question="What does verify do?",
        )
    )

    assert response.citations[0].path == "auth.py"
    assert response.answer.endswith("[S1].")


def test_pull_request_question_does_not_initialize_llm_or_embeddings(monkeypatch) -> None:
    from dev_assistant.github.activity import PullRequestSummary

    class FakeActivity:
        def latest_pull_request(self, repository_id):
            assert repository_id == "acme/demo"
            return PullRequestSummary(
                8,
                "Improve caching",
                "closed",
                "octocat",
                "2026-10-31T09:00:00Z",
                "2026-10-31T10:00:00Z",
                None,
                "https://github.com/acme/demo/pull/8",
            )

    monkeypatch.setattr(routes, "_qa_graph", lambda: pytest.fail("LLM graph should not load"))
    monkeypatch.setattr(routes, "create_activity_client", lambda settings: FakeActivity())

    response = routes.ask_assistant(
        AskRequest(
            repository_id="acme/demo",
            commit_sha="activity-only",
            question="When was the last PR issued?",
        ),
        Settings(),
    )

    assert "#8: Improve caching" in response.answer
    assert response.citations == []


def test_review_endpoint_passes_pr_diff_to_agent_without_repository_writes(monkeypatch) -> None:
    from dev_assistant.github.activity import PullRequestReviewContext

    class FakeReviewClient:
        def pull_request_for_review(self, repository_id, pull_request_number):
            assert repository_id == "acme/demo"
            assert pull_request_number == 23
            return PullRequestReviewContext(
                23,
                "Harden checkout",
                "",
                "https://github.com/acme/demo/pull/23",
                "base12345678",
                "head12345678",
                """diff --git a/checkout.py b/checkout.py
--- a/checkout.py
+++ b/checkout.py
@@ -3,1 +3,2 @@
-return charge(total)
+if total < 0: raise ValueError()
+return charge(total)
""",
            )

    class FakeGraph:
        def ask(self, repository_id, commit_sha, question, *, extra_evidence):
            assert commit_sha == "base12345678"
            assert "Review the changed code" in question
            assert extra_evidence[0].commit_sha == "head12345678"
            assert extra_evidence[0].path == "checkout.py"
            from dev_assistant.agents.graphs.code_qa import SourceCitation

            citation = SourceCitation(
                "S1",
                repository_id,
                extra_evidence[0].commit_sha,
                extra_evidence[0].path,
                3,
                4,
                "PR #23 hunk 1",
                "pull_request_diff",
            )
            return SimpleNamespace(answer="Potential validation issue [S1].", citations=(citation,))

    monkeypatch.setattr(
        routes, "create_pull_request_review_client", lambda settings: FakeReviewClient()
    )
    monkeypatch.setattr(routes, "_qa_graph", lambda: FakeGraph())

    response = routes.review_pull_request(
        PullRequestReviewRequest(repository_id="acme/demo", pull_request_number=23), Settings()
    )

    assert response.title == "Harden checkout"
    assert response.head_sha == "head12345678"
    assert response.citations[0].symbol_kind == "pull_request_diff"


def test_issue_investigation_endpoint_passes_report_as_untrusted_context(monkeypatch) -> None:
    from dev_assistant.agents.graphs.code_qa import SourceCitation
    from dev_assistant.github.activity import IssueInvestigationContext

    class FakeIssueClient:
        def issue_for_investigation(self, repository_id, issue_number):
            assert repository_id == "acme/demo"
            assert issue_number == 17
            return IssueInvestigationContext(
                17,
                "Retry causes checkout failure",
                "https://github.com/acme/demo/issues/17",
                "Issue description:\nRetry fails after timeout.",
                False,
            )

    class FakeGraph:
        def ask(
            self,
            repository_id,
            commit_sha,
            question,
            *,
            retrieval_hint,
            external_context,
        ):
            assert commit_sha == "deadbeef123"
            assert "Investigate GitHub issue #17" in question
            assert "Retry fails after timeout" in retrieval_hint
            assert "Retry fails after timeout" in external_context
            citation = SourceCitation(
                "S1", repository_id, commit_sha, "checkout/retry.py", 20, 22, "retry", "function"
            )
            return SimpleNamespace(
                answer="The retry handler may drop state [S1].", citations=(citation,)
            )

    monkeypatch.setattr(
        routes, "create_issue_investigation_client", lambda settings: FakeIssueClient()
    )
    monkeypatch.setattr(routes, "_qa_graph", lambda: FakeGraph())

    response = routes.investigate_issue(
        IssueInvestigationRequest(
            repository_id="acme/demo", commit_sha="deadbeef123", issue_number=17
        ),
        Settings(),
    )

    assert response.issue_number == 17
    assert response.citations[0].path == "checkout/retry.py"
    assert not response.context_truncated


def test_request_schema_rejects_malformed_repository() -> None:
    with pytest.raises(ValidationError):
        ConnectRepositoryRequest(repository_id="https://github.com/acme/demo")


def test_openapi_contains_client_endpoints() -> None:
    from dev_assistant.api.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/repositories/connect" in paths
    assert "/api/v1/assistant/ask" in paths
    assert "/api/v1/assistant/search" in paths
    assert "/api/v1/assistant/review" in paths
    assert "/api/v1/assistant/investigate-issue" in paths
