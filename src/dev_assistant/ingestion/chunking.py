"""Syntax-aware code chunking with bounded line-based fallback."""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterator

from dev_assistant.ingestion.models import CodeChunk, RepositorySnapshot, SourceFile

SYMBOL_NODE_KINDS = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "implementation",
    },
    "ruby": {
        "method": "method",
        "singleton_method": "method",
        "class": "class",
        "module": "module",
    },
    "c_sharp": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "php": {
        "function_definition": "function",
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
    },
    "c": {"function_definition": "function", "struct_specifier": "struct"},
    "swift": {
        "function_declaration": "function",
        "class_declaration": "class",
        "protocol_declaration": "protocol",
    },
    "kotlin": {
        "function_declaration": "function",
        "class_declaration": "class",
        "object_declaration": "object",
    },
    "scala": {
        "function_definition": "function",
        "class_definition": "class",
        "object_definition": "object",
        "trait_definition": "trait",
    },
}
DECLARATION_PATTERNS = {
    "javascript": r"\b(?:function\s*\*?|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=.*=>|\b(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*\{",
    "typescript": r"\b(?:function\s*\*?|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=.*=>|\b(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?::\s*[^{}]+)?\{",
    "tsx": r"\b(?:function\s*\*?|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=.*=>|\b(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?::\s*[^{}]+)?\{",
    "java": r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)|\b([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{",
    "go": r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)|\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)",
    "rust": r"\b(fn|struct|enum|trait|impl|mod)\s+([A-Za-z_]\w*)",
    "ruby": r"\b(def|class|module)\s+([A-Za-z_][\w!?=]*)",
    "php": r"\b(function|class|interface|trait)\s+([A-Za-z_]\w*)",
    "c_sharp": r"\b(class|interface|struct|enum|record)\s+([A-Za-z_]\w*)",
    "cpp": r"\b(class|struct|enum)\s+([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{",
    "c": r"\bstruct\s+([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{",
    "swift": r"\b(func|class|struct|enum|protocol)\s+([A-Za-z_]\w*)",
    "kotlin": r"\b(fun|class|object|interface)\s+([A-Za-z_]\w*)",
    "scala": r"\b(def|class|object|trait)\s+([A-Za-z_]\w*)",
}


class CodeChunker:
    def __init__(
        self, *, max_chars: int = 4_000, lines_per_chunk: int = 80, overlap_lines: int = 12
    ) -> None:
        self.max_chars = max_chars
        self.lines_per_chunk = lines_per_chunk
        self.overlap_lines = overlap_lines
        self._unavailable_parsers: set[str] = set()

    def chunk_snapshot(self, snapshot: RepositorySnapshot) -> list[CodeChunk]:
        return [
            chunk
            for source_file in snapshot.files
            for chunk in self.chunk_file(snapshot, source_file)
        ]

    def chunk_file(self, snapshot: RepositorySnapshot, source_file: SourceFile) -> list[CodeChunk]:
        lines = source_file.content.splitlines()
        if not lines:
            return []
        symbols = list(self._symbols(source_file))
        result: list[CodeChunk] = []
        if symbols:
            overview_end = min(24, len(lines))
            result.append(
                self._make(
                    snapshot,
                    source_file,
                    "file",
                    "",
                    1,
                    overview_end,
                    "\n".join(lines[:overview_end]),
                )
            )
            for kind, name, start, end, text in symbols:
                result.extend(
                    self._bounded_symbol_chunks(snapshot, source_file, kind, name, start, end, text)
                )
        stride = max(1, self.lines_per_chunk - self.overlap_lines)
        for start in range(0, len(lines), stride):
            end = min(len(lines), start + self.lines_per_chunk)
            result.append(
                self._make(
                    snapshot, source_file, "lines", "", start + 1, end, "\n".join(lines[start:end])
                )
            )
            if end == len(lines):
                break
        return result

    def _symbols(self, source_file: SourceFile) -> Iterator[tuple[str, str, int, int, str]]:
        kinds = SYMBOL_NODE_KINDS.get(source_file.language, {})
        if not kinds:
            yield from self._regex_symbols(source_file)
            return
        if source_file.language == "python":
            yield from self._python_symbols(source_file)
            return
        if source_file.language in self._unavailable_parsers:
            yield from self._regex_symbols(source_file)
            return
        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(source_file.language)
        except Exception:
            self._unavailable_parsers.add(source_file.language)
            yield from self._regex_symbols(source_file)
            return
        source = source_file.content.encode("utf-8")
        try:
            tree = parser.parse(source)
        except Exception:
            yield from self._regex_symbols(source_file)
            return
        if tree is None:
            yield from self._regex_symbols(source_file)
            return
        stack = [tree.root_node]
        found_symbol = False
        while stack:
            node = stack.pop()
            kind = kinds.get(node.type)
            if kind:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    found_symbol = True
                    name = source[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
                    yield kind, name, node.start_point.row + 1, node.end_point.row + 1, text
            stack.extend(reversed(node.children))
        if not found_symbol:
            yield from self._regex_symbols(source_file)

    @staticmethod
    def _python_symbols(source_file: SourceFile) -> Iterator[tuple[str, str, int, int, str]]:
        try:
            module = ast.parse(source_file.content)
        except SyntaxError:
            return
        lines = source_file.content.splitlines()
        parents = {
            child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)
        }
        nodes = [
            node
            for node in parents
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        for node in sorted(
            nodes, key=lambda item: (item.lineno, -getattr(item, "end_lineno", item.lineno))
        ):
            end = getattr(node, "end_lineno", node.lineno)
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif isinstance(parents[node], ast.ClassDef):
                kind = "method"
            else:
                kind = "function"
            yield kind, node.name, node.lineno, end, "\n".join(lines[node.lineno - 1 : end])

    @staticmethod
    def _regex_symbols(source_file: SourceFile) -> Iterator[tuple[str, str, int, int, str]]:
        pattern = DECLARATION_PATTERNS.get(source_file.language)
        if pattern is None:
            return
        lines = source_file.content.splitlines()
        compiled = re.compile(pattern)
        matches: list[tuple[str, int]] = []
        for index, line in enumerate(lines):
            match = compiled.search(line)
            if not match:
                continue
            name = next(
                (group for group in reversed(match.groups()) if group and group[:1].isidentifier()),
                "",
            )
            if not name or name in {"if", "for", "while", "switch", "catch", "return", "new"}:
                continue
            prefix = line[: match.start()]
            kind = (
                "class"
                if re.search(
                    r"\b(class|interface|struct|enum|record|trait|protocol|object|module)\b", line
                )
                else "function"
            )
            matches.append((f"{kind}\0{name}", index, len(prefix) - len(prefix.lstrip())))
        for position, (encoded, start, indent) in enumerate(matches):
            kind, name = encoded.split("\0", 1)
            end = min(len(lines), start + 80)
            for following in range(position + 1, len(matches)):
                _, next_start, next_indent = matches[following]
                if next_indent <= indent:
                    end = next_start
                    break
            text_lines = lines[start:end]
            while len(text_lines) > 1 and not text_lines[-1].strip():
                text_lines.pop()
            yield kind, name, start + 1, start + len(text_lines), "\n".join(text_lines)

    def _bounded_symbol_chunks(
        self,
        snapshot: RepositorySnapshot,
        source_file: SourceFile,
        kind: str,
        name: str,
        start: int,
        end: int,
        text: str,
    ) -> list[CodeChunk]:
        if len(text) <= self.max_chars:
            return [self._make(snapshot, source_file, kind, name, start, end, text)]
        lines = text.splitlines()
        chunks = []
        for offset in range(0, len(lines), self.lines_per_chunk):
            part = lines[offset : offset + self.lines_per_chunk]
            part_start = start + offset
            part_end = min(end, part_start + len(part) - 1)
            chunks.append(
                self._make(snapshot, source_file, kind, name, part_start, part_end, "\n".join(part))
            )
        return chunks

    @staticmethod
    def _make(
        snapshot: RepositorySnapshot,
        source_file: SourceFile,
        kind: str,
        name: str,
        start: int,
        end: int,
        content: str,
    ) -> CodeChunk:
        stable = "\0".join(
            (
                snapshot.repository_id,
                snapshot.commit_sha,
                source_file.path,
                str(start),
                str(end),
                kind,
                name,
                content,
            )
        )
        chunk_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()
        return CodeChunk(
            chunk_id,
            snapshot.repository_id,
            snapshot.commit_sha,
            source_file.path,
            source_file.language,
            content,
            start,
            end,
            name,
            kind,
        )
