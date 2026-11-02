"""Evidence-grounded code Q&A graph built with LangGraph."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from dev_assistant.github.activity import (
    format_latest_commit,
    format_latest_issue,
    format_latest_pull_request,
    latest_activity_kind,
)
from dev_assistant.search.models import SearchResult
from dev_assistant.search.service import CodeSearchService


class Answerer(Protocol):
    def answer(self, question: str, evidence: list[SearchResult]) -> str: ...


class AnalysisAnswerer(Protocol):
    def analyze(self, mode: str, question: str, evidence: list[SearchResult]) -> str: ...


class GitHubActivityLookup(Protocol):
    def latest_pull_request(self, repository_id: str) -> object | None: ...

    def latest_issue(self, repository_id: str) -> object | None: ...

    def latest_commit(self, repository_id: str) -> object | None: ...


class DependencyLookup(Protocol):
    def search(
        self, repository_id: str, commit_sha: str, query: str, *, top_k: int = 8
    ) -> list[SearchResult]: ...

    def search_calls(
        self, repository_id: str, commit_sha: str, query: str, *, top_k: int = 8
    ) -> list[SearchResult]: ...


class AgentState(TypedDict, total=False):
    repository_id: str
    commit_sha: str
    question: str
    path: str | None
    retrieval_query: str
    retrieval_hint: str
    external_context: str
    intent: str
    activity_kind: str
    workflow: str
    request_flow: bool
    retrieval_queries: list[str]
    extra_evidence: list[dict[str, object]]
    evidence: list[dict[str, object]]
    answer: str
    citations: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class SourceCitation:
    citation_id: str
    repository_id: str
    commit_sha: str
    path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: str


@dataclass(frozen=True, slots=True)
class CodeQAResponse:
    repository_id: str
    commit_sha: str
    question: str
    answer: str
    citations: tuple[SourceCitation, ...]


class CodeQAGraph:
    """Plan/retrieve/answer/verify workflow; no write-capable tools are exposed."""

    def __init__(
        self,
        search: CodeSearchService,
        answerer: Answerer,
        *,
        activity: GitHubActivityLookup | None = None,
        dependencies: DependencyLookup | None = None,
        top_k: int = 8,
        checkpointer: object | None = None,
    ) -> None:
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        self.search = search
        self.answerer = answerer
        self.activity = activity
        self.dependencies = dependencies
        self.top_k = top_k
        workflow = StateGraph(AgentState)
        workflow.add_node("plan", self._plan)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("answer", self._answer)
        workflow.add_node("github_activity", self._answer_latest_activity)
        workflow.add_node("no_evidence", self._no_evidence)
        workflow.add_node("verify", self._verify)
        workflow.add_edge(START, "plan")
        workflow.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "code": "retrieve",
                "architecture": "retrieve",
                "review": "retrieve",
                "bug_investigation": "retrieve",
                "test_generation": "retrieve",
                "github_activity": "github_activity",
            },
        )
        workflow.add_conditional_edges(
            "retrieve",
            self._route_after_retrieval,
            {"answer": "answer", "no_evidence": "no_evidence"},
        )
        workflow.add_edge("answer", "verify")
        workflow.add_edge("github_activity", "verify")
        workflow.add_edge("no_evidence", "verify")
        workflow.add_edge("verify", END)
        self._graph = workflow.compile(checkpointer=checkpointer or InMemorySaver())

    def ask(
        self,
        repository_id: str,
        commit_sha: str,
        question: str,
        *,
        path: str | None = None,
        extra_evidence: list[SearchResult] | None = None,
        retrieval_hint: str = "",
        external_context: str = "",
        thread_id: str | None = None,
    ) -> CodeQAResponse:
        supplemental_evidence = extra_evidence or []
        if len(retrieval_hint) > 3_000 or len(external_context) > 20_000:
            raise ValueError("Additional investigation context exceeds the supported size limit")
        if any(item.repository_id != repository_id for item in supplemental_evidence):
            raise ValueError("Supplemental evidence must belong to the requested repository")
        state = self._graph.invoke(
            {
                "repository_id": repository_id,
                "commit_sha": commit_sha,
                "question": question,
                "path": path,
                "extra_evidence": [asdict(item) for item in supplemental_evidence],
                "retrieval_hint": retrieval_hint,
                "external_context": external_context,
            },
            config={
                "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
                "recursion_limit": 8,
            },
        )
        return CodeQAResponse(
            repository_id=repository_id,
            commit_sha=commit_sha,
            question=question,
            answer=state["answer"],
            citations=tuple(SourceCitation(**item) for item in state.get("citations", [])),
        )

    @staticmethod
    def _plan(state: AgentState) -> dict[str, object]:
        """Preserve exact identifiers and infer a path when the user names one."""

        question = state["question"]
        quoted = re.findall(r"[`'\"]([^`'\"]+)[`'\"]", question)
        file_match = re.search(
            r"(?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|sql|md|json|ya?ml|toml)",
            question,
            re.IGNORECASE,
        )
        path = state.get("path") or (file_match.group(0) if file_match else None)
        retrieval_query = " ".join(
            value for value in [question, state.get("retrieval_hint", ""), *quoted] if value
        )
        activity_kind = latest_activity_kind(question)
        is_request_flow_question = bool(
            re.search(
                r"\b(api|request|routes?|endpoints?|flows?|pipeline|reaches|handled|entry points?)\b",
                question,
                re.IGNORECASE,
            )
        )
        is_architecture_question = is_request_flow_question or bool(
            re.search(
                r"\b(architecture|dependenc(?:y|ies)|depend(?:s|ing)? on|imports?|imported|project structure|service interactions|call graph|calls?)\b",
                question,
                re.IGNORECASE,
            )
        )
        workflow = "question"
        if re.search(r"\b(review|code review|security review)\b", question, re.IGNORECASE):
            workflow = "review"
        elif re.search(
            r"\b(investigate|debug|root cause|why (?:is|does|did|has|could|can|might)|bug)\b",
            question,
            re.IGNORECASE,
        ):
            workflow = "bug_investigation"
        elif re.search(
            r"\b(?:generate|write|create|suggest|propose)\b.{0,40}\btests?\b|\bunit tests?\b",
            question,
            re.IGNORECASE,
        ):
            workflow = "test_generation"
        intent = (
            "github_activity"
            if activity_kind
            else workflow
            if workflow != "question"
            else "architecture"
            if is_architecture_question
            else "code"
        )
        retrieval_queries = [retrieval_query]
        if is_request_flow_question:
            retrieval_queries.extend(
                [
                    "src/dev_assistant/api/routes.py ask_assistant /assistant/ask code question handler",
                    "src/dev_assistant/agents/factory.py create_code_qa_graph LangChainAnswerer graph wiring",
                    "src/dev_assistant/agents/graphs/code_qa.py CodeQAGraph ask plan retrieve answer workflow",
                    "src/dev_assistant/agents/graphs/code_qa.py _verify citations citation_id evidence validation",
                    "src/dev_assistant/agents/answering.py LangChainAnswerer prompt evidence citations",
                ]
            )
        elif is_architecture_question:
            retrieval_queries.extend(
                [
                    "repository entry points application startup configuration",
                    "API routes endpoints request handlers",
                    "module imports service dependencies calls",
                    "database connections models queries repositories",
                    "authentication authorization middleware",
                    "test files fixtures test configuration",
                    "package manifest dependencies build configuration",
                ]
            )
        return {
            "retrieval_query": retrieval_query,
            "retrieval_queries": retrieval_queries,
            "path": path,
            "intent": intent,
            "activity_kind": activity_kind or "",
            "workflow": workflow,
            "request_flow": is_request_flow_question,
        }

    @staticmethod
    def _route_after_plan(state: AgentState) -> str:
        return state.get("intent", "code")

    def _retrieve(self, state: AgentState) -> dict[str, object]:
        queries = state.get("retrieval_queries") or [
            state.get("retrieval_query", state["question"])
        ]
        evidence = list(state.get("extra_evidence", []))
        seen_chunk_ids = {item["chunk_id"] for item in evidence}
        is_architecture = state.get("intent") == "architecture"
        is_bug_investigation = state.get("workflow") == "bug_investigation"
        evidence_limit = self.top_k * (2 if is_architecture or is_bug_investigation else 1)
        semantic_limit = (
            min(evidence_limit, max(self.top_k, len(queries) * 2))
            if state.get("request_flow")
            else self.top_k
            if is_architecture
            else max(1, self.top_k // 2)
            if is_bug_investigation
            else evidence_limit
        )
        results_per_query = min(
            self.top_k,
            max(1, (semantic_limit + len(queries) - 1) // len(queries)),
        )
        for query in queries:
            results = self.search.search(
                state["repository_id"],
                state["commit_sha"],
                query,
                top_k=results_per_query,
                path=state.get("path"),
            )
            for result in results:
                if result.chunk_id not in seen_chunk_ids:
                    evidence.append(asdict(result))
                    seen_chunk_ids.add(result.chunk_id)
                if len(evidence) >= semantic_limit:
                    break
            if len(evidence) >= semantic_limit:
                break
        if is_architecture and self.dependencies is not None:
            dependencies = self.dependencies.search(
                state["repository_id"],
                state["commit_sha"],
                state.get("retrieval_query", state["question"]),
                top_k=evidence_limit - len(evidence),
            )
            for result in dependencies:
                if len(evidence) >= evidence_limit:
                    break
                if result.chunk_id not in seen_chunk_ids:
                    evidence.append(asdict(result))
                    seen_chunk_ids.add(result.chunk_id)
        call_search = getattr(self.dependencies, "search_calls", None)
        call_results: list[SearchResult] = []
        if (is_architecture or is_bug_investigation) and callable(call_search):
            call_results = call_search(
                state["repository_id"],
                state["commit_sha"],
                state.get("retrieval_query", state["question"]),
                top_k=max(1, self.top_k // 2),
            )
            for result in call_results:
                if len(evidence) >= evidence_limit:
                    break
                if result.chunk_id not in seen_chunk_ids:
                    evidence.append(asdict(result))
                    seen_chunk_ids.add(result.chunk_id)
        if is_bug_investigation:
            for call_result in call_results:
                if len(evidence) >= evidence_limit:
                    break
                target = re.search(r" in (.+):L\d+\.", call_result.content)
                if target is None:
                    continue
                target_path = target.group(1)
                target_results = self.search.search(
                    state["repository_id"],
                    state["commit_sha"],
                    call_result.symbol_name,
                    top_k=min(2, evidence_limit - len(evidence)),
                    path=target_path,
                )
                for result in target_results:
                    if len(evidence) >= evidence_limit:
                        break
                    if result.chunk_id not in seen_chunk_ids:
                        evidence.append(asdict(result))
                        seen_chunk_ids.add(result.chunk_id)
        return {"evidence": evidence}

    @staticmethod
    def _route_after_retrieval(state: AgentState) -> str:
        return "answer" if state.get("evidence") else "no_evidence"

    def _answer(self, state: AgentState) -> dict[str, str]:
        evidence = [SearchResult(**item) for item in state.get("evidence", [])]
        workflow = state.get("workflow", "question")
        if workflow != "question" and hasattr(self.answerer, "analyze"):
            answerer = self.answerer
            external_context = state.get("external_context", "")
            if external_context:
                return {
                    "answer": answerer.analyze(
                        workflow,
                        state["question"],
                        evidence,
                        external_context=external_context,
                    )
                }
            return {"answer": answerer.analyze(workflow, state["question"], evidence)}
        return {"answer": self.answerer.answer(state["question"], evidence)}

    def _answer_latest_activity(self, state: AgentState) -> dict[str, str]:
        if self.activity is None:
            return {"answer": "GitHub activity lookup is not configured for this assistant."}
        repository_id = state["repository_id"]
        kind = state.get("activity_kind")
        if kind == "pull_request":
            item = self.activity.latest_pull_request(repository_id)
            answer = format_latest_pull_request(repository_id, item)
        elif kind == "issue":
            item = self.activity.latest_issue(repository_id)
            answer = format_latest_issue(repository_id, item)
        elif kind == "commit":
            item = self.activity.latest_commit(repository_id)
            answer = format_latest_commit(repository_id, item)
        else:
            answer = "I couldn't determine which GitHub activity item to look up."
        return {"answer": answer}

    @staticmethod
    def _no_evidence(_: AgentState) -> dict[str, str]:
        return {
            "answer": "I couldn't find relevant source code in this repository snapshot. Try a more specific file, symbol, or behavior."
        }

    @staticmethod
    def _verify(state: AgentState) -> dict[str, object]:
        evidence = [SearchResult(**item) for item in state.get("evidence", [])]
        if not evidence:
            return {"citations": []}
        citation_by_id = {
            f"S{index}": SourceCitation(
                citation_id=f"S{index}",
                repository_id=item.repository_id,
                commit_sha=item.commit_sha,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                symbol_name=item.symbol_name,
                symbol_kind=item.symbol_kind,
            )
            for index, item in enumerate(evidence, start=1)
        }
        answer = state.get("answer", "")
        found = set(re.findall(r"\[(S\d+)\]", answer))
        valid = found & citation_by_id.keys()
        if found - citation_by_id.keys():
            answer = re.sub(
                r"\[(S\d+)\]",
                lambda match: match.group(0) if match.group(1) in citation_by_id else "",
                answer,
            )
        if not valid:
            return {
                "answer": (
                    "I couldn't verify an answer from the retrieved source citations. "
                    "Try asking about a specific file, symbol, or step in the flow."
                ),
                "citations": [],
            }
        return {
            "answer": answer,
            "citations": [
                asdict(citation_by_id[key])
                for key in sorted(valid, key=lambda value: int(value[1:]))
            ],
        }
