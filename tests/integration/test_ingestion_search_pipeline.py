import hashlib
import re
from pathlib import Path

from dev_assistant.ingestion.chunking import CodeChunker
from dev_assistant.ingestion.service import IngestionService
from dev_assistant.ingestion.source import LocalDirectorySource
from dev_assistant.search.lexical_index import SQLiteLexicalIndex
from dev_assistant.search.service import CodeSearchService
from dev_assistant.search.vector_store import ChromaCodeStore


class TinyEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * 64
        for token in re.findall(r"[a-zA-Z_][\w]*", text.lower()):
            vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(vector)] += 1
        return vector


def test_local_ingestion_indexes_searchable_symbol_chunks(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / "billing").mkdir(parents=True)
    (repository / "billing" / "charge.py").write_text(
        '"""Payment operations."""\n\ndef capture_payment(card):\n    """Charge a card and return the receipt."""\n    return payment_gateway.charge(card)\n',
        encoding="utf-8",
    )
    search = CodeSearchService(
        TinyEmbeddings(),
        ChromaCodeStore(tmp_path / "chroma"),
        SQLiteLexicalIndex(tmp_path / "lexical.sqlite3"),
    )
    ingestion = IngestionService(LocalDirectorySource(repository), search, CodeChunker())

    report = ingestion.ingest("acme/payments", "main")
    results = search.search("acme/payments", report.commit_sha, "capture payment card receipt")

    assert report.file_count == 1
    assert report.chunk_count >= 2
    assert results[0].path == "billing/charge.py"
    assert results[0].symbol_name == "capture_payment"
    assert results[0].citation.startswith("billing/charge.py:L")
