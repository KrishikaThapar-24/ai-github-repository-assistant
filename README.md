# AI GitHub Repository Agent

An AI developer assistant that builds an evidence-backed model of a GitHub repository and helps developers trace code, understand architecture, investigate bugs, review changes, and generate tests.

Streamlit is the initial development UI. The intended in-context product UI is a Chrome extension side panel opened from a GitHub repository page. Both it and any future Next.js dashboard use the same versioned FastAPI API, leaving domain logic and agent workflows in the backend.

## Status

The first GitHub-connected vertical slice is implemented: read-only GitHub snapshot ingestion, hybrid code search through both a direct search endpoint and cited LangGraph Q&A, latest PR/issue/commit lookup, a versioned FastAPI interface, a Streamlit development client, and a Chrome Manifest V3 side panel. The LangGraph workflow also has evidence-grounded modes for code review, bug investigation, and unit-test proposals. Pull-request review fetches and analyzes the PR diff in both clients without posting a review. GitHub issue investigation combines a bounded issue report/comments context with indexed source and cross-file call evidence. Repository indexing runs as an in-process background job. Automated checks use mocked GitHub/LLM adapters and do not require credentials.

The activity adapter supports GitHub REST by default and an optional GitHub MCP backend for latest pull request, issue, and commit lookups. Pull-request diff review currently uses bounded read-only GitHub REST requests. The MCP client starts the official server in read-only mode and applies its own read-tool allowlist. Ingestion persists a Python internal-import graph and conservatively resolved cross-file call sites with exact caller and target source locations. Bug-investigation retrieval follows resolved call edges to retrieve the target implementation as well as the caller. Other-language dependency extraction and broader call/reference tracing remain roadmap work. Review, bug investigation, and test generation are advisory, evidence-grounded workflows; broader static analysis, test-framework-aware validation, and executing generated tests remain future work. API authentication, durable background jobs, and production deployment are not implemented. Keep the development API bound to loopback.

See [the architecture and roadmap](docs/architecture.md).

## Development setup

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv sync --extra dev --extra ai --extra ui
uv run pytest -q
uv run ruff check src tests apps
node --test apps/extension/github_context.test.mjs apps/extension/snapshot_storage.test.mjs
```

Copy `.env.example` to `.env`. For private repositories, set a fine-grained GitHub token with read-only Contents, Pull requests, and Issues permissions. Set the key for the provider selected by `DEV_ASSISTANT_LLM_PROVIDER`. Public repositories can be read without a GitHub token. All credentials stay in the backend environment, not the extension.

REST is the default GitHub activity backend. To use the official GitHub MCP server instead, install Docker, set `DEV_ASSISTANT_GITHUB_ACTIVITY_BACKEND=mcp` in `.env`, and provide the read-only GitHub token. Install the MCP SDK with `uv sync --extra dev --extra ai --extra ui --extra github-mcp`. The first MCP request downloads the configured image (`DEV_ASSISTANT_GITHUB_MCP_IMAGE`); repository, issues, and pull-request toolsets run with server-side read-only mode enabled.

Start the API from the repository root and keep it on loopback:

```powershell
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
```

For the Streamlit development client, open another terminal:

```powershell
uv run streamlit run apps/web/streamlit_app.py
```

To try the Chrome side panel, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the `apps/extension` directory. Copy the ID displayed by Chrome into `DEV_ASSISTANT_CHROME_EXTENSION_ID` in `.env`, then restart the API; CORS permits only that configured extension ID. Open a GitHub repository, click the extension icon, choose **Index repo**, then ask a code question. The panel stores the selected repository/ref/commit locally so reopening it restores the matching snapshot; it does not store GitHub credentials. On a pull-request page, **Review this pull request** fetches a bounded diff and returns an advisory review with line citations. On an issue page, **Investigate this issue** compares the issue report and comments with indexed code and returns code citations. The first HuggingFace model load downloads model files and can take a while. PR, issue, and commit activity questions do not require indexing.

The FastAPI docs are available at `http://127.0.0.1:8000/docs` while the local server is running.

## Security defaults

- GitHub requests are read-only and limited by the configured token's permissions. The local API is unauthenticated and must stay bound to loopback.
- Repository-changing actions remain disabled until a user explicitly reviews and approves a specific action.
- API keys and tokens are supplied through environment configuration or a secret manager; never commit them.
- Repository files, issues, pull requests, and other retrieved text are untrusted input to the model.
