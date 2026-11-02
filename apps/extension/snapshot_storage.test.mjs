import assert from "node:assert/strict";
import test from "node:test";

import {
  loadIndexedSnapshot,
  saveIndexedSnapshot,
  snapshotStorageKey,
} from "./snapshot_storage.js";

function createStorage() {
  const values = new Map();
  return {
    async get(key) {
      return values.has(key) ? { [key]: values.get(key) } : {};
    },
    async set(entries) {
      for (const [key, value] of Object.entries(entries)) values.set(key, value);
    },
  };
}

test("persists a commit for a specific repository and branch", async () => {
  const storage = createStorage();
  const report = { commit_sha: "deadbeef1234567", file_count: 12 };

  await saveIndexedSnapshot(storage, "acme/shop", "feature/checkout", report);
  const loaded = await loadIndexedSnapshot(storage, "acme/shop", "feature/checkout");

  assert.equal(loaded.repositoryId, "acme/shop");
  assert.equal(loaded.ref, "feature/checkout");
  assert.equal(loaded.commitSha, report.commit_sha);
  assert.ok(loaded.indexedAt);
  assert.equal(await loadIndexedSnapshot(storage, "acme/shop", "main"), null);
});

test("normalizes empty refs and rejects invalid index reports", async () => {
  const storage = createStorage();

  await saveIndexedSnapshot(storage, "acme/shop", "  ", { commit_sha: "abcdef1234567" });

  assert.equal(snapshotStorageKey("acme/shop", ""), snapshotStorageKey("acme/shop", "HEAD"));
  assert.equal((await loadIndexedSnapshot(storage, "acme/shop", "HEAD")).ref, "HEAD");
  await assert.rejects(
    saveIndexedSnapshot(storage, "acme/shop", "main", { commit_sha: "short" }),
    TypeError,
  );
});
