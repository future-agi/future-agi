import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import SoonChip from "../../../components/SoonChip";
import { MIN_INTERVAL_SCENARIOS, countsOnly } from "./runHeaderFormat";

const percent = (value) => `${Math.round(value)}%`;
const scenarioNoun = (n) => `scenario${n === 1 ? "" : "s"}`;

function Stat({ label, value, help }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="baseline">
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {label}
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {help}
      </Typography>
    </Box>
  );
}
Stat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  help: PropTypes.string.isRequired,
};

/**
 * The run summary chip's hover card: pass^1, pass^k, the pass^1 95% range and
 * the inconclusive trials for a finished run, each with what it means.
 */
export default function ReliabilityHeadline({ reliability }) {
  if (reliability?.pass_hat_1 == null) return null;

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
  const fewScenarios = countsOnly(reliability);

  return (
    <Stack spacing={1} sx={{ py: 0.25, maxWidth: 360 }}>
      <Typography sx={{ typography: "s2", fontWeight: 700 }}>
        Run summary
      </Typography>
      {/* Too few scenarios for a rate: show the pooled trial count, labelled
          as such, rather than a scenario average it would not match. */}
      {fewScenarios ? (
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
            fewScenarios
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
        fewScenarios && (
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
      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        <SoonChip
          label="Your bar" detail="A pass rate you set for this environment, so each run shows whether it clears it."
          what="A pass-rate bar for this environment"
          size="tiny"
        />
        <SoonChip
          label="Causes" detail="Why calls failed: the agent, the simulated caller, the grader or the infrastructure."
          what="Verdict causes per run"
          plural
          size="tiny"
        />
      </Stack>
    </Stack>
  );
}
ReliabilityHeadline.propTypes = {
  reliability: PropTypes.object,
};
