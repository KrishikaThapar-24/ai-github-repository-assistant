import { parseGitHubRepository } from "./github_context.js";
import { loadIndexedSnapshot, saveIndexedSnapshot, snapshotStorageKey } from "./snapshot_storage.js";

const API_BASE = "http://127.0.0.1:8000/api/v1";
const contextLabel = document.querySelector("#repository-context");
const refInput = document.querySelector("#ref-input");
const statusLabel = document.querySelector("#index-status");
const backendStatusLabel = document.querySelector("#backend-status");
const connectButton = document.querySelector("#connect-button");
const askButton = document.querySelector("#ask-button");
const reviewPullRequestButton = document.querySelector("#review-pr-button");
const investigateIssueButton = document.querySelector("#investigate-issue-button");
const searchButton = document.querySelector("#search-button");
const searchInput = document.querySelector("#search-input");
const questionInput = document.querySelector("#question-input");
const messages = document.querySelector("#messages");

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    questionInput.value = button.dataset.prompt;
    questionInput.focus();
  });
});

let pageContext = null;
let indexedCommit = null;
let indexedSnapshotKey = null;
let snapshotLookupVersion = 0;

function requestedRef() {
  return refInput.value.trim() || "HEAD";
}

function activeSnapshotKey() {
  if (!pageContext) return null;
  return snapshotStorageKey(pageContext.repositoryId, requestedRef());
}

function currentIndexedCommit() {
  return indexedSnapshotKey === activeSnapshotKey() ? indexedCommit : null;
}

async function restoreIndexedSnapshot() {
  if (!pageContext) return;
  const repositoryId = pageContext.repositoryId;
  const ref = requestedRef();
  const storageKey = snapshotStorageKey(repositoryId, ref);
  const lookupVersion = ++snapshotLookupVersion;
  indexedCommit = null;
  indexedSnapshotKey = null;
  try {
    const snapshot = await loadIndexedSnapshot(chrome.storage.local, repositoryId, ref);
    if (
      lookupVersion !== snapshotLookupVersion
      || pageContext?.repositoryId !== repositoryId
      || requestedRef() !== ref
    ) {
      return;
    }
    indexedCommit = snapshot?.commitSha || null;
    indexedSnapshotKey = indexedCommit ? storageKey : null;
    if (snapshot) {
      statusLabel.textContent = `Using indexed snapshot ${snapshot.commitSha.slice(0, 8)} for ${repositoryId}@${ref}.`;
    }
  } catch (error) {
    if (lookupVersion === snapshotLookupVersion) {
      statusLabel.textContent = `Could not restore indexed snapshot: ${error.message}`;
    }
  }
}

async function readActivePage() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  let activeUrl = tab?.url || "";
  if (!activeUrl && tab?.id != null) {
    const { activeRepositoryTab } = await chrome.storage.session.get("activeRepositoryTab");
    if (activeRepositoryTab?.tabId === tab.id) {
      activeUrl = activeRepositoryTab.url;
    }
  }
  const nextContext = activeUrl ? parseGitHubRepository(activeUrl) : null;
  if (
    nextContext?.repositoryId !== pageContext?.repositoryId
    || nextContext?.ref !== pageContext?.ref
  ) {
    indexedCommit = null;
    indexedSnapshotKey = null;
  }
  pageContext = nextContext;
  if (!pageContext) {
    snapshotLookupVersion += 1;
    indexedCommit = null;
    indexedSnapshotKey = null;
    contextLabel.textContent = "Navigate to a GitHub repository to use the assistant.";
    connectButton.disabled = true;
    askButton.disabled = true;
    reviewPullRequestButton.hidden = true;
    investigateIssueButton.hidden = true;
    return;
  }

  contextLabel.textContent = pageContext.pullRequestNumber
    ? `${pageContext.repositoryId} · PR #${pageContext.pullRequestNumber}`
    : pageContext.issueNumber
      ? `${pageContext.repositoryId} · Issue #${pageContext.issueNumber}`
      : pageContext.path
      ? `${pageContext.repositoryId} · ${pageContext.path}`
      : pageContext.repositoryId;
  refInput.value = pageContext.ref === "HEAD" ? "" : pageContext.ref;
  connectButton.disabled = false;
  askButton.disabled = false;
  reviewPullRequestButton.hidden = !pageContext.pullRequestNumber;
  investigateIssueButton.hidden = !pageContext.issueNumber;
  await restoreIndexedSnapshot();
}

chrome.tabs.onActivated.addListener(() => readActivePage().catch(() => {}));
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url || changeInfo.status === "complete") {
    chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
      if (tab?.id === tabId) readActivePage().catch(() => {});
    });
  }
});

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `API request failed (${response.status}).`);
  return payload;
}

function explainError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (/^Set DEV_ASSISTANT_(OPENAI|ANTHROPIC)_API_KEY\b/.test(message)) {
    return `${message} Add the key to your ignored .env file and restart the API. `
      + "Repository indexing, code search, and GitHub activity still work without it.";
  }
  return message;
}

async function refreshBackendStatus() {
  try {
    const status = await request("/status");
    const modelStatus = status.llm_configured
      ? `AI answers are enabled with ${status.llm_provider}.`
      : `AI answers need ${status.llm_provider === "openai" ? "DEV_ASSISTANT_OPENAI_API_KEY" : "DEV_ASSISTANT_ANTHROPIC_API_KEY"} in the backend .env file; restart the API after adding it.`;
    const githubStatus = status.github_token_configured
      ? "A GitHub token is configured for private repositories."
      : "Public repositories work without a token; private repositories need a read-only GitHub token in .env.";
    backendStatusLabel.textContent = `API ready. ${modelStatus} ${githubStatus}`;
  } catch (error) {
    backendStatusLabel.textContent = `Can't reach the local API at 127.0.0.1:8000. Start it with uvicorn from the project root. ${explainError(error)}`;
  }
}

function showMessage(text, { error = false, citations = [] } = {}) {
  const card = document.createElement("article");
  card.className = error ? "message error" : "message";
  const body = document.createElement("p");
  body.textContent = text;
  card.append(body);

  for (const citation of citations) {
    const link = document.createElement("a");
    const encodedPath = citation.path.split("/").map(encodeURIComponent).join("/");
    link.href = `https://github.com/${encodeURIComponent(citation.repository_id.split("/")[0])}/${encodeURIComponent(citation.repository_id.split("/")[1])}/blob/${encodeURIComponent(citation.commit_sha)}/${encodedPath}#L${citation.start_line}-L${citation.end_line}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${citation.citation_id} · ${citation.path}:${citation.start_line}-${citation.end_line}`;
    card.append(document.createElement("br"), link);
  }
  messages.prepend(card);
}

async function waitForIndex(jobId) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const job = await request(`/repositories/jobs/${encodeURIComponent(jobId)}`);
    if (job.status === "succeeded") return job.result;
    if (job.status === "failed") throw new Error(job.error || "Repository indexing failed.");
    statusLabel.textContent = `Repository indexing: ${job.status}…`;
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  throw new Error("Indexing is taking longer than expected. You can keep this panel open and try again.");
}

refInput.addEventListener("input", () => restoreIndexedSnapshot());

connectButton.addEventListener("click", async () => {
  if (!pageContext) return;
  const repositoryId = pageContext.repositoryId;
  const ref = requestedRef();
  const snapshotKey = snapshotStorageKey(repositoryId, ref);
  connectButton.disabled = true;
  statusLabel.textContent = "Starting read-only repository indexing…";
  try {
    const result = await request("/repositories/connect", {
      method: "POST",
      body: JSON.stringify({ repository_id: repositoryId, ref }),
    });
    const indexed = await waitForIndex(result.job_id);
    await saveIndexedSnapshot(chrome.storage.local, repositoryId, ref, indexed);
    if (activeSnapshotKey() === snapshotKey) {
      indexedCommit = indexed.commit_sha;
      indexedSnapshotKey = snapshotKey;
      statusLabel.textContent = `Indexed ${indexed.file_count} files, ${indexed.chunk_count} code sections, ${indexed.dependency_count} imports, and ${indexed.call_count} cross-file calls at ${indexed.commit_sha.slice(0, 8)}.`;
    }
  } catch (error) {
    statusLabel.textContent = explainError(error);
  } finally {
    connectButton.disabled = false;
  }
});

reviewPullRequestButton.addEventListener("click", async () => {
  if (!pageContext?.pullRequestNumber) return;
  reviewPullRequestButton.disabled = true;
  statusLabel.textContent = `Reviewing pull request #${pageContext.pullRequestNumber} read-only…`;
  try {
    const review = await request("/assistant/review", {
      method: "POST",
      body: JSON.stringify({
        repository_id: pageContext.repositoryId,
        pull_request_number: pageContext.pullRequestNumber,
      }),
    });
    showMessage(`Review for #${review.pull_request_number}: ${review.title}\n\n${review.answer}`, {
      citations: review.citations,
    });
    statusLabel.textContent = `Reviewed pull request #${review.pull_request_number} at ${review.head_sha.slice(0, 8)}.`;
  } catch (error) {
    statusLabel.textContent = explainError(error);
  } finally {
    reviewPullRequestButton.disabled = false;
  }
});

investigateIssueButton.addEventListener("click", async () => {
  if (!pageContext?.issueNumber) return;
  const commitSha = currentIndexedCommit();
  if (!commitSha) {
    showMessage("Index this repository first so the issue can be traced against its source code.", {
      error: true,
    });
    return;
  }

  investigateIssueButton.disabled = true;
  statusLabel.textContent = `Investigating issue #${pageContext.issueNumber} read-only…`;
  try {
    const investigation = await request("/assistant/investigate-issue", {
      method: "POST",
      body: JSON.stringify({
        repository_id: pageContext.repositoryId,
        commit_sha: commitSha,
        issue_number: pageContext.issueNumber,
      }),
    });
    showMessage(
      `Investigation for issue #${investigation.issue_number}: ${investigation.title}\n${investigation.url}\n\n${investigation.answer}`,
      { citations: investigation.citations },
    );
    statusLabel.textContent = investigation.context_truncated
      ? `Investigated issue #${investigation.issue_number}; its report or comments were truncated.`
      : `Investigated issue #${investigation.issue_number}.`;
  } catch (error) {
    const message = explainError(error);
    showMessage(message, { error: true });
    statusLabel.textContent = message;
  } finally {
    investigateIssueButton.disabled = false;
  }
});

searchButton.addEventListener("click", async () => {
  const query = searchInput.value.trim();
  if (!pageContext || !query) return;
  const commitSha = currentIndexedCommit();
  if (!commitSha) {
    showMessage("Index this repository before searching its source code.", { error: true });
    return;
  }

  searchButton.disabled = true;
  statusLabel.textContent = "Searching indexed repository code…";
  try {
    const response = await request("/assistant/search", {
      method: "POST",
      body: JSON.stringify({
        repository_id: pageContext.repositoryId,
        commit_sha: commitSha,
        query,
        top_k: 8,
      }),
    });
    if (response.results.length === 0) {
      showMessage(`No code results found for “${query}”.`);
    } else {
      const summary = response.results.map((result) =>
        `${result.path}:L${result.start_line}-${result.end_line} · ${result.symbol_kind} ${result.symbol_name}\n${result.snippet}`
      ).join("\n\n");
      showMessage(`Code search for “${query}”\n\n${summary}`, {
        citations: response.results,
      });
    }
    statusLabel.textContent = `Found ${response.results.length} code matches.`;
  } catch (error) {
    const message = explainError(error);
    showMessage(message, { error: true });
    statusLabel.textContent = message;
  } finally {
    searchButton.disabled = false;
  }
});

document.querySelector("#question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pageContext) return;
  const question = questionInput.value.trim();
  if (!question) return;
  const asksForActivity = /\b(pr|pull requests?|issues?|bugs?|commits?)\b/i.test(question)
    && /\b(latest|last|newest|most recent|when)\b/i.test(question);
  const commitSha = currentIndexedCommit() || (asksForActivity ? "activity-only" : null);
  if (!commitSha) {
    showMessage("Index this repository first so I can answer from its source code.", { error: true });
    return;
  }

  askButton.disabled = true;
  showMessage(question);
  questionInput.value = "";
  try {
    const answer = await request("/assistant/ask", {
      method: "POST",
      body: JSON.stringify({
        repository_id: pageContext.repositoryId,
        commit_sha: commitSha,
        question,
        path: pageContext.path,
      }),
    });
    showMessage(answer.answer, { citations: answer.citations });
  } catch (error) {
    showMessage(explainError(error), { error: true });
  } finally {
    askButton.disabled = false;
  }
});

readActivePage()
  .then(() => {
    if (pageContext) return refreshBackendStatus();
  })
  .catch((error) => {
    contextLabel.textContent = error.message;
  });
