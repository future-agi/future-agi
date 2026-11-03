import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import TraceTreeV2 from "src/components/traceDetail/TraceTreeV2";

export default function WidgetSpanTree({ config, traceData }) {
  const spans = traceData?.spans;

  if (!spans?.length) {
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
          No span data available
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height: "100%", overflow: "auto" }}>
      <TraceTreeV2
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={() => {}}
        showMetrics={config.showMetrics ?? true}
        visibleMetrics={
          config.visibleMetrics || { latency: true, tokens: true, cost: false }
        }
      />
    </Box>
  );
}

WidgetSpanTree.propTypes = {
  config: PropTypes.object.isRequired,
  traceData: PropTypes.object,
};
