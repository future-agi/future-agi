import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";

export default function WidgetAgentGraph({ config, traceData }) {
  const spans = traceData?.spans;

  const graphData = useMemo(() => {
    if (!spans?.length) return null;
    return buildTraceGraph(spans);
  }, [spans]);

  if (!graphData?.nodes?.length) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography variant="body2" color="text.disabled" fontSize={12}>
          No agent graph data available
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height: "100%", overflow: "hidden" }}>
      <AgentGraph
        data={graphData}
        isLoading={false}
        onNodeClick={() => {}}
        direction={config.direction || "TB"}
      />
    </Box>
  );
}

WidgetAgentGraph.propTypes = {
  config: PropTypes.object.isRequired,
  traceData: PropTypes.object,
};
