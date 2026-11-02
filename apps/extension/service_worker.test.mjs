import assert from "node:assert/strict";
import test from "node:test";

let serviceWorkerInstance = 0;

async function loadServiceWorker() {
  let actionHandler;
  let panelOpened = false;
  const calls = [];
  const previousChrome = globalThis.chrome;

  globalThis.chrome = {
    action: {
      onClicked: {
        addListener(listener) {
          actionHandler = listener;
        },
      },
    },
    storage: {
      session: {
        async set(value) {
          calls.push({ type: "store", value });
          return {
            then(resolve, reject) {
              if (!panelOpened) {
                reject(new Error("storage write was awaited before opening the panel"));
                return;
              }
              resolve();
            },
          };
        },
      },
    },
    sidePanel: {
      async setPanelBehavior(value) {
        calls.push({ type: "behavior", value });
      },
      async open(value) {
        calls.push({ type: "open", value });
        panelOpened = true;
      },
    },
  };

  await import(`./service_worker.js?test=${serviceWorkerInstance++}`);
  return {
    actionHandler,
    calls,
    restore() {
      globalThis.chrome = previousChrome;
    },
  };
}

test("stores the clicked GitHub tab before opening its side panel", async () => {
  const worker = await loadServiceWorker();
  const tab = { id: 17, url: "https://github.com/acme/shop" };

  try {
    await worker.actionHandler(tab);

    assert.deepEqual(worker.calls, [
      { type: "behavior", value: { openPanelOnActionClick: false } },
      {
        type: "store",
        value: { activeRepositoryTab: { tabId: 17, url: tab.url } },
      },
      { type: "open", value: { tabId: 17 } },
    ]);
  } finally {
    worker.restore();
  }
});

test("ignores action events without an associated tab", async () => {
  const worker = await loadServiceWorker();

  try {
    await worker.actionHandler({});
    assert.deepEqual(worker.calls, [
      { type: "behavior", value: { openPanelOnActionClick: false } },
    ]);
  } finally {
    worker.restore();
  }
});
