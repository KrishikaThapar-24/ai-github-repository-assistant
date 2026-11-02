"""Typed source, snapshot, and code-chunk records."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    content: str
    language: str


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    repository_id: str
    ref: str
    commit_sha: str
    files: tuple[SourceFile, ...]


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    repository_id: str
    commit_sha: str
    path: str
    language: str
    content: str
    start_line: int
    end_line: int
    symbol_name: str = ""
    symbol_kind: str = ""
