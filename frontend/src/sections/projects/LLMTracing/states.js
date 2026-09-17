import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

/**
 * Converts a string to camelCase with "set" prefix.
 * @param {string} str - The string to convert
 * @returns {string} camelCase string with "set" prefix
 */
function toCamelCaseSetter(str) {
  return `set${str.charAt(0).toUpperCase()}${str.slice(1)}`;
}

/**
 * Creates a URL-synced state property for Zustand stores.
 * The value is JSON stringified in the URL query parameter.
 *
 * @param {string} urlKey - The URL query parameter key (e.g., "filter", "view")
 * @param {any} defaultValue - Default value if not found in URL
 * @returns {Function} A function that takes (set, get) and returns the state object
 *
 * @example
 * // Creates state "filter" with setter "setFilter"
 * const useStore = create((set, get) => ({
 *   ...createUrlSyncedState("filter", {})(set, get),
 *   ...createUrlSyncedState("view", "grid")(set, get),
 * }));
 * // Access: useStore((state) => state.filter)
 * // Set: useStore.getState().setFilter(value)
 */
function createUrlSyncedState(urlKey, defaultValue = null) {
  return (set, get) => {
    const stateKey = urlKey;
    const setterKey = toCamelCaseSetter(urlKey);
    const initKey = `initUrlState_${urlKey}`;

    return {
      // State property (uses urlKey directly)
      [stateKey]: (() => {
        // Initialize from URL on creation
        if (typeof window !== "undefined") {
          const params = new URLSearchParams(window.location.search);
          const urlParam = params.get(urlKey);
          if (urlParam) {
            try {
              return JSON.parse(urlParam);
            } catch {
              return defaultValue;
            }
          }
        }
        return defaultValue;
      })(),
      // Setter that updates both Zustand state and URL (camelCase with "set" prefix)
      // Preserves all other existing URL parameters - only updates the specific urlKey
      [setterKey]: (value) => {
        set({ [stateKey]: value });
        if (typeof window !== "undefined") {
          // Create URL from current location to preserve all existing params
          const url = new URL(window.location);
          if (value !== null && value !== undefined) {
            url.searchParams.set(urlKey, JSON.stringify(value));
          } else {
            url.searchParams.delete(urlKey);
          }
          // Use replaceState to update URL without page refresh, preserving all other params
          window.history.replaceState({}, "", url.toString());
        }
      },
      // Initializer that reads from URL (can be called manually if needed)
      [initKey]: () => {
        if (typeof window === "undefined") return;
        const params = new URLSearchParams(window.location.search);
        const urlParam = params.get(urlKey);
        if (urlParam) {
          try {
            const parsedValue = JSON.parse(urlParam);
            const currentValue = get()[stateKey];
            if (JSON.stringify(parsedValue) !== JSON.stringify(currentValue)) {
              set({ [stateKey]: parsedValue });
            }
          } catch {
            // If parsing fails, ignore and keep current state
          }
        }
      },
    };
  };
}

export const useLLMTracingStore = create((set, get) => ({
  ...createUrlSyncedState("traceDetailDrawerOpen", null)(set, get),
  ...createUrlSyncedState("spanDetailDrawerOpen", null)(set, get),
  ...createUrlSyncedState("primaryCollapsed", true)(set, get),
  ...createUrlSyncedState("compareCollapsed", true)(set, get),
  // Display panel: visualization mode — "graph" | "agentGraph" | "agentPath"
  ...createUrlSyncedState("viewMode", "graph")(set, get),
  // Visible trace IDs from the grid — used for prev/next navigation in drawer
  visibleTraceIds: [],
  setVisibleTraceIds: (ids) => set({ visibleTraceIds: ids }),
  resetStates: () => {
    set({
      traceDetailDrawerOpen: null,
      spanDetailDrawerOpen: null,
      primaryCollapsed: true,
      compareCollapsed: true,
      viewMode: "graph",
      visibleTraceIds: [],
    });
  },
}));

export const useLLMTracingStoreShallow = (fun) =>
  useLLMTracingStore(useShallow(fun));

export const useTraceGridStore = create((set) => ({
  toggledNodes: [],
  selectAll: false,
  totalRowCount: 0,
  totalRowCountLowerBound: null,
  totalRowCountIsLowerBound: false,
  setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
  setSelectAll: (value) => set(() => ({ selectAll: value })),
  setTotalRowCount: (value) =>
    set({
      totalRowCount: value,
      totalRowCountLowerBound: null,
      totalRowCountIsLowerBound: false,
    }),
}));

export const resetTraceGridStore = () => {
  useTraceGridStore.setState({
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 0,
    totalRowCountLowerBound: null,
    totalRowCountIsLowerBound: false,
  });
};
export const useTraceGridStoreShallow = (fun) =>
  useTraceGridStore(useShallow(fun));

export const useSpanGridStore = create((set, get, store) => ({
  toggledNodes: [],
  selectAll: false,
  totalRowCount: 0,
  totalRowCountLowerBound: null,
  totalRowCountIsLowerBound: false,
  setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
  setSelectAll: (value) => set(() => ({ selectAll: value })),
  setTotalRowCount: (value) =>
    set({
      totalRowCount: value,
      totalRowCountLowerBound: null,
      totalRowCountIsLowerBound: false,
    }),
  reset: () => {
    set(store.getInitialState());
  },
}));

export const useSpanGridStoreShallow = (fun) =>
  useSpanGridStore(useShallow(fun));

export const resetSpanGridStore = () => {
  useSpanGridStore.getState().reset();
};
