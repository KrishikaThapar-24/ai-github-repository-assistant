"""Persistent SQLite FTS index for exact identifier and keyword retrieval."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.models import SearchResult


class SQLiteLexicalIndex:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks_fts USING fts5(
                    content, chunk_id UNINDEXED, repository_id UNINDEXED, commit_sha UNINDEXED,
                    path UNINDEXED, language UNINDEXED, start_line UNINDEXED, end_line UNINDEXED,
                    symbol_name UNINDEXED, symbol_kind UNINDEXED, tokenize='unicode61'
                )"""
            )

    def upsert(self, chunks: list[CodeChunk]) -> None:
        with self._connection() as connection:
            connection.executemany(
                "DELETE FROM code_chunks_fts WHERE chunk_id = ?",
                [(chunk.chunk_id,) for chunk in chunks],
            )
            connection.executemany(
                """INSERT INTO code_chunks_fts (
                    content, chunk_id, repository_id, commit_sha, path, language, start_line,
                    end_line, symbol_name, symbol_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
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
                        start_line, end_line, symbol_name, symbol_kind, bm25(code_chunks_fts)
                 FROM code_chunks_fts
                 WHERE code_chunks_fts MATCH ? AND repository_id = ? AND commit_sha = ?"""
        params: list[object] = [match_query, repository_id, commit_sha]
        if path:
            sql += " AND path = ?"
            params.append(path)
        sql += " ORDER BY bm25(code_chunks_fts) LIMIT ?"
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
