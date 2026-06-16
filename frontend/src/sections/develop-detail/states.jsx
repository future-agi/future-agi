import { getRandomId } from "src/utils/utils";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export const DefaultFilter = {
  columnId: "",
  filterConfig: {
    filterType: "text",
    filterOp: "equals",
    filterValue: "",
  },
};

// Run Prompt Store, this state also stores configure run prompt state
export const useRunPromptStore = create((set) => ({
  openRunPrompt: null,
  setOpenRunPrompt: (value) =>
    set((state) => ({
      openRunPrompt:
        typeof value === "function" ? value(state.openRunPrompt) : value,
    })),
}));

// Run Evaluation Store
export const useRunEvaluationStore = create((set) => ({
  openRunEvaluation: false,
  // When set, EvaluationDrawer will open the EvalPicker in edit mode for this user-eval id.
  pendingEditEvalId: null,
  setOpenRunEvaluation: (value) =>
    set((state) => ({
      openRunEvaluation:
        typeof value === "function" ? value(state.openRunEvaluation) : value,
    })),
  openEditEvalFromColumn: (userEvalId) =>
    set(() => ({
      openRunEvaluation: true,
      pendingEditEvalId: userEvalId,
    })),
  clearPendingEditEval: () => set(() => ({ pendingEditEvalId: null })),
}));

// Run Compare Evaluation Store
export const useRunCompareEvaluationStore = create((set) => ({
  openRunCompareEvaluation: false,
  setOpenRunCompareEvaluation: (value) =>
    set(() => ({ openRunCompareEvaluation: value })),
}));

// Run Experiment Store
export const useRunExperimentStore = create((set) => ({
  openRunExperiment: false,

  selectedExperiment: null,

  currentStep: 0,
  mode: null,
  validatedSteps: [false, false, false],
  setStepValidated: (stepIndex, isValid) =>
    set((state) => {
      const newValidatedSteps = [...state.validatedSteps];
      newValidatedSteps[stepIndex] = isValid;
      return { validatedSteps: newValidatedSteps };
    }),
  setCurrentStep: (stepIndex) => set(() => ({ currentStep: stepIndex })),
  initiateEditMode: (value) => {
    set(() => ({
      currentStep: 0,
      mode: "edit",
      validatedSteps: [false, false],
      selectedExperiment: value,
      openRunExperiment: true,
    }));
  },
  initiateCreateMode: () => {
    set(() => ({
      currentStep: 0,
      mode: "create",
      validatedSteps: [false, false, false],
      openRunExperiment: true,
    }));
  },
  nextStep: () =>
    set((state) => {
      const maxStep = state.mode === "edit" ? 1 : 2;
      return { currentStep: Math.min(state.currentStep + 1, maxStep) };
    }),
  prevStep: () =>
    set((state) => ({
      currentStep: Math.max(state.currentStep - 1, 0),
    })),
  reset: () =>
    set(() => ({
      openRunExperiment: false,
      selectedExperiment: null,
      currentStep: 0,
      mode: null,
      validatedSteps: [false, false, false],
    })),
}));
export const useRunExperimentStoreShallow = (func) =>
  useRunExperimentStore(useShallow(func));
// Run Optimization Store
export const useRunOptimizationStore = create((set) => ({
  openRunOptimization: false,
  setOpenRunOptimization: (value) =>
    set(() => ({ openRunOptimization: value })),
}));

// Run Annotations Store
export const useRunAnnotationsStore = create((set) => ({
  openRunAnnotations: false,
  setOpenRunAnnotations: (value) => set(() => ({ openRunAnnotations: value })),
}));

// Add Column API Call Store
export const useAddColumnApiCallStore = create((set) => ({
  openAddColumnApiCall: false,
  setOpenAddColumnApiCall: (value) =>
    set(() => ({ openAddColumnApiCall: value })),
}));

// Extract Entities Store
export const useExtractEntitiesStore = create((set) => ({
  openExtractEntities: false,
  setOpenExtractEntities: (value) =>
    set(() => ({ openExtractEntities: value })),
}));

// Conditional Node Store
export const useConditionalNodeStore = create((set) => ({
  openConditionalNode: false,
  setOpenConditionalNode: (value) =>
    set(() => ({ openConditionalNode: value })),
}));

// Classification Store
export const useClassificationStore = create((set) => ({
  openClassification: false,
  setOpenClassification: (value) => set(() => ({ openClassification: value })),
}));

// Execute Code Store
export const useExecuteCodeStore = create((set) => ({
  openExecuteCode: false,
  setOpenExecuteCode: (value) => set(() => ({ openExecuteCode: value })),
}));

// Extract JSON Key Store
export const useExtractJsonKeyStore = create((set) => ({
  openExtractJsonKey: false,
  setOpenExtractJsonKey: (value) => set(() => ({ openExtractJsonKey: value })),
}));

// Retrieval Store
export const useRetrievalStore = create((set) => ({
  openRetrieval: false,
  setOpenRetrieval: (value) => set(() => ({ openRetrieval: value })),
}));

export const useAddColumnDrawerStore = create((set) => ({
  openAddColumnDrawer: false,
  setOpenAddColumnDrawer: (value) =>
    set(() => ({ openAddColumnDrawer: value })),
}));

export const useDevelopCellHeight = create((set) => ({
  cellHeight: "Short",
  setCellHeight: (value) => set(() => ({ cellHeight: value })),
}));

export const useConfigureEvalStore = create((set) => ({
  configureEval: null,
  setConfigureEval: (value) => set(() => ({ configureEval: value })),
}));

export const useShowSummaryStore = create((set) => ({
  showSummary: JSON.parse(localStorage.getItem("showSummary")) || [],
  setShowSummary: (value) => {
    localStorage.setItem("showSummary", JSON.stringify(value));
    set(() => ({ showSummary: value }));
  },
  toggleSummary: (column) =>
    set((state) => {
      const newShowSummary = state.showSummary.includes(column.id)
        ? state.showSummary.filter((id) => id !== column.id)
        : [...state.showSummary, column.id];
      localStorage.setItem("showSummary", JSON.stringify(newShowSummary));
      return { showSummary: newShowSummary };
    }),
}));

export const useEditColumnNameStore = create((set) => ({
  editColumnName: null,
  setEditColumnName: (value) => set(() => ({ editColumnName: value })),
}));

export const useEditColumnTypeStore = create((set) => ({
  editColumnType: null,
  setEditColumnType: (value) => set(() => ({ editColumnType: value })),
}));

export const useDeleteColumnStore = create((set) => ({
  deleteColumn: null,
  setDeleteColumn: (value) => set(() => ({ deleteColumn: value })),
}));

export const useRerunDependentColumnsStore = create((set) => ({
  rerunDependentColumns: null,
  setRerunDependentColumns: (value) => set(() => ({ rerunDependentColumns: value })),
}));


export const useAddEvaluationFeebackStore = create((set) => ({
  addEvaluationFeeback: null,
  setAddEvaluationFeeback: (value) =>
    set(() => ({ addEvaluationFeeback: value })),
}));

export const useImprovePromptStore = create((set) => ({
  improvePrompt: null,
  setImprovePrompt: (value) => set(() => ({ improvePrompt: value })),
}));

export const useEditCellStore = create((set) => ({
  editCell: null,
  setEditCell: (value) => set(() => ({ editCell: value })),
}));

export const useDevelopSelectedRowsStore = create((set) => ({
  toggledNodes: [],
  selectAll: false,
  setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
  setSelectAll: (value) => set(() => ({ selectAll: value })),
  resetSelectedRows: () => set(() => ({ selectedRows: [], selectAll: false })),
}));

export const useDevelopFilterStore = create((set) => ({
  isDevelopFilterOpen: false,
  setDevelopFilterOpen: (value) =>
    set((state) => {
      const newIsDevelopFilterOpen =
        typeof value === "function" ? value(state.isDevelopFilterOpen) : value;
      return { isDevelopFilterOpen: newIsDevelopFilterOpen };
    }),
  filters: [{ ...DefaultFilter, id: getRandomId() }],
  setFilters: (value) =>
    set((state) => ({
      filters: typeof value === "function" ? value(state.filters) : value,
    })),
  resetFilters: () =>
    set(() => ({
      filters: [{ ...DefaultFilter, id: getRandomId() }],
      isDevelopFilterOpen: false,
    })),
}));

export const useDevelopSearchStore = create((set) => ({
  search: "",
  setSearch: (value) => set(() => ({ search: value })),
}));

export const useDatapointDrawerStore = create((set) => ({
  datapoint: null,
  column: null,
  setDatapoint: (value) => set(() => ({ datapoint: value })),
  setDrawerColumn: (value) => set(() => ({ column: value })),
}));

export const useDatasetOriginStore = create((set) => ({
  datasetOrigin: null,
  setOrigin: (datasetOrigin) => set({ datasetOrigin }),
  processingComplete: false,
  setProcessingComplete: (processingComplete) => set({ processingComplete }),
}));

export const useProcessingStore = create((set) => ({
  isProcessingData: false,
  setIsProcessingData: (value) => set({ isProcessingData: value }),
  reset: () => set({ isProcessingData: false }),
}));

export const useCustomAudioDialog = create((set) => ({
  isCustomAudioModalOpen: false,
  setIsCustomAudioModalOpen: (v) => set({ isCustomAudioModalOpen: v }),
}));

export const useRetrievalStoreShallow = (fun) =>
  useRetrievalStore(useShallow(fun));
export const useRunPromptStoreShallow = (fun) =>
  useRunPromptStore(useShallow(fun));
export const useRunEvaluationStoreShallow = (fun) =>
  useRunEvaluationStore(useShallow(fun));
export const useAddColumnApiCallStoreShallow = (fun) =>
  useAddColumnApiCallStore(useShallow(fun));
export const useExtractEntitiesStoreShallow = (fun) =>
  useExtractEntitiesStore(useShallow(fun));
export const useConditionalNodeStoreShallow = (fun) =>
  useConditionalNodeStore(useShallow(fun));
export const useClassificationStoreShallow = (fun) =>
  useClassificationStore(useShallow(fun));
export const useExecuteCodeStoreShallow = (fun) =>
  useExecuteCodeStore(useShallow(fun));
export const useExtractJsonKeyStoreShallow = (fun) =>
  useExtractJsonKeyStore(useShallow(fun));
export const useAddColumnDrawerStoreShallow = (fun) =>
  useAddColumnDrawerStore(useShallow(fun));
export const useShowSummaryStoreShallow = (fun) =>
  useShowSummaryStore(useShallow(fun));
export const useDevelopSelectedRowsStoreShallow = (fun) =>
  useDevelopSelectedRowsStore(useShallow(fun));
export const useDevelopFilterStoreShallow = (fun) =>
  useDevelopFilterStore(useShallow(fun));
export const useEditCellStoreShallow = (fun) =>
  useEditCellStore(useShallow(fun));
export const useDatapointDrawerStoreShallow = (fun) =>
  useDatapointDrawerStore(useShallow(fun));

export const resetAllStates = () => {
  // Reset all modal/dialog states to closed
  useRunPromptStore.getState().setOpenRunPrompt(null);
  useRunEvaluationStore.getState().setOpenRunEvaluation(false);
  useRunCompareEvaluationStore.getState().setOpenRunCompareEvaluation(false);
  useRunExperimentStore.getState().reset();
  useRunOptimizationStore.getState().setOpenRunOptimization(false);
  useRunAnnotationsStore.getState().setOpenRunAnnotations(false);
  useAddColumnApiCallStore.getState().setOpenAddColumnApiCall(false);
  useExtractEntitiesStore.getState().setOpenExtractEntities(false);
  useConditionalNodeStore.getState().setOpenConditionalNode(false);
  useClassificationStore.getState().setOpenClassification(false);
  useExecuteCodeStore.getState().setOpenExecuteCode(false);
  useExtractJsonKeyStore.getState().setOpenExtractJsonKey(false);
  useRetrievalStore.getState().setOpenRetrieval(false);
  useAddColumnDrawerStore.getState().setOpenAddColumnDrawer(false);

  // Reset cell height to default
  useDevelopCellHeight.getState().setCellHeight("Short");

  // Reset configuration states
  useConfigureEvalStore.getState().setConfigureEval(null);

  // Reset summary state and clear localStorage
  useShowSummaryStore.getState().setShowSummary([]);

  // Reset edit states
  useEditColumnNameStore.getState().setEditColumnName(null);
  useEditColumnTypeStore.getState().setEditColumnType(null);
  useDeleteColumnStore.getState().setDeleteColumn(null);
  useRerunDependentColumnsStore.getState().setRerunDependentColumns(null);
  useAddEvaluationFeebackStore.getState().setAddEvaluationFeeback(null);
  useImprovePromptStore.getState().setImprovePrompt(null);
  useEditCellStore.getState().setEditCell(null);

  // Reset selection states
  useDevelopSelectedRowsStore.getState().setToggledNodes([]);
  useDevelopSelectedRowsStore.getState().setSelectAll(false);

  // Reset filter states
  useDevelopFilterStore.getState().setDevelopFilterOpen(false);
  useDevelopFilterStore
    .getState()
    .setFilters([{ ...DefaultFilter, id: getRandomId() }]);

  // Reset search state
  useDevelopSearchStore.getState().setSearch("");

  // Reset datapoint drawer state
  useDatapointDrawerStore.getState().setDatapoint(null);
  useDatapointDrawerStore.getState().setDrawerColumn(null);

  useDatasetOriginStore.getState().setOrigin(null);
  useDatasetOriginStore.getState().setProcessingComplete(false);
};
