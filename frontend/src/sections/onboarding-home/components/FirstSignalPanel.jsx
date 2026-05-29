import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { ObservePanelActions, ObservePanelHeader } from "./observe-panel-utils";

export default function FirstSignalPanel({
  action,
  fallbackAction,
  signals,
  stage,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
}) {
  const isImprovement = stage === "create_trace_evaluator";

  return (
    <Box
      data-testid="first-signal-panel"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        <ObservePanelHeader
          eyebrow={isImprovement ? "First improvement" : "First trace"}
          title={
            isImprovement
              ? "Turn the reviewed trace into a check"
              : "First trace received"
          }
          description={
            isImprovement
              ? "The first trace has been reviewed. Create a repeatable evaluator or quality check next."
              : "Review it now to understand latency, cost, and quality context."
          }
          chips={["observe", isImprovement ? "improve" : "review"]}
        />
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)" },
            gap: 1,
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">Trace</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {signals?.firstTraceId || "Waiting for trace id"}
            </Typography>
          </Box>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">Review status</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {signals?.traceReviews ? "Reviewed" : "Not reviewed"}
            </Typography>
          </Box>
        </Box>
        <ObservePanelActions
          action={action}
          fallbackAction={fallbackAction}
          onPrimaryClick={onPrimaryClick}
          onFallbackClick={onFallbackClick}
          onCheckAgain={onCheckAgain}
          isChecking={isChecking}
        />
      </Stack>
    </Box>
  );
}

FirstSignalPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  signals: PropTypes.object,
  stage: PropTypes.string,
};
