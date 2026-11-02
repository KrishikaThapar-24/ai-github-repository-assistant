import hashlib
import re
import sqlite3
from pathlib import Path

from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.lexical_index import SQLiteLexicalIndex
from dev_assistant.search.service import CodeSearchService
from dev_assistant.search.vector_store import ChromaCodeStore


class TinyEmbeddings:
    """Deterministic bag-of-words embeddings for storage/retrieval tests only."""

    size = 128

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.size
        for token in re.findall(r"[a-zA-Z_][\w]*", text.lower()):
            index = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % self.size
            vector[index] += 1.0
        return vector


def make_chunk(
    repository: str, commit: str, path: str, name: str, body: str, start: int = 1
) -> CodeChunk:
    return CodeChunk(
        chunk_id=f"{repository}:{commit}:{path}:{name}",
        repository_id=repository,
        commit_sha=commit,
        path=path,
        language="python",
        content=body,
        start_line=start,
        end_line=start + body.count("\n"),
        symbol_name=name,
        symbol_kind="function",
    )


def test_hybrid_search_returns_citations_and_isolates_repository_and_snapshot(
    tmp_path: Path,
) -> None:
    vectors = ChromaCodeStore(tmp_path / "chroma")
    lexical = SQLiteLexicalIndex(tmp_path / "search.sqlite3")
    service = CodeSearchService(TinyEmbeddings(), vectors, lexical)
    service.index(
        [
            make_chunk(
                "acme/payments",
                "sha-1",
                "billing/charge.py",
                "capture_payment",
                "def capture_payment(card):\n    charge_card(card)\n    return paid",
                10,
            ),
            make_chunk(
                "acme/payments",
                "sha-1",
                "auth/login.py",
                "login",
                "def login(user):\n    return session",
            ),
            make_chunk(
                "acme/payments",
                "sha-2",
                "billing/charge.py",
                "new_payment",
                "def new_payment():\n    pass",
            ),
            make_chunk(
                "other/payments",
                "sha-1",
                "billing/charge.py",
                "capture_payment",
                "def capture_payment():\n    return other",
            ),
        ]
    )

    results = service.search("acme/payments", "sha-1", "capture payment", top_k=4)

    assert results
    assert results[0].symbol_name == "capture_payment"
    assert results[0].citation == "billing/charge.py:L10-L12"
    assert {result.repository_id for result in results} == {"acme/payments"}
    assert {result.commit_sha for result in results} == {"sha-1"}


def test_search_can_scope_to_a_file_and_reopen_persistent_indexes(tmp_path: Path) -> None:
    vector_path = tmp_path / "chroma"
    lexical_path = tmp_path / "search.sqlite3"
    vectors = ChromaCodeStore(vector_path)
    lexical = SQLiteLexicalIndex(lexical_path)
    service = CodeSearchService(TinyEmbeddings(), vectors, lexical)
    service.index(
        [
            make_chunk("acme/demo", "sha", "a.py", "one", "def one(): return token"),
            make_chunk("acme/demo", "sha", "b.py", "two", "def two(): return token"),
        ]
    )

    reopened = CodeSearchService(
        TinyEmbeddings(), ChromaCodeStore(vector_path), SQLiteLexicalIndex(lexical_path)
    )
    results = reopened.search("acme/demo", "sha", "token", path="b.py")

    assert [result.path for result in results] == ["b.py"]


def test_filename_terms_rank_the_implementation_over_a_related_test(tmp_path: Path) -> None:
    service = CodeSearchService(
        TinyEmbeddings(),
        ChromaCodeStore(tmp_path / "chroma"),
        SQLiteLexicalIndex(tmp_path / "search.sqlite3"),
    )
    service.index(
        [
            make_chunk(
                "acme/assistant",
                "sha",
                "apps/extension/sidepanel.js",
                "submitQuestion",
                "async function submitQuestion() { return sendRequest(); }",
            ),
            make_chunk(
                "acme/assistant",
                "sha",
                "apps/extension/sidepanel.test.mjs",
                "questionFormTest",
                "test the sidepanel.js question form request handler",
            ),
        ]
    )

    results = service.search(
        "acme/assistant", "sha", "apps/extension/sidepanel.js", top_k=2
    )

    assert results[0].path == "apps/extension/sidepanel.js"


def test_lexical_index_migrates_existing_rows_to_searchable_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    chunk = make_chunk(
        "acme/assistant",
        "sha",
        "src/repository_map.py",
        "build_repository_map",
        "return module_graph",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE VIRTUAL TABLE code_chunks_fts USING fts5(
                   content, chunk_id UNINDEXED, repository_id UNINDEXED, commit_sha UNINDEXED,
                   path UNINDEXED, language UNINDEXED, start_line UNINDEXED, end_line UNINDEXED,
                   symbol_name UNINDEXED, symbol_kind UNINDEXED, tokenize='unicode61'
               )"""
        )
        connection.execute(
            """INSERT INTO code_chunks_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk.content,
                chunk.chunk_id,
                chunk.repository_id,
                chunk.commit_sha,
                chunk.path,
                chunk.language,
                chunk.start_line,
                chunk.end_line,
                chunk.symbol_name,
                chunk.symbol_kind,
            ),
        )

    lexical = SQLiteLexicalIndex(database_path)
    results = lexical.search("acme/assistant", "sha", "repository_map.py")

    assert [result.path for result in results] == ["src/repository_map.py"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_chunks_fts'"
        ).fetchone() is None
