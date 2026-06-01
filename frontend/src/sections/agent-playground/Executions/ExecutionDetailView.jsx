import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { AgentGraph } from "src/components/AgentGraph";
import { START_ID, END_ID } from "src/components/AgentGraph/layoutUtils";
import NodeOutputDetail from "../AgentBuilder/RunAgentPanel/NodeOutputDetail";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import { useGetExecutionDetail } from "src/api/agent-playground/agent-playground";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import useResolvedExecution from "../hooks/useResolvedExecution";
import { EXECUTION_STATUS } from "../utils/workflowExecution";
import AgentOnboardingFocusPanel from "../components/AgentOnboardingFocusPanel";
import {
  agentSetupQuickStartAttributionFromSearch,
  buildAgentEvalBuilderHref,
  buildAgentTraceReviewedPayload,
} from "../agentOnboardingEvents";

export default function ExecutionDetailView({ graphId, executionId }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const {
    data: executionData,
    isLoading,
    isError,
  } = useGetExecutionDetail(graphId, executionId);

  const [selectedNodeId, setSelectedNodeId] = useState(null);

  // Invalidate executions list and node details when polling reaches terminal status
  const prevStatusRef = useRef(null);
  const nodeStatusesRef = useRef({});
  useEffect(() => {
    prevStatusRef.current = null;
    nodeStatusesRef.current = {};
  }, [executionId]);
  useEffect(() => {
    const status = executionData?.status?.toLowerCase();
    if (!status || status === prevStatusRef.current) return;
    prevStatusRef.current = status;

    if (
      status === EXECUTION_STATUS.SUCCESS ||
      status === EXECUTION_STATUS.ERROR ||
      status === EXECUTION_STATUS.FAILED
    ) {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-executions", graphId],
      });
    }
  }, [executionData, graphId, queryClient]);

  // Invalidate node-execution-detail when individual nodes reach terminal status
  useEffect(() => {
    if (!executionId || !executionData?.nodes) return;

    const newNodeStatuses = {};
    for (const node of executionData.nodes) {
      const currentStatus = node.nodeExecution?.status?.toLowerCase();
      const nodeExecId = node.nodeExecution?.id;
      const prevNodeStatus = nodeStatusesRef.current[node.id];

      if (nodeExecId && currentStatus && currentStatus !== prevNodeStatus) {
        const isTerminal =
          currentStatus === EXECUTION_STATUS.SUCCESS ||
          currentStatus === EXECUTION_STATUS.ERROR ||
          currentStatus === EXECUTION_STATUS.FAILED ||
          currentStatus === EXECUTION_STATUS.SKIPPED;
        if (isTerminal) {
          queryClient.invalidateQueries({
            queryKey: [
              "agent-playground",
              "node-execution-detail",
              executionId,
              nodeExecId,
            ],
          });
        }
      }
      newNodeStatuses[node.id] = currentStatus;
    }
    nodeStatusesRef.current = newNodeStatuses;
  }, [executionData, executionId, queryClient]);

  // Reset node selection when execution changes, then auto-select last executed node
  useEffect(() => {
    if (!executionData?.nodes?.length) {
      setSelectedNodeId(null);
      return;
    }
    // Find last node that has a nodeExecution (skip pending nodes)
    const executedNodes = executionData.nodes.filter(
      (n) => n.nodeExecution || n.node_execution,
    );
    if (executedNodes.length === 0) {
      setSelectedNodeId(null);
      return;
    }
    const lastNode = executedNodes[executedNodes.length - 1];
    if (lastNode.subGraph?.nodes?.length) {
      const executedInner = lastNode.subGraph.nodes.filter(
        (n) => n.nodeExecution || n.node_execution,
      );
      if (executedInner.length > 0) {
        const lastInner = executedInner[executedInner.length - 1];
        setSelectedNodeId(`${lastNode.id}__${lastInner.id}`);
        return;
      }
    }
    setSelectedNodeId(lastNode.id);
  }, [executionId, executionData]);

  const { nodeExecutionId: selectedNodeExecutionId, resolvedExecutionId } =
    useResolvedExecution({ selectedNodeId, executionData, executionId });
  const onboardingMode = new URLSearchParams(location.search).get("onboarding");
  const versionId = new URLSearchParams(location.search).get("version");
  const quickStartAttribution = useMemo(
    () => agentSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const showCoverageHandoff =
    onboardingMode === "review-run" &&
    Boolean(executionId) &&
    Boolean(selectedNodeExecutionId);

  useEffect(() => {
    if (
      onboardingMode !== "review-run" ||
      !executionId ||
      !selectedNodeExecutionId
    ) {
      return;
    }
    recordActivationEvent?.({
      ...buildAgentTraceReviewedPayload({
        agentId: graphId,
        executionId,
        nodeExecutionId: selectedNodeExecutionId,
        quickStartAttribution,
      }),
    });
  }, [
    executionId,
    graphId,
    onboardingMode,
    quickStartAttribution,
    recordActivationEvent,
    selectedNodeExecutionId,
  ]);

  const handleGraphNodeClick = useCallback((_event, node) => {
    if (node.id === START_ID || node.id === END_ID) return;
    setSelectedNodeId(node.id);
  }, []);

  const handleAddEvalCoverage = useCallback(() => {
    navigate(
      buildAgentEvalBuilderHref({
        agentId: graphId,
        quickStartAttribution,
        versionId,
      }),
    );
  }, [graphId, navigate, quickStartAttribution, versionId]);

  if (!executionId) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          Select an execution to view details
        </Typography>
      </Box>
    );
  }

  if (isLoading) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <CircularProgress size={28} />
        <Typography typography="s2" color="text.secondary">
          Loading execution details...
        </Typography>
      </Box>
    );
  }

  const isPending =
    executionData?.status?.toLowerCase() === "pending" &&
    !selectedNodeExecutionId;

  if (isError) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="error.main">
          Failed to load execution details
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ px: 2, pt: showCoverageHandoff ? 2 : 0 }}>
        <AgentOnboardingFocusPanel
          currentStep="Coverage"
          description="Use the reviewed run to add an eval node, so this behavior is checked every time the agent changes."
          hidden={!showCoverageHandoff}
          primaryAction={{
            label: "Add eval node",
            onClick: handleAddEvalCoverage,
          }}
          secondaryAction={{
            label: "Review another node",
            onClick: () => setSelectedNodeId(null),
          }}
          singleActionFocus={showCoverageHandoff}
          steps={[
            { label: "Agent", complete: true },
            { label: "Scenario", complete: true },
            { label: "Review", complete: true },
            { label: "Coverage", complete: false },
          ]}
          title="Turn this run into agent coverage"
          tourAnchor="agent_eval_handoff_button"
        />
      </Box>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ResizablePanels
          initialLeftWidth={50}
          minLeftWidth={15}
          maxLeftWidth={80}
          leftPanel={
            <AgentGraph
              executionData={executionData}
              onNodeClick={handleGraphNodeClick}
              selectedNodeId={selectedNodeId}
            />
          }
          rightPanel={
            isPending ? (
              <Box
                sx={{
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexDirection: "column",
                  gap: 1,
                  height: "100%",
                }}
              >
                <CircularProgress size={24} />
                <Typography typography="s2" color="text.secondary">
                  Execution is waiting to start
                </Typography>
              </Box>
            ) : (
              <NodeOutputDetail
                executionId={resolvedExecutionId}
                nodeExecutionId={selectedNodeExecutionId}
              />
            )
          }
        />
      </Box>
    </Box>
  );
}

ExecutionDetailView.propTypes = {
  graphId: PropTypes.string.isRequired,
  executionId: PropTypes.string,
};
