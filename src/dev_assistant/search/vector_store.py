"""Repository-scoped persistent Chroma vector storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import chromadb

from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.models import SearchResult


class ChromaCodeStore:
    def __init__(self, persist_directory: Path) -> None:
        self.persist_directory = persist_directory
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.persist_directory))

    @staticmethod
    def _collection_name(repository_id: str) -> str:
        digest = hashlib.sha256(repository_id.encode("utf-8")).hexdigest()[:24]
        return f"repo_{digest}"

    def _collection(self, repository_id: str):
        return self._client.get_or_create_collection(
            name=self._collection_name(repository_id),
            metadata={"hnsw:space": "cosine", "repository_id": repository_id},
        )

    def upsert(self, chunks: list[CodeChunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Each code chunk must have exactly one embedding")
        grouped: dict[str, list[tuple[CodeChunk, list[float]]]] = {}
        for chunk, vector in zip(chunks, embeddings, strict=True):
            grouped.setdefault(chunk.repository_id, []).append((chunk, vector))
        for repository_id, rows in grouped.items():
            collection = self._collection(repository_id)
            collection.upsert(
                ids=[chunk.chunk_id for chunk, _ in rows],
                embeddings=[vector for _, vector in rows],
                documents=[chunk.content for chunk, _ in rows],
                metadatas=[self._metadata(chunk) for chunk, _ in rows],
            )

    def search(
        self,
        repository_id: str,
        commit_sha: str,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        path: str | None = None,
    ) -> list[SearchResult]:
        collection = self._collection(repository_id)
        where: dict[str, object] = {"commit_sha": commit_sha}
        if path:
            where = {"$and": [{"commit_sha": commit_sha}, {"path": path}]}
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=max(1, top_k),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            self._result(chunk_id, document or "", metadata or {}, 1.0 - float(distance))
            for chunk_id, document, metadata, distance in zip(
                ids, documents, metadatas, distances, strict=True
            )
        ]

    @staticmethod
    def _metadata(chunk: CodeChunk) -> dict[str, str | int]:
        return {
            "repository_id": chunk.repository_id,
            "commit_sha": chunk.commit_sha,
            "path": chunk.path,
            "language": chunk.language,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "symbol_name": chunk.symbol_name,
            "symbol_kind": chunk.symbol_kind,
        }

    @staticmethod
    def _result(
        chunk_id: str, content: str, metadata: dict[str, object], score: float
    ) -> SearchResult:
        return SearchResult(
            chunk_id=chunk_id,
            repository_id=str(metadata["repository_id"]),
            commit_sha=str(metadata["commit_sha"]),
            path=str(metadata["path"]),
            language=str(metadata["language"]),
            content=content,
            start_line=int(metadata["start_line"]),
            end_line=int(metadata["end_line"]),
            symbol_name=str(metadata.get("symbol_name", "")),
            symbol_kind=str(metadata.get("symbol_kind", "")),
            score=score,
        )
