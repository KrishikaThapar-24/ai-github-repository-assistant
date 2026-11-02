import assert from "node:assert/strict";
import test from "node:test";

import { parseGitHubRepository } from "./github_context.js";

test("parses a GitHub source URL with an encoded slash in the branch name", () => {
  const context = parseGitHubRepository(
    "https://github.com/acme/shop/blob/feature%2Fcheckout/src%2Fapi.py",
  );

  assert.equal(context.repositoryId, "acme/shop");
  assert.equal(context.ref, "feature/checkout");
  assert.equal(context.path, "src/api.py");
  assert.equal(context.pullRequestNumber, null);
});

test("uses the selected branch from a repository tree URL", () => {
  const context = parseGitHubRepository(
    "https://github.com/acme/shop/tree/release%2F2026/docs",
  );

  assert.equal(context.ref, "release/2026");
  assert.equal(context.path, null);
});

test("recognizes pull request pages including nested tabs", () => {
  const context = parseGitHubRepository("https://github.com/acme/shop/pull/42/files");

  assert.equal(context.repositoryId, "acme/shop");
  assert.equal(context.ref, "HEAD");
  assert.equal(context.pullRequestNumber, 42);
});

test("recognizes issue pages including nested comment routes", () => {
  const context = parseGitHubRepository(
    "https://github.com/acme/shop/issues/57#issuecomment-123",
  );

  assert.equal(context.repositoryId, "acme/shop");
  assert.equal(context.issueNumber, 57);
  assert.equal(context.pullRequestNumber, null);
});

test("rejects invalid URLs and unrelated hosts", () => {
  assert.equal(parseGitHubRepository("not a URL"), null);
  assert.equal(parseGitHubRepository("https://github.com.evil.example/acme/shop"), null);
  assert.equal(parseGitHubRepository("https://github.com/settings/profile"), null);
});
