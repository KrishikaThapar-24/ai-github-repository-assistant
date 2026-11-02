"""Repository source interface and safe local-directory adapter."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

import pathspec

from dev_assistant.ingestion.models import RepositorySnapshot, SourceFile

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".md": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".vue": "vue",
    ".svelte": "svelte",
}
SPECIAL_LANGUAGES = {"Dockerfile": "dockerfile", "Makefile": "make", "Justfile": "make"}
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    ".turbo",
    "coverage",
}


class RepositorySource(Protocol):
    """Fetch a repository snapshot through any source (local checkout or GitHub)."""

    def load(self, repository_id: str, ref: str = "HEAD") -> RepositorySnapshot: ...


class LocalDirectorySource:
    """Load text source from a local checkout; never follows symlinks."""

    def __init__(
        self, root: Path, *, max_file_bytes: int = 1_000_000, excluded_dirs: set[str] | None = None
    ) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Repository root must be a directory")
        self.max_file_bytes = max_file_bytes
        self.excluded_dirs = excluded_dirs or DEFAULT_EXCLUDED_DIRS
        ignore_file = self.root / ".gitignore"
        try:
            self._ignore_spec = pathspec.PathSpec.from_lines(
                "gitwildmatch", ignore_file.read_text(encoding="utf-8").splitlines()
            )
        except OSError:
            self._ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", [])

    def load(self, repository_id: str, ref: str = "HEAD") -> RepositorySnapshot:
        files: list[SourceFile] = []
        digest = hashlib.sha256()
        for directory, directories, filenames in os.walk(self.root, followlinks=False):
            current = Path(directory)
            relative_parent = current.relative_to(self.root)
            directories[:] = [
                name
                for name in sorted(directories)
                if name not in self.excluded_dirs
                and not (current / name).is_symlink()
                and not self._ignore_spec.match_file((relative_parent / name).as_posix() + "/")
            ]
            for filename in sorted(filenames):
                path = current / filename
                if path.is_symlink() or not path.is_file():
                    continue
                language = SPECIAL_LANGUAGES.get(path.name) or LANGUAGE_BY_SUFFIX.get(
                    path.suffix.lower()
                )
                if language is None:
                    continue
                relative_path = path.relative_to(self.root).as_posix()
                if self._ignore_spec.match_file(relative_path):
                    continue
                try:
                    if path.stat().st_size > self.max_file_bytes:
                        continue
                    raw = path.read_bytes()
                    if b"\x00" in raw:
                        continue
                    content = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                files.append(SourceFile(relative_path, content, language))
                digest.update(relative_path.encode("utf-8"))
                digest.update(b"\0")
                digest.update(raw)
                digest.update(b"\0")
        return RepositorySnapshot(repository_id, ref, digest.hexdigest(), tuple(files))
