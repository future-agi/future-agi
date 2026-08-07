import { createContext, useContext } from "react";

export const ObserveHeaderContext = createContext({
  headerConfig: {
    text: "",
    filterTrace: null,
    filterSpan: null,
    selectedTab: null,
    filterSession: null,
    refreshData: null,
    resetFilters: null,
    gridApi: null,
    toolbarElement: null,
  },
  setHeaderConfig: () => {},
  activeViewConfig: null,
  setActiveViewConfig: () => {},
  // Callback registered by LLMTracingView so save-view UIs (ObserveTabBar,
  // ViewConfigModal) can snapshot current filters/display at save time.
  // Pass null to unregister.
  registerGetViewConfig: () => {},
  getViewConfig: () => null,
});

export const useObserveHeader = () => {
  return useContext(ObserveHeaderContext);
};
