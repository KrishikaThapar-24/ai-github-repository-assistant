# System architecture

## Product goal

Build an AI GitHub Repository Agent that helps a developer understand and work with a complete codebase. It must reason across files, services, languages, and dependencies; find and trace relevant code; explain conclusions with file and line evidence; and support controlled analysis workflows. It is not a chat-over-documents application.

The first development UI can be Streamlit, as originally planned. The intended in-context product UI is a Chrome extension side panel that opens from the toolbar while the developer is on a GitHub repository. A future Next.js web client can use the same FastAPI contract for broader dashboard workflows; none of these clients owns repository or agent logic.

## Feature scope

The AI Developer Assistant includes all of the following product capabilities:

- Connect and select a GitHub repository, then index its source for code understanding.
- Search source code as a core feature using exact/lexical, structural, and semantic search.
- Explain what a file, function, class, method, or configuration component does.
- Answer questions that cross file boundaries by tracing definitions, references, callers, routes, services, database operations, and tests where the source allows it.
- Explain repository architecture and dependencies.
- Answer codebase questions with evidence-grounded detail and file/line citations.
- Read GitHub issues, analyze pull requests, and inspect commits through read-only GitHub integration.
- Perform automated code review and advanced bug investigation with findings tied to source evidence.
- Generate unit-test proposals grounded in the code and its test framework.
- Orchestrate multi-step agent workflows with LangGraph, tool calling, bounded memory/checkpointing, and verification.
- Support controlled GitHub actions as a future feature, disabled by default and requiring explicit approval for each action.

GitHub issue/PR/commit access complements repository code understanding; it does not replace it. A repository question should use source and code-graph tools, an activity question should use GitHub read tools, and a question that needs both should gather and cite evidence from both.

## Design principles

1. **Evidence before conclusions.** Responses cite repository path, line range, and indexed commit. The agent reports uncertainty when evidence is missing or conflicting.
2. **Code structure plus semantic retrieval.** Use parsing and symbol/dependency metadata together with lexical and vector search. Similarity search alone cannot reliably trace routes, callers, database operations, and cross-service flows.
3. **Plan, use tools, verify.** LangGraph coordinates bounded workflows: understand the question, gather evidence, analyze, check that claims are supported, then respond. Nodes call typed application services rather than embedding integration logic in prompts.
4. **Least privilege.** GitHub tools begin read-only, use narrowly scoped repository access, and are allowlisted per workflow. Mutations are a separate future capability and require explicit approval for each proposed action.
5. **Replaceable adapters.** Provider APIs, vector storage, GitHub access, and UI are behind interfaces so individual choices can change without reshaping core workflows.
6. **Scale incrementally.** Ingestion is asynchronous and resumable. Do not load an entire large repository into a model context; build indexes and fetch focused evidence on demand.
7. **Answer repository activity questions from live GitHub data.** Questions about pull requests, issues, and commits use GitHub read tools and return the relevant item and timestamp, rather than relying on the code index or model memory.

## Component view

```text
Developer on a GitHub repository
   │
   ├── Chrome toolbar click ──► Chrome side panel
   │                                │
   │                current repo/ref/path/selection context
   │                                │ HTTPS / JSON
   ├── Streamlit prototype ─────────┤
   └── future Next.js dashboard ────┴──► FastAPI application
                                               │
                                      auth, policy, API schemas
                                               │
                                         LangGraph runtime
                                   ┌───────────┼───────────┐
                                   ▼           ▼           ▼
                              Code tools   GitHub MCP   Analysis flows
                             search/graph   read tools   review/bug/tests
                                   │           │           │
                                   └───────────┼───────────┘
                                               ▼
                                 evidence validation and citations

Ingestion worker ─► GitHub read adapter ─► file filtering and parsing
                                                │
                                  symbols, imports, routes, references
                                                │
                              lexical index + embeddings + code graph
                                      ┌─────────┴─────────┐
                                      ▼                   ▼
                               ChromaDB vectors      SQL metadata/jobs
```

## Modules and responsibilities

- **API (`api`)**: FastAPI application, versioned routers, request/response schemas, authentication dependencies, health and readiness endpoints. The API is the stable boundary shared by all clients.
- **Core (`core`)**: settings, logging, errors, IDs, shared types, and authorization/policy checks.
- **Repository catalog (`repositories`)**: connected repository identity, selected branch/ref, indexed snapshot metadata, and access checks.
- **Ingestion (`ingestion`)**: asynchronous jobs; repository snapshot fetch; ignore and size policies; language detection; parsing; chunking; symbol/reference extraction; embedding and index updates. Jobs are idempotent by repository and commit SHA.
- **Code intelligence (`code_intel`)**: language parser adapters and normalized records for files, symbols, imports, routes, database access, tests, and references. Start with the languages present in target repositories, and make unsupported syntax explicit.
- **Search (`search`)**: lexical search, vector search, metadata filters, ranking, context assembly, and citation construction. Retrieval is scoped by repository and snapshot.
- **Code graph (`code_graph`)**: relationships among services, modules, symbols, routes, callers, database operations, and tests. Use static facts where available and label inferred links separately.
- **GitHub integration (`github`)**: GitHub MCP client, read-only tool registry, authentication boundary, and typed adapters for issues, pull requests (including created/updated/merged timestamps), commits, and repository metadata. Code ingestion remains behind a repository adapter so the source can evolve independently of the MCP server's supported tools.
- **Agents (`agents`)**: LangGraph state, bounded nodes, workflow graphs, checkpointing, tool routing, and evidence verification. Workflows include repository Q&A, architecture tracing, code review, bug investigation, and test generation.
- **Analysis (`analysis`)**: deterministic/static analyzers and structured prompts/results for dependency analysis, security-oriented review assistance, root-cause hypotheses, and test suggestions.
- **LLM (`llm`)**: provider-neutral chat and embedding interfaces with OpenAI, Anthropic, and HuggingFace implementations selected by configuration.
- **Worker (`worker`)**: background job runner, retry/backoff, concurrency limits, cancellation, and ingestion status reporting.
- **Persistence (`db`)**: relational records for users, repository connections, snapshots, jobs, and workflow metadata. ChromaDB stores vectors with repository, commit, path, symbol, and line metadata.
- **Streamlit development client (`web`)**: Pages and an HTTP API client only. It renders repository selection, indexing state, chat/search, evidence, analysis output, and approval previews. It does not directly use GitHub credentials or storage.
- **Browser extension (`extension`)**: Manifest V3 Chrome toolbar action and side panel for the in-context workflow. It recognizes GitHub repository, pull request, issue, commit, and source-file URLs; sends only the active repository/ref/path and user-selected code context to the API. It stores the indexed repository/ref/commit selection locally to restore the matching snapshot when the panel reopens; it does not store credentials. Repository-wide understanding comes from the backend index and GitHub read adapter, not by scraping all files into the browser. The extension contains presentation and context-detection logic only.
- **Web UI (`web`)**: Streamlit is an early development client; a future Next.js dashboard can provide wider repository management. Both call the same API and contain no agent, vector-store, or GitHub credential logic.
- **Observability (`observability`)**: structured logs, metrics, traces, redaction, and audit records for access and agent tool calls.

## Large repository understanding

For repositories with many services, languages, or hundreds of thousands of lines:

1. Pin each index to a commit SHA and retain clear indexing status.
2. Filter generated, binary, oversized, vendored, and ignored files; respect repository ignore rules and configurable exclusions.
3. Parse code into syntax-aware units such as classes, functions, routes, and configuration sections. Preserve path, symbol, and line metadata.
4. Extract structural facts (imports, symbol definitions/references, route registrations, database calls, tests, manifests) into queryable metadata and graph edges.
5. Combine exact/lexical lookup, graph traversal, and vector retrieval. Use each according to the question: identifier lookup, relationship tracing, or concept search.
6. Resolve a question about a named file or symbol directly from structural metadata before broad semantic retrieval. For behavior spanning files, expand from discovered symbols through references, callers, and dependencies, then retrieve focused context for each hop.
7. Let the workflow gather additional evidence in bounded steps. Keep citations connected to exact indexed source lines and commit.
8. Treat inferred call relationships as hypotheses when language/runtime behavior prevents static certainty; verify them against source before presenting them as facts.

## Agent workflow

```text
Question
  → classify intent and repository/snapshot
  → make a bounded evidence plan
  → call symbol/file lookup, code search, graph, and/or read-only GitHub tools
  → inspect focused source context
  → analyze evidence and identify gaps/conflicts
  → verify claims and citations
  → return answer, sources, confidence/limitations, and next steps
```

Each graph run has limits for tool calls, elapsed time, retrieved context, and retries. Retrieved repository text and GitHub content are untrusted data and must never be interpreted as instructions to override system policy or authorize a tool call. Checkpoints support resumable work, not implicit permission to perform external actions.

## Chrome extension experience

The target workflow is: install the extension, open a GitHub repository, click its toolbar icon, connect GitHub if needed, and ask questions in a persistent side panel. The panel shows the detected repository and page context so the developer can see which repository, branch/commit, file, PR, or issue the answer concerns. The developer can ask about the current file or selection, ask questions across the complete indexed repository, search code, inspect PR/issue/commit activity, and launch review, bug-investigation, or test-generation workflows.

Use Chrome's Manifest V3 Side Panel API for the persistent UI. A small content script or tab-context reader may parse the active GitHub URL and, only when the developer asks, capture selected/visible source context. Do not scrape entire repositories from page DOM. The backend gets repository-wide source through its separately authorized GitHub integration. Private repositories require the user to connect and authorize access; being on a GitHub page does not itself grant backend access. GitHub Enterprise hosts can be supported through explicit host configuration/permission.

Keep extension permissions narrow to GitHub pages and the configured backend origin. Validate all messages from content scripts; keep GitHub credentials and backend session handling in the extension's privileged context; never expose secrets to the page DOM or content script. All network calls use HTTPS. The API validates repository access and snapshot scope on every request, regardless of context supplied by the extension.

The extension can start with a small React/Vite UI built as static extension assets. Reuse the FastAPI request/response schemas; Streamlit remains useful as an early backend-development console, and a Next.js web dashboard remains an optional client rather than a requirement for the extension.

### Repository activity questions

The connected-repository experience supports both source questions and live activity questions, for example:

- “When was the last PR opened?”
- “What is the latest merged PR?”
- “Which PRs are still open?”
- “What changed in the latest commit?”
- “What are the newest open issues?”

Route activity questions to the GitHub read adapter. For “last PR” without a qualifier, define the default as **most recently created/opened**; distinguish its `created_at`, `updated_at`, and `merged_at` timestamps when available. “Latest merged PR” instead orders by merge time. Include the PR number, title, author, state, timestamp (with timezone), and a GitHub link. If GitHub returns a paginated list, continue or use server-side filtering until the answer is certain; do not call the first returned item “latest” without verifying ordering and coverage. If there are no PRs or access is unavailable, say so clearly.

Activity queries do not require code ingestion. A user should be able to connect a repository and ask about its PRs, issues, and commits while indexing is pending; codebase questions can explain that indexing is still in progress.

## GitHub authorization and actions

- Begin with read access only, limited to explicitly connected repositories and the required repository permissions.
- Keep read and write tool registries separate. Do not expose write tools to the initial agent graph.
- For a later mutation feature, show the exact target and proposed diff/action, obtain explicit user approval immediately before execution, re-check authorization, execute one scoped action, and record an audit event.
- Keep tokens out of prompts, logs, browser state, and source files. Use short-lived credentials where the chosen GitHub authentication mechanism supports them.

## Proposed project structure

```text
ai-github-repository-agent/
├── apps/
│   ├── api/                 # FastAPI entry point
│   ├── worker/              # Background job entry point
│   ├── web/                 # Streamlit development client; optional Next.js dashboard later
│   └── extension/           # Chrome Manifest V3 side panel client
├── src/dev_assistant/
│   ├── api/                 # Routers and transport schemas
│   ├── core/                # Settings, errors, policy, shared types
│   ├── repositories/        # Repository and snapshot catalog
│   ├── ingestion/           # Fetch, filter, parse, chunk, index jobs
│   ├── code_intel/          # Language adapters and structural facts
│   ├── search/              # Lexical/vector retrieval and citations
│   ├── code_graph/          # Dependency and reference relationships
│   ├── github/              # GitHub MCP/read-only integration
│   ├── agents/              # LangGraph state, nodes, and workflows
│   ├── analysis/            # Review, bug, architecture, test workflows
│   ├── llm/                 # Model and embedding adapters
│   ├── db/                  # SQL persistence and migrations
│   ├── worker/              # Job orchestration
│   └── observability/       # Logs, metrics, traces, audit
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── deploy/
│   └── docker/
├── docs/
│   └── architecture.md
├── .env.example             # Placeholder names only; no secrets
├── .gitignore
├── pyproject.toml
└── README.md
```

The API schemas are the client boundary: Streamlit, the Chrome extension, and a future Next.js dashboard can all use the same backend while agent workflows, ingestion, and persistence remain unchanged.

## Implementation roadmap

Each phase is a separate module milestone. Implement and verify the phase before moving to the next.

### Current implementation state

- **Foundation:** packaging, validated settings, FastAPI health check, and development test/lint setup are present. Docker and production authentication are not yet configured.
- **GitHub read access (step 2, initial slice):** a bounded, read-only REST archive adapter resolves and pins a ref to a commit SHA; activity questions about latest pull requests, issues, and commits use REST by default or an optional Docker-launched GitHub MCP server. MCP is configured with read-only mode, narrow toolsets, and a client-side read-tool allowlist.
- **Ingestion (step 3, initial slice):** local and GitHub snapshots, `.gitignore` and generated-file filtering, stable content-based snapshot IDs, syntax-aware Python symbols, declaration-aware fallback chunks for other common languages, bounded source chunks, and in-process background indexing jobs are implemented. Python imports, relative JavaScript/TypeScript module imports, and conservative cross-file Python call edges are persisted with source locations. Durable queues and richer cross-language reference extraction remain pending.
- **Search (step 4):** persistent Chroma vectors plus SQLite FTS keyword search, repository/snapshot/path filters, identifier ranking, and source citations are implemented. A versioned direct search endpoint is available to both Streamlit and the extension. The HuggingFace adapter is available through optional dependencies; tests use deterministic embeddings and do not download a model.
- **Code Q&A (step 5):** a LangGraph plan/retrieve/answer/citation-verification workflow is implemented. OpenAI and Anthropic adapters require the optional AI dependencies and a locally configured provider key. Tests use a fake answerer. The default checkpointer is process-local memory; durable checkpoints belong with the persistence hardening phase.
- **Client/API vertical slice:** versioned FastAPI routes, repository job polling, direct code search, code Q&A, a Streamlit client, and a Chrome side panel are connected. Architecture questions use bounded repository searches and structural import evidence; bug-investigation questions retrieve Python call edges and expand them into the callee implementation where indexed. GitHub issue investigation fetches a bounded issue report and comment set as escaped untrusted context, then uses issue terms for retrieval and keeps citations attached to source code. Evidence-grounded code review, bug investigation, and unit-test proposal prompts are available through the LangGraph workflow. The dedicated PR review route fetches a bounded unified diff with read-only GitHub REST requests, maps changed lines to head/base commits, and is available in Streamlit and the extension. JavaScript/TypeScript path aliases, other-language edges, broader static analysis, test-framework detection/validation, deeper architecture tracing, and approval-gated GitHub mutations remain future milestones. The API has no user authentication or repository catalog yet; bind it to loopback only.

1. **Foundation and scaffold** — package layout, configuration validation, logging, shared types, API/worker entry points, Docker development setup. Verify clean startup and configuration error behavior.
2. **Repository access and GitHub read adapter** — repository selection, authentication boundary, MCP client, read-only tool registry, and live PR/issue/commit queries. Verify repository scoping, created-versus-merged semantics, pagination/ordering, and absence of mutation capability.
3. **Ingestion and code intelligence** — filters, language detection, parsers, syntax-aware chunks, symbols, references, structural metadata, commit-versioned jobs. Verify file/function/class lookup, cross-file relationships, exclusions, line mapping, and idempotent re-indexing.
4. **Persistence and search** — SQL metadata, Chroma adapter, lexical + vector retrieval, filters, ranking, citations. Verify repository/snapshot isolation and source locations.
5. **LangGraph code Q&A agent** — typed state and tools, plan/retrieve/analyze/verify/respond flow, bounded execution and checkpoints. Verify explanations of files/functions/classes and cross-file behavior with citations, plus safe handling of malicious repository text.
6. **Architecture and dependency workflows** — Python imports, relative JavaScript/TypeScript imports, and resolved Python cross-file call edges are implemented and used as evidence for architecture and bug questions. Add Java/Go/Rust and path-alias support, service maps, routes, database access, tests, and graph traversal. Verify graph edges against known repository examples and label inferred relationships.
7. **Bug investigation and review** — initial evidence-grounded prompts and read-only PR diff review are implemented. Expand static checks, PR context, severity calibration, and regression evaluation. Verify findings are grounded and uncertainty is surfaced.
8. **Unit-test generation** — initial cited test proposals are implemented. Add test-framework detection and optional sandboxed validation. Verify generated tests against representative fixtures and never commit them automatically.
9. **Streamlit development UI (initial slice)** — repository connection, indexing status, direct code search, Q&A, citations, and pull-request review are implemented. Richer analysis views remain pending.
10. **Chrome extension side panel (initial slice)** — Manifest V3 toolbar action, current repository/file/PR context, indexing, direct code search, Q&A, pull-request review, citation deep links, and local indexed-snapshot restoration per repository/ref are implemented. Selected-text context, Enterprise hosts, and browser-level automated checks remain pending.
11. **Hardening and deployment** — authorization review, resource limits, retries, migrations, observability, Docker deployment, data retention/deletion behavior. Verify production configuration and operational recovery.
12. **Next.js dashboard (optional)** — build a broader web client against the established API contract if repository management needs exceed the extension side panel; preserve the same backend.
13. **Approval-gated GitHub actions (future)** — introduce individual write tools only after the read-only product is stable, with a reviewable proposed action and explicit per-action approval. Verify an unapproved action can never execute.

## Initial verification strategy

For each module, add focused unit tests and representative fixtures, then integration tests at its external boundaries. Avoid tests that require real GitHub or LLM credentials by default; use fakes or recorded contract fixtures. Add opt-in end-to-end checks for configured development environments. No module should proceed until its acceptance checks pass.
