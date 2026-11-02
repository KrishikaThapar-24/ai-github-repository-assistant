"""Search result records."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk_id: str
    repository_id: str
    commit_sha: str
    path: str
    language: str
    content: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: str
    score: float

    @property
    def citation(self) -> str:
        return f"{self.path}:L{self.start_line}-L{self.end_line}"
