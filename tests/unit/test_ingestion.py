from pathlib import Path

from dev_assistant.ingestion.chunking import CodeChunker
from dev_assistant.ingestion.models import RepositorySnapshot, SourceFile
from dev_assistant.ingestion.source import LocalDirectorySource


def test_local_source_filters_generated_binary_and_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.py\nignored_dir/\n", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("ignored()", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x00image")
    (tmp_path / "large.py").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "ignored.py").write_text("secret = True", encoding="utf-8")
    (tmp_path / "ignored_dir" / "hidden.py").write_text("hidden = True", encoding="utf-8")

    snapshot = LocalDirectorySource(tmp_path, max_file_bytes=50).load("acme/demo", "main")

    assert [source.path for source in snapshot.files] == ["src/main.py"]
    assert snapshot.repository_id == "acme/demo"
    assert snapshot.ref == "main"
    assert len(snapshot.commit_sha) == 64


def test_local_source_snapshot_id_is_stable_and_changes_with_content(tmp_path: Path) -> None:
    code = tmp_path / "main.py"
    code.write_text("answer = 1\n", encoding="utf-8")
    source = LocalDirectorySource(tmp_path)
    first = source.load("acme/demo")
    second = source.load("acme/demo")
    code.write_text("answer = 2\n", encoding="utf-8")
    changed = source.load("acme/demo")

    assert first.commit_sha == second.commit_sha
    assert first.commit_sha != changed.commit_sha


def test_chunker_extracts_python_symbols_and_preserves_line_citations() -> None:
    source = SourceFile(
        "src/service.py",
        '"""Service module."""\n\nclass BillingService:\n    def charge(self, amount):\n        return amount\n',
        "python",
    )
    snapshot = RepositorySnapshot("acme/demo", "main", "abc123", (source,))

    chunks = CodeChunker().chunk_snapshot(snapshot)
    symbols = {(chunk.symbol_kind, chunk.symbol_name) for chunk in chunks if chunk.symbol_name}

    assert ("class", "BillingService") in symbols
    assert ("method", "charge") in symbols
    charge = next(chunk for chunk in chunks if chunk.symbol_name == "charge")
    assert charge.path == "src/service.py"
    assert charge.start_line == 4
    assert "return amount" in charge.content


def test_chunker_finds_typescript_classes_and_methods_without_runtime_grammar_download() -> None:
    source = SourceFile(
        "src/checkout.ts",
        "export class CheckoutService {\n  async submit(order: Order) {\n    return save(order);\n  }\n}\n",
        "typescript",
    )
    snapshot = RepositorySnapshot("acme/demo", "main", "abc123", (source,))

    chunks = CodeChunker().chunk_snapshot(snapshot)
    names = {chunk.symbol_name for chunk in chunks}

    assert "CheckoutService" in names
    assert "submit" in names
