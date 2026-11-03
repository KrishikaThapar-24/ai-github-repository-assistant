import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { snapshotStorageKey } from "./snapshot_storage.js";
import { API_ORIGIN_KEY, API_TOKEN_KEY } from "./api_settings.js";

let sidepanelInstance = 0;

class FakeElement {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.listeners = new Map();
    this.children = [];
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) ?? [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  async dispatch(name, event = {}) {
    for (const listener of this.listeners.get(name) ?? []) {
      await listener(event);
    }
  }

  append(...children) {
    this.children.push(...children);
  }

  prepend(...children) {
    this.children.unshift(...children);
  }

  focus() {}
}

async function loadSidepanel({ url, sessionContext = null, apiOrigin, apiToken, respond }) {
  const selectors = [
    "#repository-context",
    "#settings-button",
    "#ref-input",
    "#index-status",
    "#backend-status",
    "#connect-button",
    "#ask-button",
    "#review-pr-button",
    "#investigate-issue-button",
    "#explain-file-button",
    "#search-button",
    "#search-input",
    "#question-input",
    "#messages",
    "#question-form",
  ];
  const elements = new Map(selectors.map((selector) => [selector, new FakeElement()]));
  const promptButton = new FakeElement({ prompt: "Explain this repository." });
  const explainFileButton = new FakeElement({
    prompt: "Explain this file's purpose, its important functions and classes, and how it fits into the repository.",
  });
  elements.set("#explain-file-button", explainFileButton);
  const storedValues = new Map();
  const storedSessionValues = new Map();
  if (apiOrigin) storedValues.set(API_ORIGIN_KEY, apiOrigin);
  if (apiToken) storedSessionValues.set(API_TOKEN_KEY, apiToken);
  const requests = [];
  let lastTabQuery = null;
  let activeUrl = url;
  let tabUpdatedListener = null;
  const previousGlobals = {
    chrome: globalThis.chrome,
    document: globalThis.document,
    fetch: globalThis.fetch,
  };

  globalThis.document = {
    querySelector: (selector) => elements.get(selector),
    querySelectorAll: () => [promptButton, explainFileButton],
    createElement: () => new FakeElement(),
  };
  globalThis.chrome = {
    storage: {
      session: {
        async get(key) {
          if (key === "activeRepositoryTab" && sessionContext) {
            return { activeRepositoryTab: sessionContext };
          }
          return storedSessionValues.has(key) ? { [key]: storedSessionValues.get(key) } : {};
        },
        async set(entries) {
          for (const [key, value] of Object.entries(entries)) storedSessionValues.set(key, value);
        },
        async remove(key) {
          storedSessionValues.delete(key);
        },
      },
      local: {
        async get(key) {
          return storedValues.has(key) ? { [key]: storedValues.get(key) } : {};
        },
        async set(entries) {
          for (const [key, value] of Object.entries(entries)) storedValues.set(key, value);
        },
      },
    },
    tabs: {
      query(query, callback) {
        lastTabQuery = query;
        const tabs = [{ id: 17, url: activeUrl }];
        if (callback) {
          callback(tabs);
          return undefined;
        }
        return Promise.resolve(tabs);
      },
      onActivated: { addListener() {} },
      onUpdated: {
        addListener(listener) {
          tabUpdatedListener = listener;
        },
      },
    },
    runtime: { openOptionsPage() {} },
  };
  globalThis.fetch = async (requestUrl, options = {}) => {
    const url = new URL(requestUrl);
    const request = {
      path: url.pathname.replace("/api/v1", ""),
      method: options.method ?? "GET",
      body: options.body ? JSON.parse(options.body) : null,
    };
    if (options.headers?.Authorization) {
      request.headers = { Authorization: options.headers.Authorization };
    }
    requests.push(request);
    const payload = request.path === "/status"
      ? {
          api_status: "ready",
          llm_provider: "openai",
          llm_configured: false,
          github_activity_backend: "rest",
          github_token_configured: false,
        }
      : await respond(request);
    return {
      ok: true,
      async json() {
        return payload;
      },
    };
  };

  let source = await readFile(new URL("./sidepanel.js", import.meta.url), "utf8");
  for (const moduleName of ["github_context.js", "snapshot_storage.js", "api_settings.js"]) {
    source = source.replaceAll(
      `"./${moduleName}"`,
      JSON.stringify(new URL(`./${moduleName}`, import.meta.url).href),
    );
  }
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}#sidepanel-test-${sidepanelInstance++}`;
  await import(moduleUrl);
  for (
    let attempt = 0;
    attempt < 500 && !elements.get("#backend-status").textContent;
    attempt += 1
  ) {
    await new Promise((resolve) => setTimeout(resolve, 2));
  }

  return {
    elements,
    promptButton,
    explainFileButton,
    requests,
    storedValues,
    storedSessionValues,
    get lastTabQuery() {
      return lastTabQuery;
    },
    async updateActiveUrl(nextUrl, expectedContext) {
      if (!tabUpdatedListener) throw new Error("The side panel did not register a tab update listener.");
      activeUrl = nextUrl;
      tabUpdatedListener(17, { url: nextUrl });
      for (let attempt = 0; attempt < 500; attempt += 1) {
        if (elements.get("#repository-context").textContent === expectedContext) return;
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      throw new Error("The side panel did not update its GitHub page context.");
    },
    restore() {
      globalThis.chrome = previousGlobals.chrome;
      globalThis.document = previousGlobals.document;
      globalThis.fetch = previousGlobals.fetch;
    },
  };
}

test("uses the configured HTTPS API and sends the session token only in the header", async () => {
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop",
    apiOrigin: "https://assistant.example.com",
    apiToken: "session-only-token",
    respond: async () => ({ results: [] }),
  });

  try {
    const statusRequest = panel.requests[0];
    assert.equal(statusRequest.path, "/status");
    assert.equal(statusRequest.headers.Authorization, "Bearer session-only-token");
    assert.equal(panel.storedValues.get(API_ORIGIN_KEY), "https://assistant.example.com");
    assert.equal(panel.storedValues.has(API_TOKEN_KEY), false);
    assert.equal(panel.storedSessionValues.get(API_TOKEN_KEY), "session-only-token");
  } finally {
    panel.restore();
  }
});

test("indexes the active repository and searches the matching snapshot with citations", async () => {
  const commitSha = "deadbeef1234567890";
  const hit = {
    citation_id: "S1",
    repository_id: "acme/shop",
    commit_sha: commitSha,
    path: "src/auth.py",
    start_line: 3,
    end_line: 8,
    symbol_name: "authenticate",
    symbol_kind: "function",
    language: "python",
    snippet: "def authenticate(token): ...",
    score: 0.8,
  };
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop/blob/main/src/auth.py",
    async respond(request) {
      if (request.path === "/repositories/connect") return { job_id: "job-1" };
      if (request.path === "/repositories/jobs/job-1") {
        return {
          status: "succeeded",
          result: {
            commit_sha: commitSha,
            file_count: 3,
            chunk_count: 4,
            dependency_count: 2,
            call_count: 1,
          },
        };
      }
      if (request.path === "/assistant/search") return { results: [hit] };
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    assert.equal(panel.elements.get("#repository-context").textContent, "acme/shop · src/auth.py");
    assert.equal(panel.elements.get("#ref-input").value, "main");
    assert.equal(panel.explainFileButton.hidden, false);
    assert.deepEqual(panel.lastTabQuery, { active: true, lastFocusedWindow: true });

    await panel.elements.get("#connect-button").dispatch("click");
    const connectRequest = panel.requests.find((request) => request.path === "/repositories/connect");
    assert.deepEqual(connectRequest.body, { repository_id: "acme/shop", ref: "main" });
    assert.equal(panel.elements.get("#index-status").textContent.includes("Indexed 3 files"), true);
    assert.match(panel.elements.get("#backend-status").textContent, /AI answers need DEV_ASSISTANT_OPENAI_API_KEY/);
    assert.match(panel.elements.get("#backend-status").textContent, /Public repositories work without a token/);
    assert.equal(
      panel.storedValues.get(snapshotStorageKey("acme/shop", "main")).commitSha,
      commitSha,
    );

    panel.elements.get("#search-input").value = "authentication token";
    await panel.elements.get("#search-button").dispatch("click");

    const searchRequest = panel.requests.find((request) => request.path === "/assistant/search");
    assert.deepEqual(searchRequest.body, {
      repository_id: "acme/shop",
      commit_sha: commitSha,
      query: "authentication token",
      top_k: 8,
    });
    const [message, , citation] = panel.elements.get("#messages").children[0].children;
    assert.match(message.textContent, /Code search for/);
    assert.match(citation.href, /src\/auth\.py#L3-L8$/);
  } finally {
    panel.restore();
  }
});

test("accepts a repository indexed inline by the API", async () => {
  const commitSha = "cafebabefeed1234";
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop",
    async respond(request) {
      if (request.path === "/repositories/connect") {
        return {
          status: "succeeded",
          result: {
            commit_sha: commitSha,
            file_count: 2,
            chunk_count: 3,
            dependency_count: 1,
            call_count: 0,
          },
        };
      }
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    await panel.elements.get("#connect-button").dispatch("click");

    assert.equal(
      panel.elements.get("#index-status").textContent.includes("Indexed 2 files"),
      true,
    );
    assert.equal(
      panel.storedValues.get(snapshotStorageKey("acme/shop", "HEAD")).commitSha,
      commitSha,
    );
    assert.equal(
      panel.requests.some((request) => request.path.startsWith("/repositories/jobs/")),
      false,
    );
  } finally {
    panel.restore();
  }
});

test("explains the active GitHub source file using its indexed repository context", async () => {
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop/blob/main/src/auth.py",
    async respond(request) {
      if (request.path === "/repositories/connect") return { job_id: "job-file" };
      if (request.path === "/repositories/jobs/job-file") {
        return {
          status: "succeeded",
          result: {
            commit_sha: "deadbeef12345678",
            file_count: 2,
            chunk_count: 3,
            dependency_count: 1,
            call_count: 0,
          },
        };
      }
      if (request.path === "/assistant/ask") {
        return { answer: "This module validates credentials.", citations: [] };
      }
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    await panel.elements.get("#connect-button").dispatch("click");
    await panel.explainFileButton.dispatch("click");
    await panel.elements.get("#question-form").dispatch("submit", { preventDefault() {} });

    const askRequest = panel.requests.find((request) => request.path === "/assistant/ask");
    assert.deepEqual(askRequest.body, {
      repository_id: "acme/shop",
      commit_sha: "deadbeef12345678",
      question: panel.explainFileButton.dataset.prompt,
      path: "src/auth.py",
    });
    assert.equal(
      panel.elements.get("#messages").children[0].children[0].textContent,
      "This module validates credentials.",
    );
  } finally {
    panel.restore();
  }
});

test("asks GitHub activity questions before a repository snapshot is indexed", async () => {
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop",
    async respond(request) {
      if (request.path === "/assistant/ask") {
        return { answer: "The latest pull request is #42.", citations: [] };
      }
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    assert.equal(panel.explainFileButton.hidden, true);
    const questionInput = panel.elements.get("#question-input");
    questionInput.value = "When was the last PR issued?";
    await panel.elements.get("#question-form").dispatch("submit", { preventDefault() {} });

    assert.deepEqual(
      panel.requests.filter((request) => request.path !== "/status"),
      [
        {
          path: "/assistant/ask",
          method: "POST",
          body: {
            repository_id: "acme/shop",
            commit_sha: "activity-only",
            question: "When was the last PR issued?",
            path: null,
          },
        },
      ],
      `repository context: ${panel.elements.get("#repository-context").textContent}`,
    );
    assert.equal(
      panel.elements.get("#messages").children[0].children[0].textContent,
      "The latest pull request is #42.",
    );
  } finally {
    panel.restore();
  }
});

test("explains how to enable generated answers when the model key is missing", async () => {
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop",
    async respond(request) {
      if (request.path === "/repositories/connect") return { job_id: "job-2" };
      if (request.path === "/repositories/jobs/job-2") {
        return {
          status: "succeeded",
          result: {
            commit_sha: "deadbeef12345678",
            file_count: 1,
            chunk_count: 1,
            dependency_count: 0,
            call_count: 0,
          },
        };
      }
      if (request.path === "/assistant/ask") {
        throw new Error("Set DEV_ASSISTANT_OPENAI_API_KEY to enable the configured OpenAI model.");
      }
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    await panel.elements.get("#connect-button").dispatch("click");
    panel.elements.get("#question-input").value = "What does the main page do?";
    await panel.elements.get("#question-form").dispatch("submit", { preventDefault() {} });

    const errorMessage = panel.elements.get("#messages").children[0].children[0].textContent;
    assert.match(errorMessage, /ignored \.env file and restart the API/);
    assert.match(errorMessage, /code search, and GitHub activity still work/);
  } finally {
    panel.restore();
  }
});

test("uses the toolbar-click tab context when Chrome hides the tab URL", async () => {
  const panel = await loadSidepanel({
    url: undefined,
    sessionContext: {
      tabId: 17,
      url: "https://github.com/acme/shop/blob/main/src/auth.py",
    },
    async respond(request) {
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    assert.equal(panel.elements.get("#repository-context").textContent, "acme/shop · src/auth.py");
    assert.equal(panel.elements.get("#ref-input").value, "main");

    assert.deepEqual(panel.requests.map((request) => request.path), ["/status"]);
  } finally {
    panel.restore();
  }
});

test("tracks GitHub navigation in the panel's last-focused window", async () => {
  const panel = await loadSidepanel({
    url: "https://github.com/acme/shop/blob/main/src/auth.py",
    async respond(request) {
      throw new Error(`Unexpected API request: ${request.path}`);
    },
  });

  try {
    await panel.updateActiveUrl(
      "https://github.com/acme/shop/pull/8/files",
      "acme/shop · PR #8",
    );

    assert.deepEqual(panel.lastTabQuery, { active: true, lastFocusedWindow: true });
    assert.equal(panel.elements.get("#review-pr-button").hidden, false);
    assert.equal(panel.elements.get("#explain-file-button").hidden, true);
  } finally {
    panel.restore();
  }
});
