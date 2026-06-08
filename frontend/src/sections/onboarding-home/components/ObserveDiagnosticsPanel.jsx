import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

export default function ObserveDiagnosticsPanel({ signals }) {
  if (!signals) return null;

  return (
    <Box
      data-testid="observe-diagnostics-panel"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
    >
      <Stack spacing={1}>
        <Typography variant="subtitle2">Observe signal</Typography>
        <Typography variant="body2" color="text.secondary">
          Projects: {signals.observeProjects || 0} · Traces:{" "}
          {signals.traces || 0} · Reviews: {signals.traceReviews || 0}
        </Typography>
      </Stack>
    </Box>
  );
}

ObserveDiagnosticsPanel.propTypes = {
  signals: PropTypes.object,
};
