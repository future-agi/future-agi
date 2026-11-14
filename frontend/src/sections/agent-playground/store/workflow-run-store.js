import { create } from "src/utils/zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";
import { WORKFLOW_STATE } from "../utils/workflowExecution";

// Re-export for convenience — consumers import WORKFLOW_STATE from the store
export { WORKFLOW_STATE };

export const useWorkflowRunStore = create(
  devtools(
    (set, get, store) => ({
      // Run state
      isRunning: false,
      hasRun: false,
      runError: null,
      executionId: null,

      // Workflow execution state
      workflowState: WORKFLOW_STATE.IDLE,
      setWorkflowState: (state) =>
        set(
          {
            workflowState: state,
            isRunning: state === WORKFLOW_STATE.RUNNING,
          },
          false,
          "setWorkflowState",
        ),

      // Output panel state
      showOutput: false,
      outputPanelHeight: 300,

      // Selected node for output view (defaults to last node after run)
      selectedOutputNodeId: null,

      // Run results per node
      runResults: {},

      // Actions
      setIsRunning: (isRunning) => set({ isRunning }, false, "setIsRunning"),

      setExecutionId: (id) => set({ executionId: id }, false, "setExecutionId"),

      // Store execution ID and keep running — panel stays closed until completion
      startPolling: (executionId) =>
        set({ executionId, isRunning: true }, false, "startPolling"),

      setShowOutput: (showOutput) =>
        set({ showOutput }, false, "setShowOutput"),

      setOutputPanelHeight: (height) =>
        set({ outputPanelHeight: height }, false, "setOutputPanelHeight"),

      setSelectedOutputNodeId: (nodeId) =>
        set({ selectedOutputNodeId: nodeId }, false, "setSelectedOutputNodeId"),

      setRunResults: (nodeId, results) => {
        const { runResults } = get();
        set(
          { runResults: { ...runResults, [nodeId]: results } },
          false,
          "setRunResults",
        );
      },

      startRun: () =>
        set(
          {
            isRunning: true,
            hasRun: false,
            runError: null,
            runResults: {},
            executionId: null,
            showOutput: true,
            selectedOutputNodeId: null,
            workflowState: WORKFLOW_STATE.RUNNING,
          },
          false,
          "startRun",
        ),

      completeRun: (results, lastNodeId) =>
        set(
          {
            isRunning: false,
            hasRun: true,
            showOutput: true,
            runResults: results,
            selectedOutputNodeId: lastNodeId,
            workflowState: WORKFLOW_STATE.COMPLETED,
          },
          false,
          "completeRun",
        ),

      failRun: (error, results = null) =>
        set(
          {
            isRunning: false,
            runError: error,
            workflowState: WORKFLOW_STATE.ERROR,
            ...(results && {
              hasRun: true,
              showOutput: true,
              runResults: results,
            }),
          },
          false,
          "failRun",
        ),

      stopRun: () =>
        set(
          {
            isRunning: false,
            executionId: null,
            workflowState: WORKFLOW_STATE.IDLE,
            showOutput: false,
            hasRun: false,
            runError: null,
            selectedOutputNodeId: null,
          },
          false,
          "stopRun",
        ),

      // Execution stop confirmation dialog (shared by Tabs + Header)
      executionStopDialog: {
        open: false,
        callback: null,
      },
      openExecutionStopDialog: (callback) =>
        set(
          { executionStopDialog: { open: true, callback } },
          false,
          "openExecutionStopDialog",
        ),
      closeExecutionStopDialog: () =>
        set(
          { executionStopDialog: { open: false, callback: null } },
          false,
          "closeExecutionStopDialog",
        ),
      confirmExecutionStopDialog: () => {
        const { executionStopDialog } = get();
        if (executionStopDialog.callback) {
          executionStopDialog.callback();
        }
        // Reset all workflow run state (stops polling, closes dialog)
        set(store.getInitialState(), false, "confirmExecutionStopDialog");
      },

      reset: () => {
        set(store.getInitialState(), false, "reset");
      },
    }),
    {
      name: "WorkflowRunStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useWorkflowRunStoreShallow = (fun) =>
  useWorkflowRunStore(useShallow(fun));

export const resetWorkflowRunStore = () => {
  useWorkflowRunStore.getState().reset();
};
