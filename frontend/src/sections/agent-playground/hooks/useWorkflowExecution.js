/**
 * Hook for managing workflow execution with visual states
 */
import { useCallback, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
  useGlobalVariablesDrawerStoreShallow,
} from "../store";
import { WORKFLOW_STATE } from "../utils/workflowExecution";
import { validateGraphForSave } from "../utils/workflowValidation";
import { VERSION_STATUS } from "../utils/constants";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  fetchGraphDataset,
  useExecuteDataset,
} from "../../../api/agent-playground/agent-playground";
import { useActivateVersion } from "../../../api/agent-playground/versions";

export default function useWorkflowExecution() {
  const { agentId } = useParams();
  const [searchParams] = useSearchParams();
  const graphId = agentId;
  const versionId = searchParams.get("version");

  const {
    nodes,
    edges,
    versionStatus,
    setValidationErrorNodeIds,
    clearValidationErrors,
    clearAllExecutionStates,
  } = useAgentPlaygroundStoreShallow((state) => ({
    nodes: state.nodes,
    edges: state.edges,
    versionStatus: state.currentAgent?.version_status,
    setValidationErrorNodeIds: state.setValidationErrorNodeIds,
    clearValidationErrors: state.clearValidationErrors,
    clearAllExecutionStates: state.clearAllExecutionStates,
  }));

  const { workflowState, startRun, stopRun, failRun, startPolling } =
    useWorkflowRunStoreShallow((state) => ({
      workflowState: state.workflowState,
      startRun: state.startRun,
      stopRun: state.stopRun,
      failRun: state.failRun,
      startPolling: state.startPolling,
    }));

  const { setOpen: setDrawerOpen, setPendingRun } =
    useGlobalVariablesDrawerStoreShallow((state) => ({
      setOpen: state.setOpen,
      setPendingRun: state.setPendingRun,
    }));

  const { mutateAsync: executeDataset } = useExecuteDataset();
  const { mutateAsync: activateVersion } = useActivateVersion();

  const isRunning = workflowState === WORKFLOW_STATE.RUNNING;
  const [isInitiating, setIsInitiating] = useState(false);

  const runWorkflow = useCallback(async () => {
    // Clear previous states
    clearValidationErrors();
    clearAllExecutionStates();
    setIsInitiating(true);

    // Validate graph (cycle detection + type-specific node checks) before running
    const result = validateGraphForSave(nodes, edges);
    if (!result.valid) {
      if (result.invalidNodeIds.length > 0) {
        setValidationErrorNodeIds(result.invalidNodeIds);
      }
      const message = result.hasCycle
        ? result.errors[0].message
        : result.invalidNodeIds.length === 1
          ? "Node not configured"
          : `${result.invalidNodeIds.length} nodes are not configured`;
      enqueueSnackbar(message, { variant: "error" });
      setIsInitiating(false);
      return;
    }

    // Validate variables — fresh API call, bypass cache
    try {
      const dataset = await fetchGraphDataset(graphId, versionId);
      const row = dataset?.rows?.[0];
      if (row?.cells?.length) {
        const hasEmpty = row.cells.some(
          (cell) => !cell.value || cell.value.trim() === "",
        );
        if (hasEmpty) {
          // Open the variables drawer and queue run after save
          setPendingRun(true);
          setDrawerOpen(true);
          enqueueSnackbar("Fill in all variables before running", {
            variant: "warning",
          });
          setIsInitiating(false);
          return;
        }
      }
    } catch {
      enqueueSnackbar("Failed to validate variables", { variant: "error" });
      setIsInitiating(false);
      return;
    }

    // Restore (activate) the current version before running if it's inactive
    if (versionId && versionStatus === VERSION_STATUS.INACTIVE) {
      try {
        await activateVersion({ graphId, versionId });
      } catch {
        enqueueSnackbar("Failed to restore version", { variant: "error" });
        setIsInitiating(false);
        return;
      }
    }

    // Reset previous run state and start execution via real API
    startRun();
    setIsInitiating(false);
    try {
      const res = await executeDataset({ graphId });
      const executionIds = res.data?.result?.execution_ids;
      if (executionIds?.[0]) {
        startPolling(executionIds[0]);
      } else {
        throw new Error("No execution IDs returned");
      }
    } catch (error) {
      const errorMessage =
        error?.response?.data?.result ||
        error?.message ||
        "Workflow execution failed";
      failRun(errorMessage);
      enqueueSnackbar(errorMessage, { variant: "error" });
    }
  }, [
    nodes,
    edges,
    graphId,
    versionId,
    versionStatus,
    clearValidationErrors,
    clearAllExecutionStates,
    setValidationErrorNodeIds,
    startRun,
    failRun,
    startPolling,
    executeDataset,
    activateVersion,
    setPendingRun,
    setDrawerOpen,
  ]);

  const stopWorkflow = useCallback(() => {
    clearAllExecutionStates();
    stopRun();
    enqueueSnackbar(
      "Exited workflow. It will continue running in the background.",
      { variant: "info" },
    );
  }, [clearAllExecutionStates, stopRun]);

  return {
    runWorkflow,
    stopWorkflow,
    isRunning,
    isInitiating,
    workflowState,
  };
}
