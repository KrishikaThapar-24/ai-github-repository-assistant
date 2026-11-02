"use strict";

const RESERVED_FIRST_SEGMENTS = new Set(["settings", "marketplace", "features", "topics"]);

function decodeSegment(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function parseGitHubRepository(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.hostname !== "github.com") return null;

  const parts = parsed.pathname.split("/").filter(Boolean).map(decodeSegment);
  if (parts.length < 2 || RESERVED_FIRST_SEGMENTS.has(parts[0])) return null;

  const [owner, rawRepository, viewType, ref, ...fileParts] = parts;
  const isSourcePage = viewType === "blob" || viewType === "tree";
  const filePath = viewType === "blob" && fileParts.length ? fileParts.join("/") : null;
  const pullRequestNumber = viewType === "pull" && /^\d+$/.test(ref || "")
    ? Number(ref)
    : null;
  const issueNumber = viewType === "issues" && /^\d+$/.test(ref || "")
    ? Number(ref)
    : null;

  return {
    repositoryId: `${owner}/${rawRepository.replace(/\.git$/, "")}`,
    ref: isSourcePage && ref ? ref : "HEAD",
    path: filePath,
    pullRequestNumber,
    issueNumber,
    url: parsed.href,
  };
}
