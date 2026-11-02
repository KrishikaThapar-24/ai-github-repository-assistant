from pathlib import Path

from dev_assistant.code_intel.dependencies import (
    PythonCallAnalyzer,
    PythonDependencyAnalyzer,
    SQLiteDependencyIndex,
)
from dev_assistant.ingestion.models import RepositorySnapshot, SourceFile
from dev_assistant.ingestion.service import IngestionService


def dependency_snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        "acme/service",
        "main",
        "deadbeef",
        (
            SourceFile(
                "src/dev_assistant/api/client.py",
                "import os\nfrom .routes import router\nfrom dev_assistant.core.config import Settings\n\ndef build_router():\n    return router()\n",
                "python",
            ),
            SourceFile(
                "src/dev_assistant/api/routes.py", "def router():\n    return object()\n", "python"
            ),
            SourceFile("src/dev_assistant/core/config.py", "class Settings: pass\n", "python"),
        ),
    )


def test_dependency_analyzer_resolves_absolute_and_relative_internal_imports() -> None:
    facts = PythonDependencyAnalyzer().analyze(dependency_snapshot())

    assert [(fact.target_module, fact.target_path, fact.line) for fact in facts] == [
        ("dev_assistant.api.routes", "src/dev_assistant/api/routes.py", 2),
        ("dev_assistant.core.config", "src/dev_assistant/core/config.py", 3),
    ]
    assert all(fact.source_path == "src/dev_assistant/api/client.py" for fact in facts)


def test_dependency_index_returns_cited_edges_and_reindexes_idempotently(tmp_path: Path) -> None:
    index = SQLiteDependencyIndex(tmp_path / "dependencies.sqlite3")
    snapshot = dependency_snapshot()

    assert index.index_snapshot(snapshot) == 2
    assert index.index_snapshot(snapshot) == 2
    results = index.search("acme/service", "deadbeef", "routes", top_k=5)

    assert len(results) == 1
    assert results[0].path == "src/dev_assistant/api/client.py"
    assert results[0].start_line == 2
    assert "src/dev_assistant/api/routes.py" in results[0].content


def test_ingestion_service_builds_dependency_index_for_the_pinned_snapshot(tmp_path: Path) -> None:
    class FixedSource:
        def load(self, repository_id: str, ref: str) -> RepositorySnapshot:
            return dependency_snapshot()

    class CountingIndexer:
        def index(self, chunks, *, batch_size=128):
            return len(chunks)

    dependency_index = SQLiteDependencyIndex(tmp_path / "dependencies.sqlite3")
    report = IngestionService(
        FixedSource(), CountingIndexer(), dependency_indexer=dependency_index
    ).ingest("acme/service", "main")

    assert report.dependency_count == 2
    assert report.call_count == 1
    results = dependency_index.search("acme/service", "deadbeef", "routes")
    assert results[0].start_line == 2
    call_results = dependency_index.search_calls("acme/service", "deadbeef", "router")
    assert call_results[0].start_line == 6


def test_call_analyzer_resolves_imported_module_calls_across_files(tmp_path: Path) -> None:
    snapshot = RepositorySnapshot(
        "acme/shop",
        "main",
        "cafe1234",
        (
            SourceFile(
                "src/shop/api.py",
                "from shop.services import billing\n\ndef checkout(order):\n    return billing.charge(order)\n",
                "python",
            ),
            SourceFile("src/shop/services/__init__.py", "", "python"),
            SourceFile(
                "src/shop/services/billing.py",
                "def charge(order):\n    return order.total\n",
                "python",
            ),
        ),
    )

    facts = PythonCallAnalyzer().analyze(snapshot)

    assert len(facts) == 1
    assert facts[0].source_path == "src/shop/api.py"
    assert facts[0].line == 4
    assert facts[0].target_symbol == "charge"
    assert facts[0].target_path == "src/shop/services/billing.py"
    assert facts[0].target_line == 1


def test_call_edges_are_persisted_idempotently_and_return_caller_citations(
    tmp_path: Path,
) -> None:
    snapshot = RepositorySnapshot(
        "acme/shop",
        "main",
        "cafe1234",
        (
            SourceFile(
                "src/shop/api.py",
                "from shop.services import billing\n\ndef checkout(order):\n    return billing.charge(order)\n",
                "python",
            ),
            SourceFile("src/shop/services/__init__.py", "", "python"),
            SourceFile(
                "src/shop/services/billing.py",
                "def charge(order):\n    return order.total\n",
                "python",
            ),
        ),
    )
    index = SQLiteDependencyIndex(tmp_path / "graph.sqlite3")

    assert index.index_calls(snapshot) == 1
    assert index.index_calls(snapshot) == 1
    results = index.search_calls("acme/shop", "cafe1234", "checkout billing charge")

    assert len(results) == 1
    assert results[0].path == "src/shop/api.py"
    assert results[0].start_line == 4
    assert "src/shop/services/billing.py:L1" in results[0].content
