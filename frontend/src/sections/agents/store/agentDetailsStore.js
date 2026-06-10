import { create } from "zustand";

export const useAgentDetailsStore = create((set, get) => ({
  selectedVersion: "",
  setSelectedVersion: (version) => {
    set({ selectedVersion: version });
    const url = new URL(window.location);
    if (version) {
      url.searchParams.set("version", version);
    } else {
      url.searchParams.delete("version");
    }
    window.history.replaceState({}, "", url);
  },

  agentName: "",
  setAgentName: (name) => set({ agentName: name }),

  agentDetails: {},
  setAgentDetails: (details) => set({ agentDetails: details }),

  latestVersionNumber: 0,
  setLatestVersionNumber: (versionNum) =>
    set({ latestVersionNumber: versionNum }),

  initFromUrl: () => {
    const params = new URLSearchParams(window.location.search);
    const version = params.get("version");
    if (version && version !== get().selectedVersion) {
      set({ selectedVersion: version });
    }
  },

  syncFromUrl: () => {
    const params = new URLSearchParams(window.location.search);
    const version = params.get("version");
    if (version && version !== get().selectedVersion) {
      set({ selectedVersion: version });
    }
  },

  resetAgentDetails: () => {
    set({
      selectedVersion: null,
      agentName: "",
      agentDetails: {},
      latestVersionNumber: 0,
    });
    // Clear URL params when resetting
    const url = new URL(window.location);
    url.searchParams.delete("version");
    window.history.replaceState({}, "", url);
  },
}));

useAgentDetailsStore.getState().initFromUrl();
