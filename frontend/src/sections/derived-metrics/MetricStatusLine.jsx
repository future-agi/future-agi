import PropTypes from "prop-types";
import {
  Alert,
  Button,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { formatMetricTime, formatRelativeMetricTime } from "./metricTime";
import { forwardCoverageCopy } from "./copy";

import { metricReadMessage } from "./metricPageValues";

export function MetricReadNotice({ query, label }) {
  if (["loading", "idle"].includes(query.readState)) {
    return (
      <Typography role="status" variant="body2" color="text.secondary">
        Loading {label.toLowerCase()}…
      </Typography>
    );
  }
  if (query.readState === "ready") return null;
  return (
    <Alert
      severity="warning"
      action={
        query.error?.retryable !== false && query.refetch ? (
          <Button color="inherit" onClick={() => query.refetch()}>
            Retry {label.toLowerCase()}
          </Button>
        ) : undefined
      }
    >
      {metricReadMessage(query.error, label)}
    </Alert>
  );
}
MetricReadNotice.propTypes = {
  query: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
};

export default function MetricStatusLine({
  provenance,
  definition,
  showFreshness = true,
}) {
  const done = provenance?.backfill_days_done ?? definition?.backfill_days_done;
  const total =
    provenance?.backfill_days_total ?? definition?.backfill_days_total;
  const hasProgress =
    Number.isSafeInteger(done) &&
    Number.isSafeInteger(total) &&
    total > 0 &&
    done >= 0 &&
    done <= total;
  const progressing = hasProgress && done < total;
  const failed = definition?.backfill_status === "failed";
  // Started with no history: say where values begin instead of a progress bar.
  const historyMode = provenance?.history_mode ?? definition?.history_mode;
  const coverageStart =
    provenance?.coverage_start ?? definition?.coverage_start;
  const forward = historyMode === "forward" && Boolean(coverageStart);
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {forward && (
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="metric-coverage"
        >
          {forwardCoverageCopy(coverageStart)}
        </Typography>
      )}
      {failed && (
        <Alert severity="warning">
          History recalculation failed. Previously committed values have not
          been replaced.
        </Alert>
      )}
      {progressing && (
        <Stack spacing={1} sx={{ maxWidth: 400 }}>
          <Typography variant="body2" color="text.secondary">
            Recalculating history: {done} of {total} days
          </Typography>
          <LinearProgress
            aria-label="History recalculation"
            variant="determinate"
            value={(100 * done) / total}
            sx={{
              bgcolor: "action.hover",
              "& .MuiLinearProgress-bar": { bgcolor: "text.secondary" },
            }}
          />
        </Stack>
      )}
      {showFreshness &&
        (provenance?.updated_at ? (
          <Tooltip title={formatMetricTime(provenance.updated_at)}>
            <Typography
              variant="caption"
              color="text.secondary"
              tabIndex={0}
              sx={{ width: "fit-content" }}
            >
              Updated {formatRelativeMetricTime(provenance.updated_at)} · v
              {provenance.version} · generation {provenance.generation}
            </Typography>
          </Tooltip>
        ) : (
          <Typography variant="caption" color="text.secondary">
            {provenance ? "Update time unavailable" : "Not calculated yet"}
          </Typography>
        ))}
    </Stack>
  );
}
MetricStatusLine.propTypes = {
  provenance: PropTypes.object,
  definition: PropTypes.object,
  showFreshness: PropTypes.bool,
};
