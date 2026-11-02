import { loadApiSettings, saveApiSettings } from "./api_settings.js";

const originInput = document.querySelector("#api-origin");
const tokenInput = document.querySelector("#api-token");
const clearTokenInput = document.querySelector("#clear-token");
const statusLabel = document.querySelector("#settings-status");
const form = document.querySelector("#settings-form");

const savedSettings = await loadApiSettings(chrome.storage);
originInput.value = savedSettings.apiOrigin;
statusLabel.textContent = savedSettings.apiToken
  ? "A bearer token is available until Chrome closes."
  : "No bearer token is saved for this browser session.";

clearTokenInput.addEventListener("change", () => {
  tokenInput.disabled = clearTokenInput.checked;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusLabel.textContent = "Requesting permission for this API host…";
  try {
    const savedOrigin = await saveApiSettings(chrome.storage, chrome.permissions, {
      apiOrigin: originInput.value,
      apiToken: tokenInput.value,
      clearToken: clearTokenInput.checked,
    });
    tokenInput.value = "";
    clearTokenInput.checked = false;
    tokenInput.disabled = false;
    statusLabel.textContent = `Connection saved for ${savedOrigin}. Reopen the side panel to reconnect.`;
  } catch (error) {
    statusLabel.textContent = error instanceof Error ? error.message : String(error);
  }
});
