import React from "react";
import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import ErrorState from "src/sections/common/ux/ErrorState";
import {
  MIXED_RUNS_WORDS,
  experimentDataHref,
} from "src/utils/mixedRunsRefusal";

/**
 * The results page when its runs cannot be lined up (the read's mixed-runs
 * refusal): the page's usual error state, titled in plain words, with its
 * Retry (R), and the first run's experiment one click away when the page
 * knows it. The server's raw sentence is not shown.
 */
export default function MixedRunsNotice({
  datasetId,
  experimentId,
  experimentName,
  runs = [],
  onRemoveRun,
  onRetry,
  sx,
}) {
  return (
    <Stack data-testid="mixed-runs-notice" alignItems="center" sx={sx}>
      <ErrorState compact title={MIXED_RUNS_WORDS} onRetry={onRetry} />
      {onRemoveRun && (
        <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mb: 2 }}>
          {runs.map((run) => (
            <Button
              key={run.run_id}
              size="small"
              variant="outlined"
              aria-label={`Remove ${run.label ?? run.run_id} from comparison`}
              onClick={() => onRemoveRun(run.run_id)}
            >
              Remove {run.label ?? run.run_id}
            </Button>
          ))}
        </Stack>
      )}
      {experimentId ? (
        <Button
          size="small"
          variant="outlined"
          component={RouterLink}
          to={experimentDataHref(datasetId, experimentId)}
          data-testid="mixed-runs-open-experiment"
          sx={{ mb: 3 }}
        >
          {experimentName
            ? `Open ${experimentName}`
            : "Open the first run's experiment"}
        </Button>
      ) : null}
    </Stack>
  );
}

MixedRunsNotice.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  experimentName: PropTypes.string,
  runs: PropTypes.array,
  onRemoveRun: PropTypes.func,
  onRetry: PropTypes.func,
  sx: PropTypes.object,
};
