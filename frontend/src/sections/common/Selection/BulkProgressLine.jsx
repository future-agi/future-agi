// W5-7 (C3): one line for a long bulk action, e.g.
// `Exporting 1,200 of 4,800 rows…` with a thin bar under it. The host
// passes the action's own counts (paged reads, a child run's progress).
import React from "react";
import PropTypes from "prop-types";
import { LinearProgress, Stack, Typography } from "@mui/material";
import { progressPercent, progressText } from "./bulkProgress";

const BulkProgressLine = ({ doing, done, count, total, noun }) => {
  const pct = progressPercent(count, total);
  return (
    <Stack gap={0.5} sx={{ minWidth: 0 }} data-testid="bulk-progress">
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        role="status"
        aria-live="polite"
      >
        {progressText({ doing, done, count, total, noun })}
      </Typography>
      <LinearProgress
        variant={pct == null ? "indeterminate" : "determinate"}
        value={pct ?? undefined}
        sx={{ height: 3, borderRadius: 2 }}
      />
    </Stack>
  );
};

BulkProgressLine.propTypes = {
  doing: PropTypes.string.isRequired,
  done: PropTypes.string.isRequired,
  count: PropTypes.number.isRequired,
  total: PropTypes.number,
  noun: PropTypes.string,
};

export default BulkProgressLine;
