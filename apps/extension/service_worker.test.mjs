import assert from "node:assert/strict";
import test from "node:test";

let serviceWorkerInstance = 0;

async function loadServiceWorker() {
  let actionHandler;
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
        },
      },
    },
    sidePanel: {
      async open(value) {
        calls.push({ type: "open", value });
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
    assert.deepEqual(worker.calls, []);
  } finally {
    worker.restore();
  }
});
