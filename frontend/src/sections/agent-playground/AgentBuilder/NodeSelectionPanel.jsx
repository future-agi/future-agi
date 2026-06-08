import { Box, Skeleton, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { useReactFlow } from "@xyflow/react";
import { useSearchParams } from "react-router-dom";
import { AGENT_NODE } from "../utils/constants";
import {
  useGetNodeTemplates,
  useGetReferenceableGraphs,
} from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";
import NodeCard from "../components/NodeCard";
import AgentOnboardingFocusPanel from "../components/AgentOnboardingFocusPanel";
import useAddNodeOptimistic from "./hooks/useAddNodeOptimistic";
import {
  agentSetupQuickStartAttributionFromSearch,
  buildAgentOnboardingStarterPromptConfig,
  buildAgentNodeAddedPayload,
  buildAgentScenarioSavedAsEvalPayload,
} from "../agentOnboardingEvents";
import { recordActivationEvent } from "src/sections/onboarding-home/api/onboarding-home-api";

const NodeCardSkeleton = () => (
  <Box sx={{ borderRadius: 1, padding: 0.5, width: "220px" }}>
    <Stack direction="row" spacing={1.5} alignItems="flex-start">
      <Skeleton
        variant="rounded"
        width={36}
        height={36}
        sx={{ flexShrink: 0 }}
      />
      <Stack sx={{ flex: 1 }} gap={0.5}>
        <Skeleton variant="text" width="60%" height={18} />
        <Skeleton variant="text" width="90%" height={16} />
      </Stack>
    </Stack>
  </Box>
);

export default function NodeSelectionPanel({
  width,
  disabled = false,
  onboardingMode,
  tourAnchor,
}) {
  const { addNode } = useAddNodeOptimistic();
  const { setCenter, getZoom } = useReactFlow();
  const [searchParams, setSearchParams] = useSearchParams();

  const { currentAgent, nodes } = useAgentPlaygroundStoreShallow((state) => ({
    currentAgent: state.currentAgent,
    nodes: state.nodes,
  }));
  const { data: referenceableGraphs = [] } = useGetReferenceableGraphs(
    currentAgent?.id,
  );

  const { data: templateNodes = [], isLoading } = useGetNodeTemplates();
  const nodesList = useMemo(
    () =>
      referenceableGraphs.length > 0
        ? [...templateNodes, AGENT_NODE]
        : [...templateNodes],
    [templateNodes, referenceableGraphs],
  );

  const handleNodeClick = useCallback(
    async (node, options = {}) => {
      if (disabled) return;
      const result = await addNode({
        type: node.id,
        position: undefined,
        node_template_id: node.node_template_id,
        ...(options.config && { config: options.config }),
        ...(options.waitForApi && { waitForApi: options.waitForApi }),
      });
      if (result?.position) {
        setCenter(result.position.x + 300, result.position.y, {
          duration: 800,
          zoom: getZoom(),
        });
      }
      return result;
    },
    [addNode, disabled, setCenter, getZoom],
  );

  const isAddEvalMode = onboardingMode === "add-eval";
  const isRunScenarioMode = onboardingMode === "run-scenario";
  const hasEvalNode = useMemo(
    () =>
      nodes.some(
        (node) =>
          node?.type === "eval" ||
          node?.data?.type === "eval" ||
          node?.node_type === "eval",
      ),
    [nodes],
  );
  const llmPromptNode = useMemo(
    () => nodesList.find((node) => node.id === "llm_prompt"),
    [nodesList],
  );
  const evalNode = useMemo(
    () => nodesList.find((node) => node.id === "eval"),
    [nodesList],
  );
  const handleAddPromptNode = useCallback(async () => {
    if (!llmPromptNode) return;
    const result = await handleNodeClick(llmPromptNode, {
      config: buildAgentOnboardingStarterPromptConfig(),
      waitForApi: true,
    });
    if (!result) return;
    const eventPayload = buildAgentNodeAddedPayload({
      agentId: currentAgent?.id,
      nodeId: result.nodeId,
      quickStartAttribution:
        agentSetupQuickStartAttributionFromSearch(searchParams),
      versionId: currentAgent?.version_id,
    });
    try {
      await recordActivationEvent(eventPayload);
    } catch {
      // Activation tracking should not block the builder action.
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("journey_step", "run_agent_scenario");
        next.delete("tour_anchor");
        return next;
      },
      { replace: true },
    );
  }, [
    currentAgent?.id,
    currentAgent?.version_id,
    handleNodeClick,
    llmPromptNode,
    searchParams,
    setSearchParams,
  ]);
  const handleAddEvalNode = useCallback(async () => {
    if (!evalNode) return;
    const result = await handleNodeClick(evalNode, { waitForApi: true });
    if (!result) return;
    const eventPayload = buildAgentScenarioSavedAsEvalPayload({
      agentId: currentAgent?.id,
      nodeId: result.nodeId,
      quickStartAttribution:
        agentSetupQuickStartAttributionFromSearch(searchParams),
      versionId: currentAgent?.version_id,
    });
    try {
      await recordActivationEvent(eventPayload);
    } catch {
      // Activation tracking should not block adding coverage.
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("journey_step", "agent_create_eval");
        next.set("tour_anchor", "agent_create_eval_button");
        return next;
      },
      { replace: true },
    );
  }, [
    currentAgent?.id,
    currentAgent?.version_id,
    evalNode,
    handleNodeClick,
    searchParams,
    setSearchParams,
  ]);

  const handleDragStart = useCallback(
    (event, node) => {
      if (disabled) {
        event.preventDefault();
        return;
      }
      event.dataTransfer.setData("application/reactflow", node.id);
      if (node.node_template_id) {
        event.dataTransfer.setData(
          "application/node-template-id",
          node.node_template_id,
        );
      }
      event.dataTransfer.effectAllowed = "move";
    },
    [disabled],
  );

  return (
    <Box
      sx={{
        width,
        height: "100%",
        backgroundColor: "background.paper",
        borderRight: "1px solid",
        borderColor: "divider",
        position: "absolute",
        left: 0,
        top: 0,
        bottom: 0,
        p: 2,
        overflowY: "auto",
        overflowX: "hidden",
        ...(disabled && {
          opacity: 0.5,
          pointerEvents: "none",
        }),
      }}
    >
      <AgentOnboardingFocusPanel
        currentStep="Prompt"
        description="We will add a runnable prompt with a model and sample input. You can edit it after the first run."
        hidden={!isRunScenarioMode || nodes.length > 0}
        blocker={
          disabled
            ? "Builder busy"
            : isLoading
              ? "Loading steps"
              : !llmPromptNode
                ? "Prompt step unavailable"
                : null
        }
        primaryAction={{
          label: "Add starter prompt",
          onClick: handleAddPromptNode,
          disabled: disabled || isLoading || !llmPromptNode,
          tourAnchor,
        }}
        steps={[
          { label: "Create", complete: true },
          { label: "Prompt", complete: false },
          { label: "Run", complete: false },
          { label: "Review", complete: false },
        ]}
        title="Add a starter prompt"
        sx={{ mb: 1.5 }}
      />
      <AgentOnboardingFocusPanel
        currentStep="Coverage"
        description="Add an eval node for the reviewed behavior, then save and rerun the workflow to prove the agent stays reliable."
        hidden={!isAddEvalMode || hasEvalNode}
        blocker={
          disabled ? "Builder busy" : !evalNode ? "Eval unavailable" : null
        }
        primaryAction={{
          label: "Add eval node",
          onClick: handleAddEvalNode,
          disabled: disabled || !evalNode,
          tourAnchor: tourAnchor || "agent_save_eval_button",
        }}
        steps={[
          { label: "Agent", complete: true },
          { label: "Scenario", complete: true },
          { label: "Review", complete: true },
          { label: "Coverage", complete: false },
        ]}
        title="Add coverage from the reviewed run"
        sx={{ mb: 1.5 }}
      />
      <Stack spacing={1}>
        {isLoading ? (
          <>
            <NodeCardSkeleton />
            <NodeCardSkeleton />
          </>
        ) : (
          nodesList.map((node) => (
            <Box
              key={node.id}
              data-tour-anchor={
                isAddEvalMode && node.id === "eval"
                  ? "agent_eval_node_card"
                  : undefined
              }
              onClick={() => {
                if (
                  isRunScenarioMode &&
                  nodes.length === 0 &&
                  node.id === "llm_prompt"
                ) {
                  handleAddPromptNode();
                  return;
                }
                if (isAddEvalMode && node.id === "eval") {
                  handleAddEvalNode();
                  return;
                }
                handleNodeClick(node);
              }}
              onDragStart={(e) => handleDragStart(e, node)}
              draggable={!disabled}
              sx={{
                borderRadius: 0.5,
                overflow: "hidden",
                cursor: disabled ? "not-allowed" : "pointer",
                "&:hover": {
                  backgroundColor: "action.hover",
                },
              }}
            >
              <NodeCard node={node} showExpandIcon={false} />
            </Box>
          ))
        )}
      </Stack>
    </Box>
  );
}

NodeSelectionPanel.propTypes = {
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  disabled: PropTypes.bool,
  onboardingMode: PropTypes.string,
  tourAnchor: PropTypes.string,
};
