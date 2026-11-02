export const DEFAULT_API_ORIGIN = "http://127.0.0.1:8001";
export const API_ORIGIN_KEY = "devAssistantApiOrigin";
export const API_TOKEN_KEY = "devAssistantApiToken";

export function normalizeApiOrigin(value) {
  let url;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("Enter a valid API origin, such as https://assistant.example.com.");
  }

  if (url.username || url.password || url.search || url.hash || !["/", ""].includes(url.pathname)) {
    throw new Error("Use only the API origin. Do not include credentials, a path, or query values.");
  }
  if (url.protocol !== "https:" && !isLocalHost(url.hostname)) {
    throw new Error("Remote API addresses must use HTTPS.");
  }
  return url.origin;
}

function isLocalHost(hostname) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

export async function loadApiSettings(storage) {
  const [localValues, sessionValues] = await Promise.all([
    storage.local.get(API_ORIGIN_KEY),
    storage.session.get(API_TOKEN_KEY),
  ]);
  return {
    apiOrigin: normalizeApiOrigin(localValues[API_ORIGIN_KEY] || DEFAULT_API_ORIGIN),
    apiToken: sessionValues[API_TOKEN_KEY] || "",
  };
}

export async function saveApiSettings(storage, permissions, { apiOrigin, apiToken, clearToken }) {
  const normalizedOrigin = normalizeApiOrigin(apiOrigin);
  const origins = [`${normalizedOrigin}/*`];
  const alreadyGranted = await permissions.contains({ origins });
  const permissionGranted = alreadyGranted || await permissions.request({ origins });
  if (!permissionGranted) {
    throw new Error("Chrome did not grant access to that API host. The settings were not saved.");
  }

  await storage.local.set({ [API_ORIGIN_KEY]: normalizedOrigin });
  if (clearToken) {
    await storage.session.remove(API_TOKEN_KEY);
  } else if (apiToken.trim()) {
    await storage.session.set({ [API_TOKEN_KEY]: apiToken.trim() });
  }
  return normalizedOrigin;
}
