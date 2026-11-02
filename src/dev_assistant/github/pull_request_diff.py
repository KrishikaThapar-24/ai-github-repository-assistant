"""Turn a unified GitHub pull request diff into bounded, line-cited evidence."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from dev_assistant.github.activity import PullRequestReviewContext
from dev_assistant.ingestion.source import LANGUAGE_BY_SUFFIX
from dev_assistant.search.models import SearchResult

_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def pull_request_diff_evidence(
    repository_id: str,
    pull_request: PullRequestReviewContext,
    *,
    max_hunks: int = 12,
    max_hunk_characters: int = 4_000,
) -> tuple[list[SearchResult], bool]:
    """Extract changed hunks with head/base line citations; report evidence truncation."""

    if max_hunks < 1 or max_hunk_characters < 1:
        raise ValueError("Diff evidence limits must be positive")
    results: list[SearchResult] = []
    current_path: str | None = None
    old_path: str | None = None
    old_line = 0
    new_line = 0
    hunk_lines: list[str] = []
    added_lines: list[int] = []
    removed_lines: list[int] = []
    hunk_number = 0
    truncated = False

    def finish_hunk() -> None:
        nonlocal truncated, hunk_lines, added_lines, removed_lines
        if not hunk_lines or not (added_lines or removed_lines):
            hunk_lines = []
            added_lines = []
            removed_lines = []
            return
        path = current_path or old_path
        if path is None or not _is_safe_repository_path(path):
            hunk_lines = []
            added_lines = []
            removed_lines = []
            return
        if len(results) >= max_hunks:
            truncated = True
            hunk_lines = []
            added_lines = []
            removed_lines = []
            return
        # Added lines point to the PR head; deletion-only findings point to the base.
        line_numbers = added_lines or removed_lines
        commit_sha = pull_request.head_sha if added_lines else pull_request.base_sha
        content = "\n".join(hunk_lines)
        if len(content) > max_hunk_characters:
            content = content[:max_hunk_characters] + "\n[Diff hunk excerpt truncated]"
            truncated = True
        digest = hashlib.sha256(
            f"{repository_id}:{pull_request.number}:{path}:{hunk_number}".encode()
        ).hexdigest()[:20]
        results.append(
            SearchResult(
                chunk_id=f"pr-diff-{digest}",
                repository_id=repository_id,
                commit_sha=commit_sha,
                path=path,
                language=LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower(), "text"),
                content=(
                    f"Pull request #{pull_request.number} changed lines in {path}:\n{content}"
                ),
                start_line=min(line_numbers),
                end_line=max(line_numbers),
                symbol_name=f"PR #{pull_request.number} hunk {hunk_number}",
                symbol_kind="pull_request_diff",
                score=1.0,
            )
        )
        hunk_lines = []
        added_lines = []
        removed_lines = []

    for line in pull_request.diff.splitlines():
        if line.startswith("diff --git "):
            finish_hunk()
            current_path = None
            old_path = None
            continue
        if line.startswith("--- a/"):
            old_path = line[6:]
            continue
        file_match = _FILE_HEADER.match(line)
        if file_match:
            current_path = file_match.group(1)
            continue
        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match:
            finish_hunk()
            hunk_number += 1
            old_line, new_line = int(hunk_match.group(1)), int(hunk_match.group(2))
            hunk_lines = [line]
            added_lines = []
            removed_lines = []
            continue
        if not hunk_lines:
            continue
        hunk_lines.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(old_line)
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1

    finish_hunk()
    if not results and pull_request.diff.strip():
        # Binary-only diffs and unsupported formats cannot be reviewed as source evidence.
        return [], False
    return results, truncated


def _is_safe_repository_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        bool(path)
        and not parsed.is_absolute()
        and all(part not in {".", ".."} for part in parsed.parts)
    )
