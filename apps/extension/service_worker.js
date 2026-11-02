chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });

chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id == null) return;

  const contextSaved = chrome.storage.session.set({
    activeRepositoryTab: {
      tabId: tab.id,
      url: tab.url || "",
    },
  });
  const panelOpened = chrome.sidePanel.open({ tabId: tab.id });

  try {
    await Promise.all([contextSaved, panelOpened]);
  } catch (error) {
    console.error("Could not open the repository assistant panel:", error);
  }
});
