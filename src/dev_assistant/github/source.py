"""Read-only GitHub repository snapshot adapter using archive downloads."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin, urlparse

import httpx
import pathspec

from dev_assistant.ingestion.models import RepositorySnapshot, SourceFile
from dev_assistant.ingestion.source import (
    DEFAULT_EXCLUDED_DIRS,
    LANGUAGE_BY_SUFFIX,
    SPECIAL_LANGUAGES,
)


class GitHubSourceError(RuntimeError):
    """A user-facing failure while reading a GitHub repository."""


class GitHubRepositorySource:
    """Fetch a pinned repository archive with read-only GitHub REST access."""

    def __init__(
        self,
        *,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        timeout_seconds: int = 30,
        max_archive_bytes: int = 64 * 1024 * 1024,
        max_file_bytes: int = 1_000_000,
        max_file_count: int = 10_000,
        max_total_source_bytes: int = 128 * 1024 * 1024,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_archive_bytes = max_archive_bytes
        self.max_file_bytes = max_file_bytes
        self.max_file_count = max_file_count
        self.max_total_source_bytes = max_total_source_bytes
        self._client_factory = client_factory or self._make_client

    def _make_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_seconds, follow_redirects=False)

    def load(self, repository_id: str, ref: str = "HEAD") -> RepositorySnapshot:
        owner, repository = self._parse_repository_id(repository_id)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        with self._client_factory() as client:
            repository_response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}", headers=headers
            )
            self._raise_for_github(repository_response)
            repository_data = repository_response.json()
            resolved_ref = repository_data.get("default_branch") if ref == "HEAD" else ref
            if not resolved_ref:
                raise GitHubSourceError(
                    "GitHub did not return a default branch for this repository."
                )

            commit_response = client.get(
                f"{self.api_url}/repos/{owner}/{repository}/commits/{quote(resolved_ref, safe='')}",
                headers=headers,
            )
            self._raise_for_github(commit_response)
            commit_sha = commit_response.json().get("sha")
            if not isinstance(commit_sha, str) or not commit_sha:
                raise GitHubSourceError("GitHub did not return a commit SHA for the requested ref.")

            archive_url = (
                f"{self.api_url}/repos/{owner}/{repository}/tarball/{quote(commit_sha, safe='')}"
            )
            archive = self._download_archive(client, archive_url, headers)

        files = self._read_archive(archive)
        return RepositorySnapshot(repository_id, resolved_ref, commit_sha, tuple(files))

    @staticmethod
    def _parse_repository_id(repository_id: str) -> tuple[str, str]:
        pieces = repository_id.split("/")
        if len(pieces) != 2 or any(
            not piece
            or piece in {".", ".."}
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                for char in piece
            )
            for piece in pieces
        ):
            raise ValueError("Repository must be given as owner/repository")
        return pieces[0], pieces[1]

    def _download_archive(
        self, client: httpx.Client, archive_url: str, headers: dict[str, str]
    ) -> bytes:
        with client.stream("GET", archive_url, headers=headers, follow_redirects=False) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                return self._read_bounded_archive(response)
            location = response.headers.get("location")
            if not location:
                raise GitHubSourceError("GitHub returned an archive redirect without a location.")
            download_url = urljoin(archive_url, location)
            self._validate_archive_redirect(download_url)

        # The signed archive URL can use a different host; never forward the API token to it.
        with client.stream("GET", download_url, follow_redirects=False) as response:
            return self._read_bounded_archive(response)

    def _read_bounded_archive(self, response: httpx.Response) -> bytes:
        self._raise_for_github(response)
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > self.max_archive_bytes:
            raise GitHubSourceError("Repository archive exceeds the configured download limit.")
        buffer = io.BytesIO()
        for chunk in response.iter_bytes():
            if buffer.tell() + len(chunk) > self.max_archive_bytes:
                raise GitHubSourceError("Repository archive exceeds the configured download limit.")
            buffer.write(chunk)
        return buffer.getvalue()

    def _validate_archive_redirect(self, download_url: str) -> None:
        parsed = urlparse(download_url)
        api_host = urlparse(self.api_url).hostname
        allowed_hosts = {api_host, "codeload.github.com"}
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise GitHubSourceError("GitHub returned an archive redirect to an untrusted host.")

    @staticmethod
    def _raise_for_github(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code == 401 or response.status_code == 403:
            raise GitHubSourceError(
                "GitHub denied access. Check repository visibility and the token's read-only contents permission."
            )
        if response.status_code == 404:
            raise GitHubSourceError(
                "Repository or ref was not found, or the configured token cannot access it."
            )
        raise GitHubSourceError(
            f"GitHub returned HTTP {response.status_code} while reading the repository."
        )

    def _read_archive(self, archive_bytes: bytes) -> list[SourceFile]:
        files: list[SourceFile] = []
        total_source_bytes = 0
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            root_ignore = next(
                (
                    member
                    for member in members
                    if member.isfile()
                    and PurePosixPath(member.name).name == ".gitignore"
                    and len(PurePosixPath(member.name).parts) == 2
                ),
                None,
            )
            ignore_patterns: list[str] = []
            if root_ignore is not None:
                ignore_file = archive.extractfile(root_ignore)
                if ignore_file is not None:
                    try:
                        ignore_patterns = ignore_file.read(256_000).decode("utf-8").splitlines()
                    except UnicodeDecodeError:
                        ignore_patterns = []
            ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)

            for member in members:
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2 or not member.isfile():
                    continue
                relative_parts = parts[1:]
                if any(part in {".", ".."} for part in relative_parts):
                    continue
                if any(part in DEFAULT_EXCLUDED_DIRS for part in relative_parts[:-1]):
                    continue
                relative_path = PurePosixPath(*relative_parts).as_posix()
                if ignore_spec.match_file(relative_path):
                    continue
                language = SPECIAL_LANGUAGES.get(
                    PurePosixPath(relative_path).name
                ) or LANGUAGE_BY_SUFFIX.get(PurePosixPath(relative_path).suffix.lower())
                if language is None:
                    continue
                if member.size > self.max_file_bytes:
                    continue
                if len(files) >= self.max_file_count:
                    raise GitHubSourceError(
                        "Repository has more indexable files than the configured limit."
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(self.max_file_bytes + 1)
                if len(raw) > self.max_file_bytes or b"\x00" in raw:
                    continue
                total_source_bytes += len(raw)
                if total_source_bytes > self.max_total_source_bytes:
                    raise GitHubSourceError(
                        "Repository source exceeds the configured indexing limit."
                    )
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                files.append(SourceFile(relative_path, content, language))
        files.sort(key=lambda source_file: source_file.path)
        return files
