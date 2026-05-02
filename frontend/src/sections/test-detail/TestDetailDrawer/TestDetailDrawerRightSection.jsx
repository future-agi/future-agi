import React, { useMemo, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import useKpis from "src/hooks/useKpis";
import GraphView from "src/components/GraphBuilder/GraphView";
import { dagreTransformAndLayout } from "src/components/GraphBuilder/common";
import { ReactFlowProvider } from "@xyflow/react";
import TestDetailCallAnalytics from "./TestDetailCallAnalytics";
import CustomAgentTabs, {
  CONTAINER_SMALL,
  CONTAINER_XS,
} from "src/sections/agents/CustomAgentTabs";
import TestDetailEvaluationGrid from "./TestDetailEvaluationGrid";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import {
  getCompareBaselineTooltipTitle,
  getLoadingStateWithRespectiveStatus,
  TestRunExecutionStatus,
} from "../common";
import { TEST_DETAIL_RIGHT_TABS } from "src/components/CallLogsDetailDrawer/utils";
import SvgColor from "src/components/svg-color";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { AGENT_TYPES } from "src/sections/agents/constants";
import CustomTooltip from "../../../components/tooltip/CustomTooltip";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";

const TestDetailDrawerRightSection = ({
  scenarioId,
  openedExecutionId,
  latencies,
  analysisSummary,
  costBreakdown,
  evalOutputs,
  callStatus,
  status,
  setCompareReplay,
  simulationCallType,
  sessionId,
  provider,
  hideAnnotationTab=false,
}) => {
  const [currentRightTab, setCurrentRightTab] = useState(
    simulationCallType === AGENT_TYPES.CHAT ? "evaluations" : "callAnalytics",
  );
  const { executionId } = useParams();
  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(status, simulationCallType);

  const { data: kpis, isLoading: isKpisLoading } = useKpis(executionId, {
    enabled: false,
  });

  const {
    data: flowAnalysis,
    isLoading: isFlowAnalysisLoading,
    isError: isFlowAnalysisError,
  } = useQuery({
    queryKey: ["flow-analysis", openedExecutionId],
    queryFn: () =>
      axios.get(endpoints.testExecutions.flowAnalysis(openedExecutionId)),
    enabled: currentRightTab === "flowAnalysis" && Boolean(openedExecutionId),
    select: (data) => data.data,
  });

  const isLoading = isKpisLoading || isFlowAnalysisLoading;

  const scenarioGraphs = kpis?.scenarioGraphs;

  const scenarioGraph = scenarioGraphs?.[scenarioId];

  const tabs = useMemo(() => {
    const t = [
      ...(simulationCallType === AGENT_TYPES.VOICE
        ? [
            {
              label: "Call Analytics",
              value: TEST_DETAIL_RIGHT_TABS.CALL_ANALYTICS,
            },
          ]
        : []),
      {
        label: "Evaluations",
        value: TEST_DETAIL_RIGHT_TABS.EVALUATIONS,
      },
    ];

    if (scenarioGraph?.nodes) {
      t.push({
        label: "Flow Analysis",
        value: TEST_DETAIL_RIGHT_TABS.FLOW_ANALYSIS,
      });
    }
    if (!hideAnnotationTab) {
      t.push({
        label: "Annotations",
        value: TEST_DETAIL_RIGHT_TABS.ANNOTATIONS,
      });
    }

    return t;
  }, [scenarioGraph, hideAnnotationTab, simulationCallType]);

  const { nodes, edges } = useMemo(() => {
    const currentPath = flowAnalysis?.analysis?.currentPath;
    const expectedPath = flowAnalysis?.analysis?.expectedPath;
    const newNodes = flowAnalysis?.analysis?.newNodes;
    const newEdges = flowAnalysis?.analysis?.newEdges;

    if (
      !scenarioGraph?.nodes ||
      !Array.isArray(currentPath) ||
      !Array.isArray(expectedPath) ||
      !currentPath.length ||
      !expectedPath.length
    ) {
      return { nodes: [], edges: [] };
    }

    const greenNodes = [];
    const redNodes = [];

    for (
      let idx = 0;
      idx < Math.max(currentPath.length, expectedPath.length);
      idx++
    ) {
      const currentPathNode = currentPath?.[idx];
      const expectedPathNode = expectedPath?.[idx];

      if (currentPathNode === expectedPathNode) {
        greenNodes.push(currentPathNode);
      } else {
        if (currentPathNode) {
          redNodes.push(currentPathNode);
        }
        if (expectedPathNode) {
          greenNodes.push(expectedPathNode);
        }
      }
    }

    const finalNodes = [...scenarioGraph.nodes, ...newNodes].filter(
      (node) =>
        currentPath.includes(node.name) || expectedPath.includes(node.name),
    );
    const finalEdges = [...scenarioGraph.edges, ...newEdges].filter(
      (edge) =>
        (currentPath.includes(edge.from) && currentPath.includes(edge.to)) ||
        (expectedPath.includes(edge.from) && expectedPath.includes(edge.to)),
    );

    const extraNodeDataGenerator = (node) => {
      if (greenNodes.includes(node.name)) {
        return {
          highlightColor: "success",
        };
      } else if (redNodes.includes(node.name)) {
        return {
          highlightColor: "error",
        };
      }
    };

    const extraEdgeDataGenerator = (edge) => {
      if (greenNodes.includes(edge.to)) {
        return {
          highlightColor: "success",
        };
      } else if (redNodes.includes(edge.to)) {
        return {
          highlightColor: "error",
        };
      }
    };

    const { nodes: transformedNodes, edges: transformedEdges } =
      dagreTransformAndLayout(
        finalNodes,
        finalEdges,
        extraNodeDataGenerator,
        extraEdgeDataGenerator,
      );

    return { nodes: transformedNodes, edges: transformedEdges };
  }, [scenarioGraph, flowAnalysis]);

  return (
    <Box
      sx={{
        width: "100%",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minHeight: 334,
        containerType: "inline-size",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        gap={1}
        sx={{
          [CONTAINER_SMALL]: {
            flexDirection: "column",
            alignItems: "stretch",
            "& .compare-btn-wrapper": { display: "block", width: "100%" },
          },
          [CONTAINER_XS]: {
            flexDirection: "column",
            alignItems: "flex-start",
            "& .compare-btn-wrapper": { display: "block", width: "100%" },
          },
        }}
      >
        <CustomAgentTabs
          value={currentRightTab}
          onChange={(_, value) => setCurrentRightTab(value)}
          tabs={tabs}
          containerResponsive
        />
        <ShowComponent
          condition={
            (simulationCallType === AGENT_TYPES.CHAT ||
              simulationCallType === AGENT_TYPES.VOICE) &&
            sessionId
          }
        >
          <CustomTooltip
            type={"black"}
            show={status !== TestRunExecutionStatus.COMPLETED}
            title={getCompareBaselineTooltipTitle(status)}
            arrow
            placement="bottom"
            size="small"
          >
            <span className="compare-btn-wrapper">
              <Button
                variant="outlined"
                color="primary"
                size="small"
                disabled={status !== TestRunExecutionStatus.COMPLETED}
                sx={{
                  whiteSpace: "nowrap",
                  width: "auto",
                  [CONTAINER_SMALL]: {
                    width: "100%",
                  },
                  [CONTAINER_XS]: {
                    width: "100%",
                  },
                  "& startIcon": {
                    color: "primary.main",
                  },
                }}
                startIcon={
                  <SvgColor
                    sx={{
                      height: "20px",
                      width: "20px",
                    }}
                    src="/assets/icons/ic_compare.svg"
                  />
                }
                onClick={() => setCompareReplay(true)}
              >
                {simulationCallType === AGENT_TYPES.VOICE
                  ? "Compare with baseline call"
                  : "Compare with baseline chat"}
              </Button>
            </span>
          </CustomTooltip>
        </ShowComponent>
      </Stack>
      <ShowComponent condition={isCallInProgress}>
        <LoadingStateComponent message={loadingMessage} />
      </ShowComponent>
      <ShowComponent condition={!isCallInProgress}>
        <Box sx={{ width: "100%", height: "100%" }}>
          <ShowComponent
            condition={
              currentRightTab === TEST_DETAIL_RIGHT_TABS.CALL_ANALYTICS
            }
          >
            <TestDetailCallAnalytics
              latencies={latencies}
              analysisSummary={analysisSummary}
              costBreakdown={costBreakdown}
              provider={provider}
            />
          </ShowComponent>
          <ShowComponent
            condition={currentRightTab === TEST_DETAIL_RIGHT_TABS.EVALUATIONS}
          >
            <TestDetailEvaluationGrid
              evalOutputs={evalOutputs}
              callStatus={callStatus}
            />
          </ShowComponent>
          <ShowComponent
            condition={currentRightTab === TEST_DETAIL_RIGHT_TABS.FLOW_ANALYSIS}
          >
            <ShowComponent condition={isLoading}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 1,
                  height: "100%",
                  width: "100%",
                }}
              >
                <CircularProgress size={20} />
                <Typography typography="s1">
                  We are analyzing your flow...
                </Typography>
              </Box>
            </ShowComponent>
            <ShowComponent condition={isFlowAnalysisError}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 1,
                  height: "100%",
                  width: "100%",
                }}
              >
                <Typography typography="s1">
                  There was an error analyzing your flow. This call does not
                  have enough data to analyze flow.
                </Typography>
              </Box>
            </ShowComponent>
            <ShowComponent condition={!isLoading && !isFlowAnalysisError}>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  paddingBottom: 2,
                }}
              >
                <ShowComponent
                  condition={flowAnalysis?.analysis?.analysisSummary}
                >
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      padding: 2,
                    }}
                  >
                    <Typography typography="s1" fontWeight="fontWeightSemiBold">
                      Analysis Summary
                    </Typography>
                    <Typography typography="s1">
                      {flowAnalysis?.analysis?.analysisSummary}
                    </Typography>
                  </Box>
                </ShowComponent>
                <Box
                  sx={{
                    width: "100%",
                    height: "500px",
                    position: "relative",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    overflow: "hidden",
                  }}
                >
                  <ReactFlowProvider>
                    <GraphView nodes={nodes} edges={edges} />
                  </ReactFlowProvider>
                </Box>
              </Box>
            </ShowComponent>
          </ShowComponent>
          <ShowComponent
            condition={currentRightTab === TEST_DETAIL_RIGHT_TABS.ANNOTATIONS}
          >
            <ScoresListSection
              sourceType="call_execution"
              sourceId={openedExecutionId}
            />
          </ShowComponent>
        </Box>
      </ShowComponent>
    </Box>
  );
};

TestDetailDrawerRightSection.propTypes = {
  scenarioId: PropTypes.string,
  openedExecutionId: PropTypes.string,
  latencies: PropTypes.object,
  analysisSummary: PropTypes.string,
  costBreakdown: PropTypes.object,
  evalOutputs: PropTypes.object,
  callStatus: PropTypes.string,
  status: PropTypes.string,
  setCompareReplay: PropTypes.func,
  simulationCallType: PropTypes.string,
  sessionId: PropTypes.string,
  provider: PropTypes.string,
  hideAnnotationTab: PropTypes.bool,
};

export default TestDetailDrawerRightSection;
