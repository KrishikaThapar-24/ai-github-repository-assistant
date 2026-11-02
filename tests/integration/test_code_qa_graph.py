import hashlib
import re
from pathlib import Path

from dev_assistant.agents.graphs.code_qa import CodeQAGraph
from dev_assistant.github.activity import (
    CommitFileSummary,
    CommitSummary,
    PullRequestReviewContext,
    PullRequestSummary,
)
from dev_assistant.github.pull_request_diff import pull_request_diff_evidence
from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.lexical_index import SQLiteLexicalIndex
from dev_assistant.search.models import SearchResult
from dev_assistant.search.service import CodeSearchService
from dev_assistant.search.vector_store import ChromaCodeStore


class TinyEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * 64
        for token in re.findall(r"[a-zA-Z_][\w]*", text.lower()):
            vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(vector)] += 1
        return vector


class FixedAnswerer:
    def __init__(self, answer: str) -> None:
        self.response = answer
        self.calls = 0
        self.seen_evidence = []

    def answer(self, question, evidence) -> str:
        self.calls += 1
        self.seen_evidence = evidence
        return self.response


class FixedActivityLookup:
    def __init__(self, pull_request: PullRequestSummary | None) -> None:
        self.pull_request = pull_request
        self.seen_repository = None

    def latest_pull_request(self, repository_id: str) -> PullRequestSummary | None:
        self.seen_repository = repository_id
        return self.pull_request


def build_service(tmp_path: Path) -> CodeSearchService:
    return CodeSearchService(
        TinyEmbeddings(),
        ChromaCodeStore(tmp_path / "chroma"),
        SQLiteLexicalIndex(tmp_path / "lexical.sqlite3"),
    )


def sample_chunk() -> CodeChunk:
    return CodeChunk(
        "chunk-1",
        "acme/payments",
        "sha-123",
        "billing/charge.py",
        "python",
        "def capture_payment(card):\n    return charge_card(card)",
        20,
        21,
        "capture_payment",
        "function",
    )


def test_langgraph_answers_from_retrieved_code_with_verified_source(tmp_path: Path) -> None:
    search = build_service(tmp_path)
    search.index([sample_chunk()])
    answerer = FixedAnswerer("The function delegates charging to charge_card [S1].")
    graph = CodeQAGraph(search, answerer)

    response = graph.ask("acme/payments", "sha-123", "What does capture_payment do?")

    assert response.answer == "The function delegates charging to charge_card [S1]."
    assert response.citations[0].path == "billing/charge.py"
    assert response.citations[0].start_line == 20
    assert response.citations[0].commit_sha == "sha-123"
    assert answerer.calls == 1


def test_graph_rejects_model_answer_when_it_omits_citations(tmp_path: Path) -> None:
    search = build_service(tmp_path)
    search.index([sample_chunk()])
    graph = CodeQAGraph(search, FixedAnswerer("It delegates to a payment processor."))

    response = graph.ask("acme/payments", "sha-123", "Explain capture_payment")

    assert "couldn't verify an answer" in response.answer
    assert response.citations == ()


def test_request_flow_plan_retrieves_route_factory_graph_verifier_and_answerer_sources() -> None:
    chunks = [
        SearchResult(
            "readme-flow",
            "acme/assistant",
            "sha-flow",
            "README.md",
            "markdown",
            "The assistant uses a FastAPI backend and LangGraph.",
            1,
            3,
            "",
            "",
            0.9,
        ),
        SearchResult(
            "route-flow",
            "acme/assistant",
            "sha-flow",
            "src/dev_assistant/api/routes.py",
            "python",
            "def ask_assistant(...): return _qa_graph().ask(...)",
            90,
            115,
            "ask_assistant",
            "function",
            0.9,
        ),
        SearchResult(
            "graph-flow",
            "acme/assistant",
            "sha-flow",
            "src/dev_assistant/agents/graphs/code_qa.py",
            "python",
            "The graph plans, retrieves evidence, answers, and verifies citations.",
            100,
            130,
            "CodeQAGraph",
            "class",
            0.9,
        ),
        SearchResult(
            "answer-flow",
            "acme/assistant",
            "sha-flow",
            "src/dev_assistant/agents/answering.py",
            "python",
            "The answer chain formats sources and asks the model for a cited response.",
            1,
            24,
            "LangChainAnswerer",
            "class",
            0.9,
        ),
        SearchResult(
            "factory-flow",
            "acme/assistant",
            "sha-flow",
            "src/dev_assistant/agents/factory.py",
            "python",
            "create_code_qa_graph wires LangChainAnswerer into CodeQAGraph.",
            12,
            24,
            "create_code_qa_graph",
            "function",
            0.9,
        ),
        SearchResult(
            "verify-flow",
            "acme/assistant",
            "sha-flow",
            "src/dev_assistant/agents/graphs/code_qa.py",
            "python",
            "_verify maps supplied [S#] IDs to source citations and rejects uncited answers.",
            350,
            380,
            "_verify",
            "method",
            0.9,
        ),
    ]

    class RequestFlowSearch:
        def __init__(self) -> None:
            self.queries = []

        def search(self, repository_id, commit_sha, query, *, top_k=8, path=None):
            self.queries.append(query)
            matching = [
                chunk
                for chunk in chunks
                if chunk.path in query
            ]
            matching.extend(chunk for chunk in chunks if chunk.path == "README.md")
            return matching[:top_k]

    search = RequestFlowSearch()
    answerer = FixedAnswerer("The API route calls the graph [S3].")
    graph = CodeQAGraph(search, answerer)

    response = graph.ask(
        "acme/assistant",
        "sha-flow",
        "Trace how a developer code question reaches LangGraph and gets answered with citations.",
    )

    evidence_paths = {item.path for item in answerer.seen_evidence}
    assert "src/dev_assistant/api/routes.py" in evidence_paths
    assert "src/dev_assistant/agents/factory.py" in evidence_paths
    assert "src/dev_assistant/agents/graphs/code_qa.py" in evidence_paths
    assert "src/dev_assistant/agents/answering.py" in evidence_paths
    assert any(item.symbol_name == "_verify" for item in answerer.seen_evidence)
    assert len(search.queries) >= 6
    assert response.citations
    assert response.citations[0].path in evidence_paths


def test_graph_returns_clear_no_evidence_answer_without_calling_model(tmp_path: Path) -> None:
    answerer = FixedAnswerer("should not be called")
    graph = CodeQAGraph(build_service(tmp_path), answerer)

    response = graph.ask("acme/empty", "sha-empty", "What does process_order do?")

    assert "couldn't find relevant source code" in response.answer
    assert response.citations == ()
    assert answerer.calls == 0


def test_plan_detects_a_named_file_and_limits_evidence_to_it(tmp_path: Path) -> None:
    search = build_service(tmp_path)
    other = CodeChunk(
        "chunk-2",
        "acme/payments",
        "sha-123",
        "auth/login.py",
        "python",
        "def login(user): return session",
        1,
        1,
        "login",
        "function",
    )
    search.index([sample_chunk(), other])
    answerer = FixedAnswerer("The function charges a card [S1].")
    graph = CodeQAGraph(search, answerer)

    response = graph.ask("acme/payments", "sha-123", "What does billing/charge.py do?")

    assert response.citations
    assert {item.path for item in response.citations} == {"billing/charge.py"}
    assert {item.path for item in answerer.seen_evidence} == {"billing/charge.py"}


def test_graph_routes_latest_pr_question_to_github_activity(tmp_path: Path) -> None:
    answerer = FixedAnswerer("This should not be called")
    activity = FixedActivityLookup(
        PullRequestSummary(
            42,
            "Handle retries",
            "closed",
            "octocat",
            "2026-10-31T09:00:00Z",
            "2026-10-31T11:00:00Z",
            "2026-10-31T10:00:00Z",
            "https://github.com/acme/payments/pull/42",
        )
    )
    graph = CodeQAGraph(build_service(tmp_path), answerer, activity=activity)

    response = graph.ask("acme/payments", "unused-for-activity", "When was the last PR issued?")

    assert "most recently opened pull request" in response.answer
    assert "#42: Handle retries" in response.answer
    assert "2026-10-31T09:00:00Z" in response.answer
    assert "merged at 2026-10-31T10:00:00Z" in response.answer
    assert response.citations == ()
    assert activity.seen_repository == "acme/payments"
    assert answerer.calls == 0


def test_graph_routes_latest_commit_question_to_github_activity(tmp_path: Path) -> None:
    class ActivityWithCommit(FixedActivityLookup):
        def latest_commit(self, repository_id: str) -> CommitSummary:
            self.seen_repository = repository_id
            return CommitSummary(
                "deadbeef12345678",
                "Fix checkout retries",
                "octocat",
                "2026-10-31T09:00:00Z",
                "https://github.com/acme/payments/commit/deadbeef12345678",
                (CommitFileSummary("checkout/retry.py", "modified", 5, 2),),
            )

    answerer = FixedAnswerer("This should not be called")
    activity = ActivityWithCommit(None)
    graph = CodeQAGraph(build_service(tmp_path), answerer, activity=activity)

    response = graph.ask("acme/payments", "activity-only", "What changed in the latest commit?")

    assert "Fix checkout retries" in response.answer
    assert "checkout/retry.py" in response.answer
    assert activity.seen_repository == "acme/payments"
    assert response.citations == ()
    assert answerer.calls == 0


def test_architecture_question_gathers_bounded_evidence_from_multiple_code_areas() -> None:
    class RecordingSearch:
        def __init__(self) -> None:
            self.queries = []

        def search(self, repository_id, commit_sha, query, *, top_k=8, path=None):
            self.queries.append(query)
            return [
                SearchResult(
                    "architecture-chunk",
                    repository_id,
                    commit_sha,
                    "src/api.py",
                    "python",
                    "def handle_request(): return payment_service.process()",
                    10,
                    10,
                    "handle_request",
                    "function",
                    0.9,
                )
            ]

    search = RecordingSearch()
    answerer = FixedAnswerer("The API handler delegates to a service [S1].")
    graph = CodeQAGraph(search, answerer)

    response = graph.ask("acme/payments", "sha-123", "Explain this repository architecture")

    assert any("entry points" in query for query in search.queries)
    assert any("database" in query for query in search.queries)
    assert any("authentication" in query for query in search.queries)
    assert len(search.queries) <= 8
    assert response.citations[0].path == "src/api.py"


def test_dependency_edges_join_architecture_evidence_with_real_source_lines() -> None:
    class EmptySearch:
        def search(self, *args, **kwargs):
            return []

    class FixedDependencies:
        def __init__(self) -> None:
            self.seen_query = None

        def search(self, repository_id, commit_sha, query, *, top_k=8):
            self.seen_query = query
            return [
                SearchResult(
                    "dependency-1",
                    repository_id,
                    commit_sha,
                    "src/api.py",
                    "python",
                    "Internal dependency: src/api.py imports app.services.billing from src/app/services/billing.py.\nfrom app.services import billing",
                    4,
                    4,
                    "app.services.billing",
                    "dependency",
                    1.0,
                )
            ]

    dependencies = FixedDependencies()
    answerer = FixedAnswerer("The API imports the billing service [S1].")
    graph = CodeQAGraph(EmptySearch(), answerer, dependencies=dependencies)

    response = graph.ask("acme/payments", "sha-123", "Explain module dependencies")

    assert dependencies.seen_query == "Explain module dependencies"
    assert response.citations[0].path == "src/api.py"
    assert response.citations[0].start_line == 4


def test_bug_investigation_retrieves_call_target_source_after_finding_call_edge() -> None:
    caller = SearchResult(
        "caller",
        "acme/shop",
        "sha-123",
        "src/api.py",
        "python",
        "def checkout(order): return billing.charge(order)",
        10,
        10,
        "checkout",
        "function",
        0.9,
    )
    callee = SearchResult(
        "callee",
        "acme/shop",
        "sha-123",
        "src/billing.py",
        "python",
        "def charge(order): return gateway.capture(order.total)",
        18,
        20,
        "charge",
        "function",
        0.95,
    )
    call_edge = SearchResult(
        "call-edge",
        "acme/shop",
        "sha-123",
        "src/api.py",
        "python",
        "Cross-file call: billing.charge(order) calls charge in src/billing.py:L18.",
        10,
        10,
        "charge",
        "call_site",
        1.0,
    )

    class TargetAwareSearch:
        def search(self, repository_id, commit_sha, query, *, top_k=8, path=None):
            return [callee] if path == "src/billing.py" else [caller]

    class CallIndex:
        def search(self, *args, **kwargs):
            return []

        def search_calls(self, *args, **kwargs):
            return [call_edge]

    answerer = FixedAnswerer(
        "The API delegates to the billing implementation [S1], which calls the gateway [S3]."
    )
    graph = CodeQAGraph(TargetAwareSearch(), answerer, dependencies=CallIndex())

    response = graph.ask("acme/shop", "sha-123", "Why could checkout fail?")

    assert any(
        item.path == "src/api.py" and item.symbol_kind == "call_site"
        for item in answerer.seen_evidence
    )
    assert any(
        item.path == "src/billing.py" and item.symbol_name == "charge"
        for item in answerer.seen_evidence
    )
    assert {citation.path for citation in response.citations} >= {"src/api.py", "src/billing.py"}


def test_issue_context_guides_retrieval_but_reaches_agent_as_separate_untrusted_data() -> None:
    class IssueAwareSearch:
        def __init__(self) -> None:
            self.queries = []

        def search(self, repository_id, commit_sha, query, *, top_k=8, path=None):
            self.queries.append(query)
            chunk = sample_chunk()
            return [
                SearchResult(
                    chunk.chunk_id,
                    chunk.repository_id,
                    chunk.commit_sha,
                    chunk.path,
                    chunk.language,
                    chunk.content,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.symbol_name,
                    chunk.symbol_kind,
                    0.9,
                )
            ]

    class IssueAwareAnswerer(FixedAnswerer):
        def __init__(self) -> None:
            super().__init__("The payment function could lose retry state [S1].")
            self.external_context = None

        def analyze(self, mode, question, evidence, *, external_context=""):
            assert mode == "bug_investigation"
            self.external_context = external_context
            self.seen_evidence = evidence
            return self.response

    search = IssueAwareSearch()
    answerer = IssueAwareAnswerer()
    graph = CodeQAGraph(search, answerer)
    report = "Issue description: timeout after checkout; retry duplicates payment."

    response = graph.ask(
        "acme/payments",
        "sha-123",
        "Investigate GitHub issue #17 using the supplied report.",
        retrieval_hint=report,
        external_context=report,
    )

    assert report in search.queries[0]
    assert answerer.external_context == report
    assert response.citations[0].path == "billing/charge.py"


def test_review_bug_and_test_requests_use_specialized_read_only_workflows(tmp_path: Path) -> None:
    class RecordingAnalysisAnswerer(FixedAnswerer):
        def __init__(self) -> None:
            super().__init__("Evidence-based result [S1].")
            self.modes = []
            self.evidence_by_mode = {}

        def analyze(self, mode, question, evidence):
            self.modes.append(mode)
            self.seen_evidence = evidence
            self.evidence_by_mode[mode] = evidence
            return self.response

    class RecordingGraphIndex:
        def __init__(self) -> None:
            self.call_queries = []

        def search(self, repository_id, commit_sha, query, *, top_k=8):
            return []

        def search_calls(self, repository_id, commit_sha, query, *, top_k=8):
            self.call_queries.append(query)
            return [
                SearchResult(
                    "call-checkout-billing",
                    repository_id,
                    commit_sha,
                    "src/api.py",
                    "python",
                    "Cross-file call: src/api.py calls charge in src/billing.py:L18.",
                    11,
                    11,
                    "charge",
                    "call_site",
                    1.0,
                )
            ]

    search = build_service(tmp_path)
    search.index([sample_chunk()])
    answerer = RecordingAnalysisAnswerer()
    graph_index = RecordingGraphIndex()
    graph = CodeQAGraph(search, answerer, dependencies=graph_index)

    questions = [
        ("Review this code", "review"),
        ("Why could this API be slow?", "bug_investigation"),
        ("Generate unit tests for capture_payment", "test_generation"),
    ]
    for question, expected_mode in questions:
        response = graph.ask("acme/payments", "sha-123", question)
        assert response.citations[0].path == "billing/charge.py"

    assert answerer.modes == [mode for _, mode in questions]
    assert graph_index.call_queries == ["Why could this API be slow?"]
    assert any(
        item.symbol_kind == "call_site" for item in answerer.evidence_by_mode["bug_investigation"]
    )


def test_langgraph_reviews_pr_diff_even_without_an_indexed_repository_snapshot() -> None:
    class EmptySearch:
        def search(self, *args, **kwargs):
            return []

    pull_request = PullRequestReviewContext(
        23,
        "Harden checkout",
        "",
        "https://github.com/acme/shop/pull/23",
        "base12345678",
        "head12345678",
        """diff --git a/checkout.py b/checkout.py
--- a/checkout.py
+++ b/checkout.py
@@ -10,1 +10,2 @@
-return charge(total)
+if total < 0: raise ValueError()
+return charge(total)
""",
    )
    evidence, truncated = pull_request_diff_evidence("acme/shop", pull_request)
    assert not truncated
    answerer = FixedAnswerer("The new negative total check may raise unexpectedly [S1].")
    graph = CodeQAGraph(EmptySearch(), answerer)

    response = graph.ask(
        "acme/shop",
        pull_request.base_sha,
        "Review the changed code in pull request #23",
        extra_evidence=evidence,
    )

    assert answerer.calls == 1
    assert answerer.seen_evidence[0].symbol_kind == "pull_request_diff"
    assert response.citations[0].commit_sha == pull_request.head_sha
    assert response.citations[0].path == "checkout.py"
    assert response.citations[0].start_line == 10
