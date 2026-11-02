"""Persistent SQLite FTS index for exact identifier and keyword retrieval."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.models import SearchResult

_INDEX_NAME = "code_chunks_fts_v2"
_LEGACY_INDEX_NAME = "code_chunks_fts"


class SQLiteLexicalIndex:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks_fts_v2 USING fts5(
                    content, path, symbol_name, symbol_kind,
                    chunk_id UNINDEXED, repository_id UNINDEXED, commit_sha UNINDEXED,
                    language UNINDEXED, start_line UNINDEXED, end_line UNINDEXED,
                    tokenize='unicode61'
                )"""
            )
            legacy_index_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_LEGACY_INDEX_NAME,),
            ).fetchone()
            if legacy_index_exists:
                connection.execute(
                    """INSERT INTO code_chunks_fts_v2 (
                           content, path, symbol_name, symbol_kind, chunk_id, repository_id,
                           commit_sha, language, start_line, end_line
                       )
                       SELECT old.content, old.path, old.symbol_name, old.symbol_kind,
                              old.chunk_id, old.repository_id, old.commit_sha, old.language,
                              old.start_line, old.end_line
                       FROM code_chunks_fts AS old
                       WHERE NOT EXISTS (
                           SELECT 1 FROM code_chunks_fts_v2 AS new
                           WHERE new.chunk_id = old.chunk_id
                       )"""
                )
                connection.execute("DROP TABLE code_chunks_fts")

    def upsert(self, chunks: list[CodeChunk]) -> None:
        with self._connection() as connection:
            connection.executemany(
                "DELETE FROM code_chunks_fts_v2 WHERE chunk_id = ?",
                [(chunk.chunk_id,) for chunk in chunks],
            )
            connection.executemany(
                """INSERT INTO code_chunks_fts_v2 (
                    content, path, symbol_name, symbol_kind, chunk_id, repository_id, commit_sha,
                    language, start_line, end_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chunk.content,
                        chunk.path,
                        chunk.symbol_name,
                        chunk.symbol_kind,
                        chunk.chunk_id,
                        chunk.repository_id,
                        chunk.commit_sha,
                        chunk.language,
                        chunk.start_line,
                        chunk.end_line,
                    )
                    for chunk in chunks
                ],
            )

    def search(
        self,
        repository_id: str,
        commit_sha: str,
        query: str,
        *,
        top_k: int = 10,
        path: str | None = None,
    ) -> list[SearchResult]:
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        sql = """SELECT chunk_id, content, repository_id, commit_sha, path, language,
                        start_line, end_line, symbol_name, symbol_kind,
                        bm25(code_chunks_fts_v2, 1.0, 4.0, 5.0, 1.0, 0.0, 0.0, 0.0,
                             0.0, 0.0, 0.0)
                 FROM code_chunks_fts_v2
                 WHERE code_chunks_fts_v2 MATCH ? AND repository_id = ? AND commit_sha = ?"""
        params: list[object] = [match_query, repository_id, commit_sha]
        if path:
            sql += " AND path = ?"
            params.append(path)
        sql += " ORDER BY bm25(code_chunks_fts_v2) LIMIT ?"
        params.append(max(1, top_k))
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            SearchResult(
                chunk_id=row[0],
                repository_id=row[2],
                commit_sha=row[3],
                path=row[4],
                language=row[5],
                start_line=int(row[6]),
                end_line=int(row[7]),
                symbol_name=row[8],
                symbol_kind=row[9],
                content=row[1],
                score=-float(row[10]),
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
