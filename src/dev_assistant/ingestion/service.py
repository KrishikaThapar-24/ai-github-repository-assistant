"""Application service that turns a repository snapshot into searchable chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dev_assistant.ingestion.chunking import CodeChunker
from dev_assistant.ingestion.models import CodeChunk, RepositorySnapshot
from dev_assistant.ingestion.source import RepositorySource


class ChunkIndexer(Protocol):
    def index(self, chunks: list[CodeChunk], *, batch_size: int = 128) -> int: ...


class DependencyIndexer(Protocol):
    def index_snapshot(self, snapshot: RepositorySnapshot) -> int: ...

    def index_calls(self, snapshot: RepositorySnapshot) -> int: ...


@dataclass(frozen=True, slots=True)
class IngestionReport:
    repository_id: str
    ref: str
    commit_sha: str
    file_count: int
    chunk_count: int
    dependency_count: int
    call_count: int


class IngestionService:
    def __init__(
        self,
        source: RepositorySource,
        indexer: ChunkIndexer,
        chunker: CodeChunker | None = None,
        dependency_indexer: DependencyIndexer | None = None,
    ) -> None:
        self.source = source
        self.indexer = indexer
        self.chunker = chunker or CodeChunker()
        self.dependency_indexer = dependency_indexer

    def ingest(self, repository_id: str, ref: str = "HEAD") -> IngestionReport:
        snapshot: RepositorySnapshot = self.source.load(repository_id, ref)
        chunks = self.chunker.chunk_snapshot(snapshot)
        indexed = self.indexer.index(chunks)
        dependency_count = (
            self.dependency_indexer.index_snapshot(snapshot)
            if self.dependency_indexer is not None
            else 0
        )
        call_count = (
            self.dependency_indexer.index_calls(snapshot)
            if self.dependency_indexer is not None
            else 0
        )
        return IngestionReport(
            repository_id,
            ref,
            snapshot.commit_sha,
            len(snapshot.files),
            indexed,
            dependency_count,
            call_count,
        )
