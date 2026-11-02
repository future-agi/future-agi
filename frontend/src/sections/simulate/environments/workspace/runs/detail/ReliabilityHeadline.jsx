import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";

import { useRunReliability } from "src/api/simulate-environments/runAnalytics";

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
  const { data: reliability } = useRunReliability(executionId, { enabled });
  if (!enabled || reliability?.pass_hat_1 == null) return null;

  const {
    k,
    pass_hat_1: passHat1,
    pass_hat_k: passHatK,
    pass_hat_k_scenarios: passHatKScenarios,
    pass_hat_k_passed: passHatKPassed,
    pass_hat_k_excluded: passHatKExcluded,
  } = reliability;
  const conclusive = (reliability.rows ?? []).filter((row) => row.evaluated);
  const interval = reliability.pass_rate_interval;
  const countsOnly = conclusive.length < MIN_INTERVAL_SCENARIOS;
  const scenarioNoun = (n) => `scenario${n === 1 ? "" : "s"}`;

  return (
    <Stack
      direction="row"
      spacing={2.5}
      alignItems="center"
      sx={{ flexShrink: 0, minWidth: 0 }}
    >
      {/* Too few scenarios for a rate: show the pooled trial count, labelled
          as such, rather than a scenario average it would not match. */}
      {countsOnly ? (
        <Stat
          label="Trials passed"
          value={`${reliability.trials_passed} / ${reliability.trials_evaluated}`}
          help={`Pooled across conclusive trials. pass^1 is shown as a rate from ${MIN_INTERVAL_SCENARIOS} scenarios.`}
        />
      ) : (
        <Stat
          label="pass^1"
          value={percent(passHat1)}
          help="Average per-trial pass rate: each scenario's passed trials over its conclusive trials, averaged across scenarios."
        />
      )}
      {/* pass^k covers fewer scenarios than pass^1 when some had an
          inconclusive trial, so it always names its own denominator. */}
      {passHatK != null && (
        <Stat
          label={`pass^${k}`}
          value={
            countsOnly
              ? `${passHatKPassed} / ${passHatKScenarios} ${scenarioNoun(passHatKScenarios)}`
              : `${percent(passHatK)} of ${passHatKScenarios} ${scenarioNoun(passHatKScenarios)}`
          }
          help={`Share of scenarios that passed all ${k} trials.${
            passHatKExcluded
              ? ` ${passHatKExcluded} ${scenarioNoun(passHatKExcluded)} with an inconclusive trial ${passHatKExcluded === 1 ? "is" : "are"} left out, so this can differ from pass^1's scenario set.`
              : ""
          }`}
        />
      )}
      {interval ? (
        <Stat
          label="95% range"
          value={`${percent(interval.low)}–${percent(interval.high)}`}
          help={`Range for pass^1 across ${interval.scenarios} scenarios. Trials of one scenario are counted together, never as independent samples.`}
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
