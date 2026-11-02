"use strict";

export function snapshotStorageKey(repositoryId, ref) {
  const normalizedRef = ref.trim() || "HEAD";
  return `indexed-snapshot:${encodeURIComponent(repositoryId)}:${encodeURIComponent(normalizedRef)}`;
}

export async function loadIndexedSnapshot(storage, repositoryId, ref) {
  const key = snapshotStorageKey(repositoryId, ref);
  const stored = await storage.get(key);
  const snapshot = stored?.[key];
  if (!snapshot || typeof snapshot.commitSha !== "string") return null;
  return snapshot;
}

export async function saveIndexedSnapshot(storage, repositoryId, ref, report) {
  if (!report || typeof report.commit_sha !== "string" || report.commit_sha.length < 7) {
    throw new TypeError("An indexed snapshot must include a valid commit SHA.");
  }
  const key = snapshotStorageKey(repositoryId, ref);
  const snapshot = {
    repositoryId,
    ref: ref.trim() || "HEAD",
    commitSha: report.commit_sha,
    indexedAt: new Date().toISOString(),
  };
  await storage.set({ [key]: snapshot });
  return snapshot;
}
