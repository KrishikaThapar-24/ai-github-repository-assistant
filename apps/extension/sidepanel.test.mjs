import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { snapshotStorageKey } from "./snapshot_storage.js";

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

async function loadSidepanel({ url, sessionContext = null, respond }) {
  const selectors = [
    "#repository-context",
    "#ref-input",
    "#index-status",
    "#connect-button",
    "#ask-button",
    "#review-pr-button",
    "#investigate-issue-button",
    "#search-button",
    "#search-input",
    "#question-input",
    "#messages",
    "#question-form",
  ];
  const elements = new Map(selectors.map((selector) => [selector, new FakeElement()]));
  const promptButton = new FakeElement({ prompt: "Explain this repository." });
  const storedValues = new Map();
  const requests = [];
  const previousGlobals = {
    chrome: globalThis.chrome,
    document: globalThis.document,
    fetch: globalThis.fetch,
  };

  globalThis.document = {
    querySelector: (selector) => elements.get(selector),
    querySelectorAll: () => [promptButton],
    createElement: () => new FakeElement(),
  };
  globalThis.chrome = {
    storage: {
      session: {
        async get(key) {
          return key === "activeRepositoryTab" && sessionContext
            ? { activeRepositoryTab: sessionContext }
            : {};
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
      async query() {
        return [{ id: 17, url }];
      },
      onActivated: { addListener() {} },
      onUpdated: { addListener() {} },
    },
  };
  globalThis.fetch = async (requestUrl, options = {}) => {
    const request = {
      path: requestUrl.replace("http://127.0.0.1:8000/api/v1", ""),
      method: options.method ?? "GET",
      body: options.body ? JSON.parse(options.body) : null,
    };
    requests.push(request);
    const payload = await respond(request);
    return {
      ok: true,
      async json() {
        return payload;
      },
    };
  };

  let source = await readFile(new URL("./sidepanel.js", import.meta.url), "utf8");
  for (const moduleName of ["github_context.js", "snapshot_storage.js"]) {
    source = source.replaceAll(
      `"./${moduleName}"`,
      JSON.stringify(new URL(`./${moduleName}`, import.meta.url).href),
    );
  }
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}#sidepanel-test-${sidepanelInstance++}`;
  await import(moduleUrl);
  for (let attempt = 0; attempt < 500 && !elements.get("#repository-context").textContent; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2));
  }

  return {
    elements,
    promptButton,
    requests,
    storedValues,
    restore() {
      globalThis.chrome = previousGlobals.chrome;
      globalThis.document = previousGlobals.document;
      globalThis.fetch = previousGlobals.fetch;
    },
  };
}

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

    await panel.elements.get("#connect-button").dispatch("click");
    assert.deepEqual(panel.requests[0].body, { repository_id: "acme/shop", ref: "main" });
    assert.equal(panel.elements.get("#index-status").textContent.includes("Indexed 3 files"), true);
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
    const questionInput = panel.elements.get("#question-input");
    questionInput.value = "When was the last PR issued?";
    await panel.elements.get("#question-form").dispatch("submit", { preventDefault() {} });

    assert.deepEqual(
      panel.requests,
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

    assert.equal(panel.requests.length, 0);
  } finally {
    panel.restore();
  }
});
