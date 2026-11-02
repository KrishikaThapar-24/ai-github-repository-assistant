import io
import tarfile

import httpx
import pytest

from dev_assistant.github.source import GitHubRepositorySource, GitHubSourceError


def make_archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            payload = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_github_source_pins_commit_and_filters_archive_files() -> None:
    archive = make_archive(
        {
            "acme-demo-main/.gitignore": "ignored.py\n",
            "acme-demo-main/src/worker.py": "def run_job():\n    return True\n",
            "acme-demo-main/ignored.py": "secret = True\n",
            "acme-demo-main/node_modules/pkg/index.js": "ignored()\n",
            "acme-demo-main/README.md": "Repository guide\n",
        }
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/acme/demo":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": "deadbeef"})
        if request.url.path.endswith("/tarball/deadbeef"):
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    source = GitHubRepositorySource(
        token="test-read-token",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    snapshot = source.load("acme/demo", "HEAD")

    assert snapshot.commit_sha == "deadbeef"
    assert snapshot.ref == "main"
    assert [(item.path, item.language) for item in snapshot.files] == [
        ("README.md", "markdown"),
        ("src/worker.py", "python"),
    ]
    assert requests[0].headers["Authorization"] == "Bearer test-read-token"


def test_github_source_rejects_invalid_repo_identifiers() -> None:
    source = GitHubRepositorySource()

    with pytest.raises(ValueError, match="owner/repository"):
        source.load("https://github.com/acme/demo")


def test_github_source_rejects_untrusted_archive_redirects() -> None:
    source = GitHubRepositorySource()

    with pytest.raises(GitHubSourceError, match="untrusted host"):
        source._validate_archive_redirect("https://attacker.example/archive.tar.gz")


def test_archive_redirect_does_not_forward_github_token() -> None:
    archive = make_archive({"acme-demo/main.py": "print('safe')"})
    archive_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        archive_requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={"Location": "https://codeload.github.com/acme/demo/tar.gz/deadbeef"},
            )
        return httpx.Response(200, content=archive)

    source = GitHubRepositorySource(
        token="private-token",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with source._client_factory() as client:
        downloaded = source._download_archive(
            client,
            "https://api.github.com/archive",
            {"Authorization": "Bearer private-token"},
        )

    assert downloaded == archive
    assert archive_requests[0].headers["Authorization"] == "Bearer private-token"
    assert "Authorization" not in archive_requests[1].headers


def test_archive_download_stops_when_response_exceeds_byte_limit() -> None:
    source = GitHubRepositorySource(max_archive_bytes=100)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 101))
    )

    with client:
        with pytest.raises(GitHubSourceError, match="download limit"):
            source._download_archive(client, "https://api.github.com/archive", {})


def test_github_source_enforces_archive_source_limits() -> None:
    archive = make_archive({"acme-demo/main.py": "x" * 100})
    source = GitHubRepositorySource(max_file_bytes=20)

    files = source._read_archive(archive)

    assert files == []
