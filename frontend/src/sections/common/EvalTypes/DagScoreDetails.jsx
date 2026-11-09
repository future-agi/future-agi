// W7 A2 (W7-3): a decision-tree cell's path. Compact (the grid tooltip, S8):
// `Path: 1 Yes → 2 No → Wrong amount (0.00)`. Full (the results drawer, S7):
// each visited node with its answer, p, reason, judge, cost and time, and a
// link to all paths on the run.
import React from "react";
import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { endpoints } from "src/utils/axios";
import DagPathSteps from "./DagPathSteps";
import { dagCompactLine } from "./dagModel";

export default function DagScoreDetails({ cell, compact = false }) {
  const t = cell?.typed;
  if (t?.kind !== "dag") return null;
  if (compact)
    return (
      <Box data-testid="dag-compact">
        <Typography typography="s3">{dagCompactLine(t)}</Typography>
      </Box>
    );
  return (
    <Box
      data-testid="dag-details"
      sx={{
        mb: 1.25,
        p: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.75 }}>
        <Typography typography="s2" fontWeight={600}>
          Decision path
        </Typography>
        <Box sx={{ flex: 1 }} />
        {t.template_id && endpoints.develop?.w7?.dagPaths ? (
          <Link
            typography="s3"
            component={RouterLink}
            to={`/dashboard/evaluations/${t.template_id}?tab=details${t.run_id ? `&run=${t.run_id}` : ""}&view=paths`}
          >
            {t.run_label ? `All paths on ${t.run_label}` : "All paths"}
          </Link>
        ) : null}
      </Stack>
      <DagPathSteps
        path={t.path}
        leaf={t.leaf}
        skipLabel={cell.skip_label ?? null}
      />
    </Box>
  );
}

DagScoreDetails.ready = true;

DagScoreDetails.propTypes = {
  cell: PropTypes.object,
  compact: PropTypes.bool,
};
