import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Link, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import MiniHistogram from "src/sections/common/Analysis/MiniHistogram";
import { datasetGridHref } from "src/sections/common/Analysis/analysisLinks";
import { EvalVersion } from "../EvalsOverviewTable";
import { failingText, num, pct, toMiniHistogram } from "../summaryApi";

const tabular = { fontVariantNumeric: "tabular-nums" };

/** "38 rows below 0.70 · View rows" under a chart (opens exactly those rows). */
export function FailingRowsLink({ row, datasetId, version }) {
  if (!row) return null;
  if (!row.failing)
    return (
      <Typography typography="s2" color="text.secondary">
        No failing rows
      </Typography>
    );
  return (
    <Stack direction="row" alignItems="center" gap={1}>
      <Typography typography="s2" sx={tabular}>
        {failingText(row)}
      </Typography>
      <Link
        component={RouterLink}
        to={datasetGridHref(datasetId, row.failingFilters, { version })}
        underline="hover"
        typography="s2"
        fontWeight={500}
      >
        View rows
      </Link>
    </Stack>
  );
}

FailingRowsLink.propTypes = {
  row: PropTypes.object,
  datasetId: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

/**
 * Score eval on the dataset: 10-bin histogram with the pass threshold as a
 * dashed line, then the rows on the failing side of it.
 */
const EvalHistogramCard = ({ row, datasetId, version }) => {
  const theme = useTheme();
  const threshold = row.passThreshold;
  return (
    <Stack gap={1.5}>
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Typography typography="s1" fontWeight={600}>
          {row.name}
        </Typography>
        <DirectionIcon higherIsBetter={row.higherIsBetter ?? true} />
        <EvalVersion version={row.version} latestVersion={row.latestVersion} />
        <Typography typography="s2" color="text.secondary" sx={tabular}>
          Mean {num(row.mean)} · Pass rate {pct(row.passRate)} · n=
          {row.scored ?? 0}
        </Typography>
      </Stack>
      <Typography typography="s3" color="text.secondary">
        {row.templateName} · rows per score bucket
      </Typography>
      <Box sx={{ px: 0.5 }}>
        <MiniHistogram
          histogram={toMiniHistogram(row.histogram, false)}
          runs={[{ run_id: "rows", label: row.name }]}
          colors={{ rows: { solid: theme.palette.primary.main } }}
          threshold={threshold ?? undefined}
          thresholdLabel={
            threshold != null
              ? `Pass ${row.higherIsBetter === false ? "≤" : "≥"} ${num(threshold)}`
              : undefined
          }
          formatX={(v) => Number(v).toFixed(1)}
          height={170}
        />
      </Box>
      <FailingRowsLink row={row} datasetId={datasetId} version={version} />
    </Stack>
  );
};

EvalHistogramCard.propTypes = {
  row: PropTypes.object.isRequired,
  datasetId: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default EvalHistogramCard;
