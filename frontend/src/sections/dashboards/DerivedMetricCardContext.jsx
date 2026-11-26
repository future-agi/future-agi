import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SelectedRunSummary from "../derived-metrics/SelectedRunSummary";

/** Never borrow another series' context or a current editable definition. */
export default function DerivedMetricCardContext({
  context,
  runId,
  point,
  value,
}) {
  const run = context?.run;
  const result = context?.result;
  const matches =
    run?.id === runId &&
    Boolean(run?.result_id) &&
    point &&
    Number.isFinite(point.x) &&
    new Date(run.window_end).getTime() === point.x &&
    result &&
    result.group_key === "" &&
    result.value === value &&
    (!run.definition || run.definition.version === run.metric_version);
  if (!matches) {
    return (
      <Stack gap={0.5}>
        <Typography variant="body2" color="text.secondary">
          Saved formula and diagnostic context unavailable. This value is not
          explained by the current metric draft or widget description.
        </Typography>
        {value != null && point?.ci_reason && <Box component="details" sx={{ textAlign: "left" }}>
          <Box component="summary" sx={{ cursor: "pointer" }}>Details</Box>
          <Typography variant="caption" color="text.secondary">No 95% interval: {point.ci_reason.replaceAll("_", " ")}</Typography>
        </Box>}
      </Stack>
    );
  }
  return <SelectedRunSummary compact run={run} result={result} intervalReason={point?.ci_reason} />;
}
DerivedMetricCardContext.propTypes = {
  context: PropTypes.object,
  runId: PropTypes.string.isRequired,
  point: PropTypes.object,
  value: PropTypes.number,
};
