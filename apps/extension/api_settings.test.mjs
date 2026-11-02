import assert from "node:assert/strict";
import test from "node:test";

import {
  API_ORIGIN_KEY,
  API_TOKEN_KEY,
  DEFAULT_API_ORIGIN,
  loadApiSettings,
  normalizeApiOrigin,
  saveApiSettings,
} from "./api_settings.js";

function createStorage() {
  const localValues = new Map();
  const sessionValues = new Map();
  return {
    localValues,
    sessionValues,
    local: {
      async get(key) {
        return localValues.has(key) ? { [key]: localValues.get(key) } : {};
      },
      async set(values) {
        for (const [key, value] of Object.entries(values)) localValues.set(key, value);
      },
    },
    session: {
      async get(key) {
        return sessionValues.has(key) ? { [key]: sessionValues.get(key) } : {};
      },
      async set(values) {
        for (const [key, value] of Object.entries(values)) sessionValues.set(key, value);
      },
      async remove(key) {
        sessionValues.delete(key);
      },
    },
  };
}

test("uses a local API by default and accepts remote HTTPS origins", async () => {
  const storage = createStorage();

  assert.equal((await loadApiSettings(storage)).apiOrigin, DEFAULT_API_ORIGIN);
  assert.equal(normalizeApiOrigin(" https://assistant.example.com/ "), "https://assistant.example.com");
  assert.equal(normalizeApiOrigin("http://localhost:8001"), "http://localhost:8001");
});

test("rejects unsafe remote HTTP and API URLs containing paths or credentials", () => {
  assert.throws(() => normalizeApiOrigin("http://assistant.example.com"), /must use HTTPS/);
  assert.throws(() => normalizeApiOrigin("https://assistant.example.com/api/v1"), /only the API origin/);
  assert.throws(() => normalizeApiOrigin("https://user:secret@assistant.example.com"), /only the API origin/);
});

test("validates the stored API origin before returning a session token", async () => {
  const storage = createStorage();
  storage.localValues.set(API_ORIGIN_KEY, "http://attacker.example.com");
  storage.sessionValues.set(API_TOKEN_KEY, "private-token");

  await assert.rejects(loadApiSettings(storage), /must use HTTPS/);
});

test("requests exact host access and stores API tokens only for the browser session", async () => {
  const storage = createStorage();
  const permissionRequests = [];
  const permissions = {
    async contains() {
      return false;
    },
    async request(request) {
      permissionRequests.push(request);
      return true;
    },
  };

  const origin = await saveApiSettings(storage, permissions, {
    apiOrigin: "https://assistant.example.com",
    apiToken: "do-not-persist",
    clearToken: false,
  });

  assert.equal(origin, "https://assistant.example.com");
  assert.deepEqual(permissionRequests, [{ origins: ["https://assistant.example.com/*"] }]);
  assert.equal(storage.localValues.get(API_ORIGIN_KEY), origin);
  assert.equal(storage.localValues.has(API_TOKEN_KEY), false);
  assert.equal(storage.sessionValues.get(API_TOKEN_KEY), "do-not-persist");

  await saveApiSettings(storage, permissions, {
    apiOrigin: origin,
    apiToken: "",
    clearToken: true,
  });
  assert.equal(storage.sessionValues.has(API_TOKEN_KEY), false);
});

test("reuses an already granted local API permission", async () => {
  const storage = createStorage();
  let requested = false;
  const origin = await saveApiSettings(storage, {
    async contains({ origins }) {
      return origins[0] === "http://127.0.0.1:8001/*";
    },
    async request() {
      requested = true;
      return false;
    },
  }, {
    apiOrigin: DEFAULT_API_ORIGIN,
    apiToken: "local-token",
    clearToken: false,
  });

  assert.equal(origin, DEFAULT_API_ORIGIN);
  assert.equal(requested, false);
  assert.equal(storage.sessionValues.get(API_TOKEN_KEY), "local-token");
});

test("does not save settings when Chrome denies access to the API origin", async () => {
  const storage = createStorage();
  await assert.rejects(
    saveApiSettings(storage, { contains: async () => false, request: async () => false }, {
      apiOrigin: "https://assistant.example.com",
      apiToken: "secret",
      clearToken: false,
    }),
    /did not grant.*not saved/i,
  );
  assert.equal(storage.localValues.size, 0);
  assert.equal(storage.sessionValues.size, 0);
});
