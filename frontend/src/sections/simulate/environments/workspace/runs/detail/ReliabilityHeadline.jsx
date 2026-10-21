import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { useRunAnalytics } from "src/api/simulate-environments/runAnalytics";

// Below this many scenarios the backend withholds the range; rates read as more
// certain than they are, so the header falls back to raw counts.
const MIN_INTERVAL_SCENARIOS = 10;

const percent = (value) => `${Math.round(value)}%`;

function Stat({ label, value, help }) {
  return (
    <Tooltip arrow title={help}>
      <Box sx={{ minWidth: 0 }}>
        <Typography
          noWrap
          sx={{ fontSize: 10, color: "text.secondary", lineHeight: 1.2 }}
        >
          {label}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </Typography>
      </Box>
    </Tooltip>
  );
}
Stat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  help: PropTypes.string.isRequired,
};

/** pass^1, pass^k and the pass^1 95% range for a finished run. */
export default function ReliabilityHeadline({ executionId, enabled }) {
  const { data } = useRunAnalytics(executionId, { enabled });
  const reliability = data?.reliability;
  if (!enabled || reliability?.pass_hat_1 == null) return null;

  const { k, pass_hat_1: passHat1, pass_hat_k: passHatK } = reliability;
  const rows = reliability.rows ?? [];
  const conclusive = rows.filter((row) => row.evaluated);
  const interval = reliability.pass_rate_interval;
  const countsOnly = conclusive.length < MIN_INTERVAL_SCENARIOS;
  const trialsPassed = conclusive.reduce((sum, row) => sum + row.passed, 0);
  const trialsEvaluated = conclusive.reduce(
    (sum, row) => sum + row.evaluated,
    0,
  );
  const allPassed = rows.filter(
    (row) => row.evaluated === k && row.passed === k,
  ).length;

  return (
    <Stack
      direction="row"
      spacing={2.5}
      alignItems="center"
      sx={{ flexShrink: 0, minWidth: 0 }}
    >
      <Stat
        label="pass^1"
        value={
          countsOnly
            ? `${trialsPassed} / ${trialsEvaluated} trials`
            : percent(passHat1)
        }
        help="Average per-trial pass rate: each scenario's passed trials over its conclusive trials, averaged across scenarios."
      />
      {passHatK != null && (
        <Stat
          label={`pass^${k}`}
          value={
            countsOnly
              ? `${allPassed} / ${reliability.pass_hat_k_scenarios} scenarios`
              : percent(passHatK)
          }
          help={`Share of scenarios that passed all ${k} trials. A scenario with an inconclusive trial is left out.`}
        />
      )}
      {interval ? (
        <Stat
          label="95% range"
          value={`${percent(interval.low)}–${percent(interval.high)}`}
          help={`Range for pass^1 from resampling the ${interval.scenarios} scenarios with their trials kept together, so repeated trials never count as independent samples.`}
        />
      ) : (
        countsOnly && (
          <Stat
            label="95% range"
            value="-"
            help={`Needs ${MIN_INTERVAL_SCENARIOS} or more scenarios with a conclusive trial; this run has ${conclusive.length}.`}
          />
        )
      )}
      {reliability.inconclusive > 0 && (
        <Stat
          label="Inconclusive"
          value={`${reliability.inconclusive} trial${reliability.inconclusive === 1 ? "" : "s"}`}
          help="Infra, simulator or missing-evidence results. They say nothing about the agent, so they are left out of every rate."
        />
      )}
    </Stack>
  );
}
ReliabilityHeadline.propTypes = {
  executionId: PropTypes.string,
  enabled: PropTypes.bool,
};
