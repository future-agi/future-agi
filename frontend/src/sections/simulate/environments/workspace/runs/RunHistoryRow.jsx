import PropTypes from "prop-types";
import { Box, Stack, Typography, LinearProgress } from "@mui/material";
import { fDateTime } from "src/utils/format-time";
import StatusChip from "./StatusChip";
import PassBar from "./PassBar";
import { RUNS_COPY } from "./runs.constants";

// NOTE: deliberately minimal history row — label, timestamp, pass rate and a
// status chip. This is NOT a port of the designer's populated history (that is
// RunsSummary, a later phase); the designer's own RunsPanel rows never render
// (the component delegates to RunsSummary the moment a run exists). Rows come
// from the real executions API and open the reused product run detail, so a row
// is only clickable when it carries an executionId to route to.
export default function RunHistoryRow({ run, onOpenRun }) {
  const isRunning = run.status === "running";
  const timestamp = isRunning
    ? `${RUNS_COPY.started} ${fDateTime(run.startedAt)}`
    : fDateTime(run.finishedAt || run.startedAt);
  const passRate = run.total > 0 ? Math.round((run.passed / run.total) * 100) : 0;
  const clickable = !!run.executionId;

  const open = () => {
    if (clickable) onOpenRun(run);
  };

  const interactive = clickable
    ? {
        role: "button",
        tabIndex: 0,
        onClick: open,
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        },
        sx: {
          px: 2.5,
          py: 1.75,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        },
      }
    : { sx: { px: 2.5, py: 1.75, cursor: "default" } };

  return (
    <Stack direction="row" alignItems="center" spacing={2} {...interactive}>
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {run.label}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {timestamp} · {RUNS_COPY.tasks(run.total)}
          {run.agentVersion ? ` · agent ${run.agentVersion}` : ""}
        </Typography>
      </Box>
      <Box sx={{ width: 120, display: { xs: "none", sm: "block" } }}>
        {isRunning ? (
          <LinearProgress
            sx={{
              height: 4,
              borderRadius: 2,
              bgcolor: "action.hover",
              "& .MuiLinearProgress-bar": { bgcolor: "primary.main" },
            }}
          />
        ) : (
          <PassBar passed={run.passed} total={run.total} />
        )}
      </Box>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightBold",
          width: 54,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: isRunning ? "text.subtitle" : "text.primary",
        }}
      >
        {isRunning ? "—" : `${passRate}%`}
      </Typography>
      <StatusChip status={run.status} />
    </Stack>
  );
}

RunHistoryRow.propTypes = {
  run: PropTypes.shape({
    id: PropTypes.string,
    executionId: PropTypes.string,
    label: PropTypes.string,
    status: PropTypes.string,
    startedAt: PropTypes.string,
    finishedAt: PropTypes.string,
    total: PropTypes.number,
    passed: PropTypes.number,
    failed: PropTypes.number,
    agentVersion: PropTypes.string,
  }).isRequired,
  onOpenRun: PropTypes.func,
};
