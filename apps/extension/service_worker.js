chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id == null) return;

  await chrome.storage.session.set({
    activeRepositoryTab: {
      tabId: tab.id,
      url: tab.url || "",
    },
  });
  await chrome.sidePanel.open({ tabId: tab.id });
});
