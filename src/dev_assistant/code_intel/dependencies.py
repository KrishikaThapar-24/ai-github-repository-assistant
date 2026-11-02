"""Python import graph extraction and persistent dependency lookup."""

from __future__ import annotations

import ast
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from dev_assistant.ingestion.models import RepositorySnapshot, SourceFile
from dev_assistant.search.models import SearchResult


@dataclass(frozen=True, slots=True)
class DependencyFact:
    repository_id: str
    commit_sha: str
    source_path: str
    line: int
    target_module: str
    target_path: str
    content: str


@dataclass(frozen=True, slots=True)
class CallFact:
    repository_id: str
    commit_sha: str
    source_path: str
    line: int
    target_symbol: str
    target_path: str
    target_line: int
    content: str


class PythonDependencyAnalyzer:
    """Resolve imports between Python files in one repository snapshot."""

    def analyze(self, snapshot: RepositorySnapshot) -> list[DependencyFact]:
        module_paths = self._module_paths(snapshot.files)
        facts: list[DependencyFact] = []
        for source_file in snapshot.files:
            if source_file.language != "python":
                continue
            try:
                module = ast.parse(source_file.content)
            except SyntaxError:
                continue
            source_module = self._module_name(source_file.path)
            package = (
                source_module
                if source_file.path.endswith("/__init__.py")
                else source_module.rpartition(".")[0]
            )
            for node in ast.walk(module):
                imported_modules = self._imported_modules(node, package)
                for target_module in imported_modules:
                    target_path = self._resolve_module(target_module, module_paths)
                    if target_path is None or target_path == source_file.path:
                        continue
                    snippet = ast.get_source_segment(source_file.content, node) or ""
                    facts.append(
                        DependencyFact(
                            snapshot.repository_id,
                            snapshot.commit_sha,
                            source_file.path,
                            node.lineno,
                            target_module,
                            target_path,
                            snippet.strip(),
                        )
                    )
        return sorted(set(facts), key=lambda fact: (fact.source_path, fact.line, fact.target_path))

    @staticmethod
    def _module_name(path: str) -> str:
        parts = Path(path).with_suffix("").parts
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    @classmethod
    def _module_paths(cls, files: tuple[SourceFile, ...]) -> dict[str, str]:
        paths: dict[str, str] = {}
        for source_file in files:
            if source_file.language != "python":
                continue
            module = cls._module_name(source_file.path)
            if module:
                paths[module] = source_file.path
        return paths

    @staticmethod
    def _imported_modules(node: ast.AST, package: str) -> set[str]:
        if isinstance(node, ast.Import):
            return {alias.name for alias in node.names}
        if not isinstance(node, ast.ImportFrom):
            return set()
        base_parts = package.split(".") if package and node.level else []
        if node.level:
            base_parts = base_parts[: max(0, len(base_parts) - node.level + 1)]
        if node.module:
            base_parts.extend(node.module.split("."))
        base = ".".join(part for part in base_parts if part)
        imported = {base} if base else set()
        if node.module is None:
            imported.update(f"{base}.{alias.name}" for alias in node.names if base)
        return imported

    @staticmethod
    def _resolve_module(module: str, module_paths: dict[str, str]) -> str | None:
        if module in module_paths:
            return module_paths[module]
        parts = module.split(".")
        for position in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:position])
            if parent in module_paths:
                return module_paths[parent]
        return None


class PythonCallAnalyzer:
    """Find conservative cross-file call edges in a Python snapshot."""

    def analyze(self, snapshot: RepositorySnapshot) -> list[CallFact]:
        module_paths = PythonDependencyAnalyzer._module_paths(snapshot.files)
        definitions = self._definitions(snapshot.files)
        facts: set[CallFact] = set()

        for source_file in snapshot.files:
            if source_file.language != "python":
                continue
            try:
                module = ast.parse(source_file.content)
            except SyntaxError:
                continue
            source_module = PythonDependencyAnalyzer._module_name(source_file.path)
            package = (
                source_module
                if source_file.path.endswith("/__init__.py")
                else source_module.rpartition(".")[0]
            )
            symbol_bindings, module_bindings = self._import_bindings(module, package, module_paths)
            for node in ast.walk(module):
                if not isinstance(node, ast.Call):
                    continue
                qualified_name = self._qualified_name(node.func)
                if not qualified_name:
                    continue
                target_symbol = qualified_name[-1]
                target = self._resolve_call(
                    qualified_name,
                    symbol_bindings,
                    module_bindings,
                    module_paths,
                    definitions,
                )
                if target is None or target[0] == source_file.path:
                    continue
                target_path, target_line = target
                call_text = ast.get_source_segment(source_file.content, node) or ""
                source_lines = source_file.content.splitlines()
                source_line = (
                    source_lines[node.lineno - 1].strip()
                    if node.lineno <= len(source_lines)
                    else call_text.strip()
                )
                facts.add(
                    CallFact(
                        snapshot.repository_id,
                        snapshot.commit_sha,
                        source_file.path,
                        node.lineno,
                        target_symbol,
                        target_path,
                        target_line,
                        f"{source_line} calls {target_symbol} in {target_path}:L{target_line}.",
                    )
                )
        return sorted(facts, key=lambda fact: (fact.source_path, fact.line, fact.target_path))

    @staticmethod
    def _definitions(
        files: tuple[SourceFile, ...],
    ) -> dict[str, list[tuple[str, int]]]:
        definitions: dict[str, list[tuple[str, int]]] = {}
        for source_file in files:
            if source_file.language != "python":
                continue
            try:
                module = ast.parse(source_file.content)
            except SyntaxError:
                continue
            for node in ast.walk(module):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions.setdefault(node.name, []).append((source_file.path, node.lineno))
        return definitions

    @staticmethod
    def _import_bindings(
        module: ast.Module,
        package: str,
        module_paths: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        symbol_bindings: dict[str, str] = {}
        module_bindings: dict[str, str] = {}
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target_path = module_paths.get(alias.name)
                    local_name = alias.asname or alias.name.split(".")[0]
                    if target_path:
                        module_bindings[local_name] = target_path
            elif isinstance(node, ast.ImportFrom):
                base_parts = package.split(".") if package and node.level else []
                if node.level:
                    base_parts = base_parts[: max(0, len(base_parts) - node.level + 1)]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base_module = ".".join(part for part in base_parts if part)
                base_path = module_paths.get(base_module)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    full_module = f"{base_module}.{alias.name}" if base_module else alias.name
                    target_path = module_paths.get(full_module) or base_path
                    if target_path:
                        symbol_bindings[alias.asname or alias.name] = target_path
                        if full_module in module_paths:
                            module_bindings[alias.asname or alias.name] = target_path
        return symbol_bindings, module_bindings

    @staticmethod
    def _qualified_name(node: ast.AST) -> tuple[str, ...]:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        else:
            return ()
        return tuple(reversed(parts))

    @staticmethod
    def _resolve_call(
        qualified_name: tuple[str, ...],
        symbol_bindings: dict[str, str],
        module_bindings: dict[str, str],
        module_paths: dict[str, str],
        definitions: dict[str, list[tuple[str, int]]],
    ) -> tuple[str, int] | None:
        target_name = qualified_name[-1]
        if len(qualified_name) == 1 and target_name in symbol_bindings:
            target_path = symbol_bindings[target_name]
            for path, line in definitions.get(target_name, []):
                if path == target_path:
                    return path, line
            return target_path, 1

        dotted_prefixes = [
            ".".join(qualified_name[:position])
            for position in range(len(qualified_name) - 1, 0, -1)
        ]
        for prefix in dotted_prefixes:
            target_path = module_paths.get(prefix)
            if target_path:
                return PythonCallAnalyzer._definition_location(
                    target_name, target_path, definitions
                )
        if qualified_name[0] in module_bindings:
            target_path = module_bindings[qualified_name[0]]
            return PythonCallAnalyzer._definition_location(target_name, target_path, definitions)

        matches = definitions.get(target_name, [])
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _definition_location(
        name: str, path: str, definitions: dict[str, list[tuple[str, int]]]
    ) -> tuple[str, int]:
        for definition_path, line in definitions.get(name, []):
            if definition_path == path:
                return path, line
        return path, 1


class SQLiteDependencyIndex:
    """Persist and query import edges by repository snapshot."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS dependency_edges (
                    repository_id TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    target_module TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY (repository_id, commit_sha, source_path, line, target_path)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS dependency_by_target "
                "ON dependency_edges(repository_id, commit_sha, target_path)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS call_edges (
                    repository_id TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    target_symbol TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    target_line INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY (repository_id, commit_sha, source_path, line, target_path, target_symbol)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS call_by_target "
                "ON call_edges(repository_id, commit_sha, target_path, target_symbol)"
            )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def index_snapshot(self, snapshot: RepositorySnapshot) -> int:
        facts = PythonDependencyAnalyzer().analyze(snapshot)
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM dependency_edges WHERE repository_id = ? AND commit_sha = ?",
                (snapshot.repository_id, snapshot.commit_sha),
            )
            connection.executemany(
                """INSERT INTO dependency_edges (
                    repository_id, commit_sha, source_path, line, target_module, target_path, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        fact.repository_id,
                        fact.commit_sha,
                        fact.source_path,
                        fact.line,
                        fact.target_module,
                        fact.target_path,
                        fact.content,
                    )
                    for fact in facts
                ],
            )
        return len(facts)

    def index_calls(self, snapshot: RepositorySnapshot) -> int:
        facts = PythonCallAnalyzer().analyze(snapshot)
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM call_edges WHERE repository_id = ? AND commit_sha = ?",
                (snapshot.repository_id, snapshot.commit_sha),
            )
            connection.executemany(
                """INSERT INTO call_edges (
                    repository_id, commit_sha, source_path, line, target_symbol,
                    target_path, target_line, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        fact.repository_id,
                        fact.commit_sha,
                        fact.source_path,
                        fact.line,
                        fact.target_symbol,
                        fact.target_path,
                        fact.target_line,
                        fact.content,
                    )
                    for fact in facts
                ],
            )
        return len(facts)

    def search(
        self, repository_id: str, commit_sha: str, query: str, *, top_k: int = 8
    ) -> list[SearchResult]:
        if top_k < 1:
            return []
        terms = {
            token
            for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
            if token not in {"what", "which", "explain", "show", "the", "this", "that", "from"}
        }
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT source_path, line, target_module, target_path, content
                FROM dependency_edges WHERE repository_id = ? AND commit_sha = ?""",
                (repository_id, commit_sha),
            ).fetchall()
        incoming = Counter(row["target_path"] for row in rows)

        def rank(row: sqlite3.Row) -> tuple[int, int, str, int]:
            searchable = " ".join(
                str(row[key]).lower()
                for key in ("source_path", "target_module", "target_path", "content")
            )
            matches = sum(term in searchable for term in terms)
            return (-matches, -incoming[row["target_path"]], row["source_path"], row["line"])

        selected = sorted(rows, key=rank)
        if terms and any(
            any(term in " ".join(str(value).lower() for value in row) for term in terms)
            for row in rows
        ):
            selected = [row for row in selected if rank(row)[0] < 0]
        return [
            SearchResult(
                chunk_id=f"dependency-{index}-{row['source_path']}-{row['line']}",
                repository_id=repository_id,
                commit_sha=commit_sha,
                path=row["source_path"],
                language="python",
                content=f"Internal dependency: {row['source_path']} imports {row['target_module']} from {row['target_path']}.\n{row['content']}",
                start_line=row["line"],
                end_line=row["line"],
                symbol_name=row["target_module"],
                symbol_kind="dependency",
                score=1.0 / (index + 1),
            )
            for index, row in enumerate(selected[:top_k], start=1)
        ]

    def search_calls(
        self, repository_id: str, commit_sha: str, query: str, *, top_k: int = 8
    ) -> list[SearchResult]:
        if top_k < 1:
            return []
        terms = {
            token
            for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
            if token not in {"what", "which", "explain", "show", "the", "this", "that", "from"}
        }
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT source_path, line, target_symbol, target_path, target_line, content
                FROM call_edges WHERE repository_id = ? AND commit_sha = ?""",
                (repository_id, commit_sha),
            ).fetchall()

        def rank(row: sqlite3.Row) -> tuple[int, str, int]:
            searchable = " ".join(str(value).lower() for value in row)
            return (-sum(term in searchable for term in terms), row["source_path"], row["line"])

        selected = sorted(rows, key=rank)
        if terms and any(rank(row)[0] < 0 for row in rows):
            selected = [row for row in selected if rank(row)[0] < 0]
        return [
            SearchResult(
                chunk_id=f"call-{index}-{row['source_path']}-{row['line']}",
                repository_id=repository_id,
                commit_sha=commit_sha,
                path=row["source_path"],
                language="python",
                content=f"Cross-file call: {row['content']}",
                start_line=row["line"],
                end_line=row["line"],
                symbol_name=row["target_symbol"],
                symbol_kind="call_site",
                score=1.0 / index,
            )
            for index, row in enumerate(selected[:top_k], start=1)
        ]
