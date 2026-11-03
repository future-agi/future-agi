import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import SpanTreeTimeline from "src/components/traceDetail/SpanTreeTimeline";

export default function WidgetTimeline({ config: _config, traceData }) {
  // Use trace data passed from the drawer context
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
      <SpanTreeTimeline
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={() => {}}
        showMetrics
        visibleMetrics={{ latency: true, tokens: true, cost: false }}
      />
    </Box>
  );
}

WidgetTimeline.propTypes = {
  config: PropTypes.object.isRequired,
  traceData: PropTypes.object,
};
