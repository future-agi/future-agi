import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";
import { useSelectedSimulatorAgentsStore } from "./TestRuns/states";
import {
  createGridSelectionStore,
  createEvaluationDialogStore,
} from "src/sections/common/simulation";

// Store for test evaluation dialog (uses factory)
const testEvaluationStore = createEvaluationDialogStore("openTestEvaluation");
export const useTestEvaluationStore = testEvaluationStore.store;
export const useTestEvaluationStoreShallow =
  testEvaluationStore.useStoreShallow;

// Store for selected executions (custom store, not using factory)
export const useSelectedExecutionsStore = create((set, get) => ({
  selectedExecutions: [],
  setSelectedExecutions: (value) =>
    set({
      selectedExecutions:
        typeof value === "function" ? value(get().selectedExecutions) : value,
    }),
}));

export const useSelectedExecutionsStoreShallow = (fun) =>
  useSelectedExecutionsStore(useShallow(fun));

// Store for test runs grid selection (uses factory)
const testRunsGridStore = createGridSelectionStore();
export const useTestRunsGridStore = testRunsGridStore.store;
export const useTestRunsGridStoreShallow = testRunsGridStore.useStoreShallow;

export const resetState = () => {
  useTestEvaluationStore.setState({ openTestEvaluation: false });
  useSelectedSimulatorAgentsStore.setState({
    selectedSimulatorAgent: null,
  });
  useSelectedExecutionsStore.setState({
    selectedExecutions: [],
  });
};
