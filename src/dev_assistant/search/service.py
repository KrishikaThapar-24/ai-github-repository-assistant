"""Hybrid lexical and semantic retrieval with reciprocal-rank fusion."""

from __future__ import annotations

import re
from dataclasses import replace

from dev_assistant.ingestion.models import CodeChunk
from dev_assistant.search.embeddings import EmbeddingProvider
from dev_assistant.search.lexical_index import SQLiteLexicalIndex
from dev_assistant.search.models import SearchResult
from dev_assistant.search.vector_store import ChromaCodeStore


class CodeSearchService:
    def __init__(
        self, embeddings: EmbeddingProvider, vectors: ChromaCodeStore, lexical: SQLiteLexicalIndex
    ) -> None:
        self.embeddings = embeddings
        self.vectors = vectors
        self.lexical = lexical

    def index(self, chunks: list[CodeChunk], *, batch_size: int = 128) -> int:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embeddings.embed_documents([chunk.content for chunk in batch])
            self.vectors.upsert(batch, vectors)
            self.lexical.upsert(batch)
        return len(chunks)

    def search(
        self,
        repository_id: str,
        commit_sha: str,
        query: str,
        *,
        top_k: int = 8,
        path: str | None = None,
    ) -> list[SearchResult]:
        semantic = self.vectors.search(
            repository_id,
            commit_sha,
            self.embeddings.embed_query(query),
            top_k=top_k * 2,
            path=path,
        )
        lexical = self.lexical.search(repository_id, commit_sha, query, top_k=top_k * 2, path=path)
        fused: dict[str, tuple[SearchResult, float]] = {}
        for rank, result in enumerate(semantic, start=1):
            fused[result.chunk_id] = (result, 1.0 / (60 + rank))
        for rank, result in enumerate(lexical, start=1):
            previous = fused.get(result.chunk_id)
            fused[result.chunk_id] = (
                result,
                (previous[1] if previous else 0.0) + 1.0 / (60 + rank),
            )
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        boosted = []
        for result, score in fused.values():
            symbol_terms = set(re.findall(r"[a-z0-9]+", result.symbol_name.lower()))
            if symbol_terms and symbol_terms <= query_terms:
                score += 0.05
            boosted.append((result, score))
        ordered = sorted(boosted, key=lambda item: item[1], reverse=True)
        return [replace(result, score=score) for result, score in ordered[:top_k]]
