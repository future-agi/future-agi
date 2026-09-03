import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { ReactFlowProvider } from "@xyflow/react";
import axios, { endpoints } from "src/utils/axios";
import useKpis from "src/hooks/useKpis";
import GraphView from "src/components/GraphBuilder/GraphView";
import { dagreTransformAndLayout } from "src/components/GraphBuilder/common";
import { ShowComponent } from "src/components/show";
import { useParams } from "react-router";

/**
 * Path analyzer for voice simulations — compares expected scenario flow to
 * actual call path, highlighting matching nodes in success and divergences in
 * error. Extracted from TestDetailDrawerRightSection so it can live in the
 * left panel of the revamped voice drawer.
 */
const FlowAnalysisPanel = ({ scenarioId, openedExecutionId, enabled }) => {
  const { executionId } = useParams();

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
    enabled: !!enabled && !!openedExecutionId,
    select: (res) => res.data,
  });

  const scenarioGraphs = kpis?.scenario_graphs;
  const scenarioGraph = scenarioGraphs?.[scenarioId];

  const { nodes, edges } = useMemo(() => {
    const currentPath = flowAnalysis?.analysis?.current_path;
    const expectedPath = flowAnalysis?.analysis?.expected_path;
    const newNodes = flowAnalysis?.analysis?.new_nodes;
    const newEdges = flowAnalysis?.analysis?.new_edges;

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
        if (currentPathNode) redNodes.push(currentPathNode);
        if (expectedPathNode) greenNodes.push(expectedPathNode);
      }
    }

    const finalNodes = [...scenarioGraph.nodes, ...(newNodes || [])].filter(
      (node) =>
        currentPath.includes(node.name) || expectedPath.includes(node.name),
    );
    const finalEdges = [...scenarioGraph.edges, ...(newEdges || [])].filter(
      (edge) =>
        (currentPath.includes(edge.from) && currentPath.includes(edge.to)) ||
        (expectedPath.includes(edge.from) && expectedPath.includes(edge.to)),
    );

    const extraNodeDataGenerator = (node) => {
      if (greenNodes.includes(node.name)) return { highlightColor: "success" };
      if (redNodes.includes(node.name)) return { highlightColor: "error" };
      return undefined;
    };
    const extraEdgeDataGenerator = (edge) => {
      if (greenNodes.includes(edge.to)) return { highlightColor: "success" };
      if (redNodes.includes(edge.to)) return { highlightColor: "error" };
      return undefined;
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

  const isLoading = isKpisLoading || isFlowAnalysisLoading;

  return (
    <Stack gap={2} height="100%" minHeight={300}>
      <ShowComponent condition={isLoading}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 1,
            height: "100%",
            width: "100%",
            minHeight: 300,
          }}
        >
          <CircularProgress size={20} />
          <Typography typography="s1">Analyzing your flow...</Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!isLoading && isFlowAnalysisError}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            width: "100%",
            minHeight: 300,
            p: 2,
          }}
        >
          <Typography typography="s1" textAlign="center">
            There was an error analyzing your flow. This call does not have
            enough data to analyze flow.
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!isLoading && !isFlowAnalysisError}>
        <Stack gap={2} sx={{ width: "100%", paddingBottom: 2 }}>
          <ShowComponent condition={!!flowAnalysis?.analysis?.analysisSummary}>
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
              height: 500,
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
        </Stack>
      </ShowComponent>
    </Stack>
  );
};

FlowAnalysisPanel.propTypes = {
  scenarioId: PropTypes.string,
  openedExecutionId: PropTypes.string,
  enabled: PropTypes.bool,
};

export default FlowAnalysisPanel;
