import { Box, CircularProgress } from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  startTransition,
} from "react";
import { ReactFlowProvider } from "@xyflow/react";
import NodeSelectionPanel from "./NodeSelectionPanel";
import EmptyView from "./EmptyView";
import GraphView from "./GraphView";
import TemplateLoadingOverlay from "./TemplateLoadingOverlay";
import { NodeDrawer } from "./NodeDrawer";
import {
  resetGlobalVariablesDrawerStore,
  resetWorkflowRunStore,
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
  useGlobalVariablesDrawerStoreShallow,
  useTemplateLoadingStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";
import GlobalVariablePanel from "./GlobalVariablesPanel/GlobalVariablePanel";
import { useNodeDrawerResize } from "./hooks";
import SaveDraftProvider from "./SaveDraftProvider";
import BuilderActions from "./BuilderActions";
import RunAgentPanel from "./RunAgentPanel/RunAgentPanel";
import { useGetVersionDetail } from "src/api/agent-playground/agent-playground";
import { useQueryClient } from "@tanstack/react-query";
import { VERSION_STATUS } from "../utils/constants";
import { useParams, useSearchParams, Navigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import useExecutionSync from "../hooks/useExecutionSync";

const SELECTION_PANEL_WIDTH = "230px";

export default function AgentBuilder() {
  const { agentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawUrlversionId = searchParams.get("version");
  const { globalVariablesDrawerOpen, setGlobalVariablesDrawerOpen } =
    useGlobalVariablesDrawerStoreShallow((state) => ({
      globalVariablesDrawerOpen: state.open,
      setGlobalVariablesDrawerOpen: state.setOpen,
    }));
  const {
    nodes,
    selectedNode,
    clearSelectedNode,
    loadVersion,
    updateVersion,
    clearAllExecutionStates,
  } = useAgentPlaygroundStoreShallow((state) => ({
    selectedNode: state.selectedNode,
    clearSelectedNode: state.clearSelectedNode,
    loadVersion: state.loadVersion,
    updateVersion: state.updateVersion,
    nodes: state.nodes,
    clearAllExecutionStates: state.clearAllExecutionStates,
  }));
  const { isLoadingTemplate } = useTemplateLoadingStoreShallow((state) => ({
    isLoadingTemplate: state.isLoadingTemplate,
  }));
  const queryClient = useQueryClient();

  // Load version data from API on mount
  const graphId = agentId;
  const versionId = rawUrlversionId;
  const { data: versionData, isLoading: isVersionLoading } =
    useGetVersionDetail(graphId, versionId, { staleTime: Infinity });

  useEffect(() => {
    if (!versionData) return;

    // During optimistic active→draft transitions, the store already has the
    // correct state (remapped IDs + optimistic edit). Skip loadVersion to
    // avoid overwriting it. The flag is cleared after one skip.
    const store = useAgentPlaygroundStore.getState();
    if (store._skipNextLoadVersion) {
      useAgentPlaygroundStore.setState({ _skipNextLoadVersion: false });
      return;
    }

    loadVersion(versionData);
    const freshAgent = useAgentPlaygroundStore.getState().currentAgent;
    if (freshAgent) {
      const isDraft = versionData.status === VERSION_STATUS.DRAFT;
      const versionName = `Version ${versionData.version_number}`;
      if (
        freshAgent.is_draft !== isDraft ||
        freshAgent.version_name !== versionName ||
        freshAgent.version_status !== versionData.status
      ) {
        updateVersion(versionData.id, versionData.version_number, {
          is_draft: isDraft,
          version_status: versionData.status,
        });
      }
    }
  }, [versionData, loadVersion, updateVersion]);

  const {
    showOutput,
    outputPanelHeight,
    setOutputPanelHeight,
    setShowOutput,
    executionId,
    isRunning,
  } = useWorkflowRunStoreShallow((state) => ({
    showOutput: state.showOutput,
    outputPanelHeight: state.outputPanelHeight,
    setOutputPanelHeight: state.setOutputPanelHeight,
    setShowOutput: state.setShowOutput,
    executionId: state.executionId,
    isRunning: state.isRunning,
  }));

  // Poll execution detail and sync node/edge visual states
  const { executionData, executionStatusRef } = useExecutionSync(
    graphId,
    executionId,
  );

  // Called after POST creates a new draft from an active version
  const handleCreateDraft = useCallback(
    (newVersionData) => {
      if (!newVersionData) return;
      updateVersion(newVersionData.id, newVersionData.version_number, {
        is_draft: true,
      });
      // Clear stale execution results and edge/node visual states —
      // this is a new draft version, previous run is no longer relevant.
      resetWorkflowRunStore();
      executionStatusRef.current = null;
      const store = useAgentPlaygroundStore.getState();
      store.setNodeExecutionStates({});
      store.setEdgeExecutionStates({});
      // Seed version-detail cache with POST response so useGetVersionDetail
      // finds data immediately when the URL changes — prevents loading flash.
      // Cancel any pending refetch for the new version detail query.
      const versionDetailKey = [
        "agent-playground",
        "version-detail",
        graphId,
        newVersionData.id,
      ];
      queryClient.cancelQueries({ queryKey: versionDetailKey });
      queryClient.setQueryData(versionDetailKey, {
        data: { result: newVersionData },
      });
      // Mark version list and graph detail as stale without triggering an
      // immediate refetch. This avoids loadVersion re-running and wiping
      // optimistic nodes that were added during the in-flight POST.
      // The data refreshes naturally when the user navigates to those views.
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-versions", graphId],
        refetchType: "none",
      });
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph", graphId],
        refetchType: "none",
      });
      // Sync React Router so useSearchParams returns the draft version ID.
      setSearchParams({ version: newVersionData.id }, { replace: true });
    },
    [updateVersion, queryClient, graphId, executionStatusRef, setSearchParams],
  );

  const isDrawerOpen = !!selectedNode;
  const nodeDrawerResize = useNodeDrawerResize(isDrawerOpen);

  const hasNodes = nodes?.length > 0;

  // Defer heavy GraphView mount so the loader paints first on tab switch
  const [showGraph, setShowGraph] = useState(false);
  useEffect(() => {
    if (!versionData) return;
    startTransition(() => {
      setShowGraph(true);
    });
  }, [versionData]);

  // Hide EmptyView overlay while dragging a node onto the empty canvas
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const dragCounterRef = useRef(0);

  const handleCanvasDragEnter = useCallback((e) => {
    e.preventDefault();
    dragCounterRef.current += 1;
    if (dragCounterRef.current === 1) setIsDraggingOver(true);
  }, []);

  const handleCanvasDragLeave = useCallback(() => {
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) setIsDraggingOver(false);
  }, []);

  const handleCanvasDrop = useCallback(() => {
    dragCounterRef.current = 0;
    setIsDraggingOver(false);
  }, []);

  const handleDrawerClose = useCallback(() => {
    clearSelectedNode();
  }, [clearSelectedNode]);

  const handleCloseRunPanel = useCallback(() => {
    setShowOutput(false);
  }, [setShowOutput]);

  const renderCanvasContent = () => {
    if (isVersionLoading || !showGraph) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
          }}
        >
          <CircularProgress size={32} />
        </Box>
      );
    }
    if (isLoadingTemplate) {
      return <TemplateLoadingOverlay />;
    }
    return (
      <>
        <GraphView />
        {!hasNodes && !isDraggingOver && <EmptyView />}
      </>
    );
  };

  useEffect(() => {
    return () => {
      resetWorkflowRunStore();
      resetGlobalVariablesDrawerStore();
      clearAllExecutionStates();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!agentId) {
    enqueueSnackbar("Missing agent", { variant: "error" });
    return <Navigate to="/dashboard/agents" replace />;
  }

  return (
    <ReactFlowProvider>
      <SaveDraftProvider onCreateDraft={handleCreateDraft}>
        <Box
          sx={{
            position: "relative",
            width: "100%",
            height: "100%",
          }}
        >
          <NodeSelectionPanel
            width={SELECTION_PANEL_WIDTH}
            disabled={isLoadingTemplate || isRunning || isVersionLoading}
          />
          <Box
            sx={{
              display: "flex",
              height: "100%",
              width: `calc(100% - ${SELECTION_PANEL_WIDTH})`,
              marginLeft: SELECTION_PANEL_WIDTH,
            }}
          >
            <Box
              sx={{
                flex: 1,
                bgcolor: "background.paper",
                height: "100%",
                overflow: "hidden",
                position: "relative",
              }}
              {...(!hasNodes && {
                onDragEnter: handleCanvasDragEnter,
                onDragLeave: handleCanvasDragLeave,
                onDrop: handleCanvasDrop,
              })}
            >
              {renderCanvasContent()}
            </Box>
            <NodeDrawer
              open={isDrawerOpen && !isLoadingTemplate}
              onClose={handleDrawerClose}
              node={selectedNode}
              width={nodeDrawerResize.width}
              isResizing={nodeDrawerResize.isResizing}
              onResizeStart={nodeDrawerResize.handleResizeStart}
            />
            {hasNodes && !isLoadingTemplate && (
              <GlobalVariablePanel
                globalVariablesDrawerOpen={globalVariablesDrawerOpen}
                setGlobalVariablesDrawerOpen={setGlobalVariablesDrawerOpen}
              />
            )}
            <BuilderActions width={SELECTION_PANEL_WIDTH} hasNodes={hasNodes} />
            {/* Run Agent Panel - Shows output after workflow run */}
            {hasNodes && showOutput && !isLoadingTemplate && (
              <RunAgentPanel
                graphId={graphId}
                panelHeight={outputPanelHeight}
                onResize={setOutputPanelHeight}
                onClose={handleCloseRunPanel}
                executionId={executionId}
                executionData={executionData}
              />
            )}
          </Box>
        </Box>
      </SaveDraftProvider>
    </ReactFlowProvider>
  );
}
