import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { REPLAY_MODULES } from "./configurations";

export const useReplaySessionsStore = create((set, get, store) => ({
  // keeps track of the current step in the replay session creation process
  currentStep: 0,
  // keeps track of the validation status of each step in the replay session creation process
  validatedSteps: [],
  setCurrentStep: (step) => set({ currentStep: step }),
  setValidatedStep: (step, validated) =>
    set((state) => ({
      validatedSteps: { ...state.validatedSteps, [step]: validated },
    })),
  // keeps track of the expanded drawer view
  expandView: false,
  setExpandView: (expand) => set({ expandView: expand }),
  // keeps track of the type of replay session, new scenario group or existing scenario group
  replayType: null,
  setReplayType: (type) => set({ replayType: type }),
  // search for existing scenario group
  searchQuery: "",
  setSearchQuery: (query) => set({ searchQuery: query }),
  // keeps track of the selected scenario group in the existing scenario group flow
  selectedGroup: null,
  setSelectedGroup: (group) => set({ selectedGroup: group }),
  //open confirm close dialog when user tries to close the replay session drawer
  isConfirmationModalOpen: false,
  setIsConfirmationModalOpen: (open) => set({ isConfirmationModalOpen: open }),
  // stores the created scenario in the first step of the replay session creation process, being used to fetch scenario details and
  createdScenario: null,
  setCreatedScenario: (scenario) => set({ createdScenario: scenario }),
  // stores the create scenarions form data as is so it can be restored when user navigates back to the first step
  formData: null,
  setFormData: (data) => set({ formData: data }),
  // control the open state of the create scenarios dialog which has options to create new scenario group or add to existing scenario group
  openCreateScenarios: false,
  setOpenCreateScenarios: (open) => set({ openCreateScenarios: open }),
  // control the collapsed state of the replay session drawer
  isReplayDrawerCollapsed: {
    [REPLAY_MODULES.TRACES]: false,
    [REPLAY_MODULES.SESSIONS]: false,
  },
  setIsReplayDrawerCollapsed: (module, collapsed) =>
    set((state) => ({
      isReplayDrawerCollapsed: {
        ...state.isReplayDrawerCollapsed,
        [module]: collapsed,
      },
    })),
  // control the open state of the replay session drawer for each module
  openReplaySessionDrawer: {
    [REPLAY_MODULES.TRACES]: false,
    [REPLAY_MODULES.SESSIONS]: false,
  },
  setOpenReplaySessionDrawer: (module, open) =>
    set((state) => ({
      openReplaySessionDrawer: {
        ...state.openReplaySessionDrawer,
        [module]: open,
      },
    })),
  // stores the replay session data when user clicks on the replay button, which then opens the replay session drawer
  createdReplay: null,
  setCreatedReplay: (replay) => set({ createdReplay: replay }),
  // reset the store to its initial state
  reset: () => {
    set(store.getInitialState());
  },
}));

// shallow selector for the replay sessions store
export const useReplaySessionsStoreShallow = (fun) =>
  useReplaySessionsStore(useShallow(fun));

// reset the replay sessions store
export const resetReplaySessionsStore = () => {
  useReplaySessionsStore.getState().reset();
};

// store for the sessions grid state
export const useSessionsGridStore = create((set, get, store) => ({
  // keeps track of the toggled nodes in the sessions grid
  toggledNodes: [],
  // keeps track of the select all state of the sessions grid
  selectAll: false,
  setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
  setSelectAll: (value) => set(() => ({ selectAll: value })),
  // keeps track of the total row count of the sessions grid
  totalRowCount: 0,
  totalRowCountLowerBound: null,
  totalRowCountIsLowerBound: false,
  setTotalRowCount: (value) => set(() => ({ totalRowCount: value })),
  // reset the store to its initial state
  reset: () => {
    set(store.getInitialState());
  },
}));

export const useSessionsGridStoreShallow = (fun) =>
  useSessionsGridStore(useShallow(fun));

export const resetSessionsGridStore = () => {
  useSessionsGridStore.getState().reset();
};
