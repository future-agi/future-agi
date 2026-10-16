import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";

import { useHarnessRunReport } from "src/api/harness/releases";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import {
  SCENARIO_ORIGIN_LABELS,
  displayText,
  errorMessage,
  percent,
  readable,
  shortDigest,
} from "src/pages/dashboard/harness/harnessShared";
import SectionCard from "../../../components/SectionCard";
import { bindingModeLabel } from "../../composition/composition.helpers";

// The run report of a composition run (environment-releases.md §7.7): run mode, comparability
// with a chosen earlier run, coverage, pass^1 / pass^k per use case and per scenario, and
// environment faults kept apart from the agent's score.

const TRIAL_COLORS = {
  passed: "accent.pass",
  failed: "accent.fail",
  errored: "warning.main",
  skipped: "text.disabled",
};

function TrialDots({ trials }) {
  return (
    <Stack direction="row" spacing={0.5} aria-label="Trials">
      {(trials || []).map((trial, index) => (
        <Tooltip
          key={index}
          title={`Trial ${index + 1}: ${trial ? readable(trial) : "pending"}`}
        >
          <Box
            sx={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              bgcolor: TRIAL_COLORS[trial] || "transparent",
              border: "1px solid",
              borderColor: TRIAL_COLORS[trial] || "divider",
            }}
          />
        </Tooltip>
      ))}
    </Stack>
  );
}

TrialDots.propTypes = { trials: PropTypes.array };

// Rate with the baseline beside it, when there is a comparable baseline to read.
function Rate({ value, baseline }) {
  const hasBaseline = typeof baseline === "number";
  const delta =
    hasBaseline && typeof value === "number"
      ? Math.round((value - baseline) * 100)
      : null;
  return (
    <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>
      {percent(value)}
      {hasBaseline && (
        <Typography
          component="span"
          sx={{
            typography: "s3",
            ml: 0.75,
            color:
              delta > 0
                ? "accent.pass"
                : delta < 0
                  ? "accent.fail"
                  : "text.subtitle",
          }}
        >
          {delta === null
            ? `was ${percent(baseline)}`
            : `${delta > 0 ? "+" : ""}${delta} pts`}
        </Typography>
      )}
    </Typography>
  );
}

Rate.propTypes = { value: PropTypes.number, baseline: PropTypes.number };

function Grid({ columns, header, children }) {
  return (
    <Box
      role="table"
      sx={{
        display: "grid",
        gridTemplateColumns: columns,
        alignItems: "center",
      }}
    >
      {header.map((label) => (
        <Box key={label} sx={{ px: 1.5, py: 1 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle" }}
          >
            {label}
          </Typography>
        </Box>
      ))}
      {children}
    </Box>
  );
}

Grid.propTypes = {
  columns: PropTypes.string.isRequired,
  header: PropTypes.arrayOf(PropTypes.string).isRequired,
  children: PropTypes.node,
};

function Cell({ children }) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        minWidth: 0,
      }}
    >
      {children}
    </Box>
  );
}

Cell.propTypes = { children: PropTypes.node };

function Comparability({ comparability }) {
  if (!comparability) return null;
  if (comparability.comparable) {
    return (
      <Alert severity="success" variant="outlined" sx={{ mt: 1.5 }}>
        Directly comparable with the baseline: same environment, suite,
        scenarios, simulator, scorer and runtime.
      </Alert>
    );
  }
  return (
    <Alert severity="warning" variant="outlined" sx={{ mt: 1.5 }}>
      Not a direct comparison. The runs differ in:{" "}
      {(comparability.differing || []).map(displayText).join(", ")}.
    </Alert>
  );
}

Comparability.propTypes = { comparability: PropTypes.object };

export default function RunReport({ env, executionId, live = false }) {
  const [baseline, setBaseline] = useState(null);
  const { runs } = useEnvironmentRuns(env, undefined, { pageSize: 50 });
  const baselineOptions = useMemo(
    () =>
      runs
        .filter((run) => run.executionId && run.executionId !== executionId)
        .map((run) => ({
          id: run.executionId,
          label: run.label || run.executionId,
        })),
    [runs, executionId],
  );
  const baselineId =
    baseline?.id || (typeof baseline === "string" ? baseline.trim() : "");
  const report = useHarnessRunReport(env.id, executionId, baselineId, { live });
  const comparable = report.data?.comparability?.comparable === true;
  // Only a baseline from this environment has a report under this environment's route.
  const sameEnvironmentBaseline = baselineOptions.some(
    (option) => option.id === baselineId,
  );
  const baselineReport = useHarnessRunReport(
    comparable && sameEnvironmentBaseline ? env.id : null,
    baselineId,
  );

  if (report.isLoading) {
    return (
      <Box sx={{ display: "grid", placeItems: "center", py: 6 }}>
        <CircularProgress size={22} />
      </Box>
    );
  }
  if (report.error) {
    return (
      <Alert severity="error" variant="outlined">
        {errorMessage(report.error)}
      </Alert>
    );
  }
  const data = report.data || {};
  const coverage = data.coverage || {};
  const manifest = data.manifest || {};
  const before = comparable ? baselineReport.data : null;
  const beforeUseCase = new Map(
    (before?.use_cases || []).map((row) => [row.use_case, row]),
  );
  const beforeScenario = new Map(
    (before?.scenarios || []).map((row) => [row.scenario_key, row]),
  );

  return (
    <Stack spacing={2}>
      <SectionCard
        title={
          <Stack direction="row" spacing={1} alignItems="center">
            <span>Run report</span>
            <Chip
              size="small"
              color="primary"
              variant="outlined"
              label={bindingModeLabel(data.run_mode)}
            />
          </Stack>
        }
        subtitle={`Manifest ${shortDigest(manifest.digest)} · suite ${shortDigest(
          manifest.suite?.digest,
        )} · binding ${shortDigest(manifest.binding?.digest)} · ${
          manifest.selection?.trials ?? "\u2014"
        } trials per scenario`}
      >
        <Box sx={{ p: 2 }}>
          <Autocomplete
            freeSolo
            size="small"
            options={baselineOptions}
            value={baseline}
            onChange={(_, value) => setBaseline(value || null)}
            getOptionLabel={(option) =>
              typeof option === "string" ? option : option.label
            }
            isOptionEqualToValue={(option, value) => option.id === value?.id}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Compare with run"
                placeholder="Pick an earlier run or paste a run ID"
              />
            )}
            sx={{ maxWidth: 420 }}
          />
          <Comparability comparability={data.comparability} />
        </Box>
      </SectionCard>

      <SectionCard title="Coverage">
        <Stack spacing={0.75} sx={{ p: 2 }}>
          <Typography sx={{ typography: "s2" }}>
            {coverage.runnable ?? 0} runnable of {coverage.suite_scenarios ?? 0}{" "}
            suite scenarios ({coverage.base_scenarios ?? 0} in the base suite)
          </Typography>
          {(coverage.unscorable || []).length > 0 && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Unscorable: {coverage.unscorable.join(", ")}
            </Typography>
          )}
          {(coverage.extension_tools || []).length > 0 && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Extension tools: {coverage.extension_tools.join(", ")}
            </Typography>
          )}
          {data.environment_faults > 0 && (
            <Alert severity="warning" variant="outlined">
              {data.environment_faults} environment{" "}
              {data.environment_faults === 1 ? "fault" : "faults"}: those trials
              are left out of the pass rates and do not count against the agent.
            </Alert>
          )}
        </Stack>
      </SectionCard>

      <SectionCard
        title="Use cases"
        subtitle="pass^1: share of passing trials · pass^k: share of scenarios passing every trial"
      >
        <Grid
          columns="minmax(160px, 2fr) 90px 70px 1fr 1fr"
          header={["Use case", "Scenarios", "Trials", "pass^1", "pass^k"]}
        >
          {(data.use_cases || []).map((row) => (
            <Box key={row.use_case} role="row" sx={{ display: "contents" }}>
              <Cell>
                <Typography sx={{ typography: "s2" }} noWrap>
                  {displayText(row.use_case)}
                </Typography>
              </Cell>
              <Cell>
                <Typography sx={{ typography: "s2" }}>
                  {row.scenarios}
                </Typography>
              </Cell>
              <Cell>
                <Typography sx={{ typography: "s2" }}>{row.trials}</Typography>
              </Cell>
              <Cell>
                <Rate
                  value={row.pass_1}
                  baseline={beforeUseCase.get(row.use_case)?.pass_1}
                />
              </Cell>
              <Cell>
                <Rate
                  value={row.pass_k}
                  baseline={beforeUseCase.get(row.use_case)?.pass_k}
                />
              </Cell>
            </Box>
          ))}
        </Grid>
      </SectionCard>

      <SectionCard title="Scenarios">
        <Grid
          columns="minmax(180px, 2fr) minmax(120px, 1fr) 110px minmax(90px, 1fr) 1fr"
          header={["Scenario", "Use case", "Origin", "Trials", "pass^1"]}
        >
          {(data.scenarios || []).map((row) => (
            <Box key={row.scenario_key} role="row" sx={{ display: "contents" }}>
              <Cell>
                <Typography
                  sx={{ typography: "s2" }}
                  noWrap
                  title={row.scenario_key}
                >
                  {displayText(row.name || row.scenario_key)}
                </Typography>
              </Cell>
              <Cell>
                <Typography
                  sx={{ typography: "s2", color: "text.subtitle" }}
                  noWrap
                >
                  {displayText(row.use_case)}
                </Typography>
              </Cell>
              <Cell>
                <Chip
                  size="small"
                  variant="outlined"
                  label={
                    SCENARIO_ORIGIN_LABELS[row.origin] ||
                    readable(displayText(row.origin))
                  }
                />
              </Cell>
              <Cell>
                <TrialDots trials={row.trials} />
              </Cell>
              <Cell>
                <Rate
                  value={row.pass_1}
                  baseline={beforeScenario.get(row.scenario_key)?.pass_1}
                />
              </Cell>
            </Box>
          ))}
        </Grid>
      </SectionCard>
    </Stack>
  );
}

RunReport.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  executionId: PropTypes.string.isRequired,
  live: PropTypes.bool,
};
