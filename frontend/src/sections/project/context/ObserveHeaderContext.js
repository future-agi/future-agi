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
  // Returns the current tab_type ("traces" | "spans") so save-view UIs know
  // what to persist as the view's tab_type. Registered by LLMTracingView.
  registerGetTabType: () => {},
  getTabType: () => "traces",
});

export const useObserveHeader = () => {
  return useContext(ObserveHeaderContext);
};
