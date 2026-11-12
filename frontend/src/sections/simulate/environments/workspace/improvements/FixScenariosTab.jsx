import PropTypes from "prop-types";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { Box, Button, Link, Stack, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useFixScenarioRows } from "src/api/simulate-environments/fixRuns";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";

import { reviewTrialOf } from "./fixRunView";
import { OUTCOME_ORDER, scenarioOutcomes } from "./scenarioOutcomes";

const OUTCOME = {
  fixed: "Fixed",
  broke: "Broke",
  "still-failing": "Still failing",
  same: "Unchanged",
};

const splitOf = (request, key) => {
  if (request.held_back_scenario_keys?.includes(key)) return "Held back";
  if (request.target_scenario_keys?.includes(key)) return "Target";
  if (request.protected_scenario_keys?.includes(key)) return "Must-pass";
  return "";
};

// The design's per-scenario list (ScenarioOutcomes), fed from the check runs:
// what the chosen trial did to each scenario against today's version.
export default function FixScenariosTab({ fix, candidateId }) {
  const location = useLocation();
  // The trial "Review Trial N" or the trial picker asked for, else the winner,
  // else the trial that did best on its targets.
  const picked = candidateId || reviewTrialOf(fix)?.id;
  const candidate = (fix.candidates || []).find((c) => c.id === picked);
  const trial =
    candidate?.attempt != null ? `Trial ${candidate.attempt}` : "This trial";
  const {
    before,
    after,
    runByScenario,
    isLoading,
    isError,
    isFetching,
    refetch,
  } = useFixScenarioRows(fix, picked);
  const [only, setOnly] = useState(null);
  const rows = scenarioOutcomes(before, after);
  const counts = Object.fromEntries(
    OUTCOME_ORDER.map((k) => [k, rows.filter((r) => r.outcome === k).length]),
  );
  const shown = rows.filter((r) => !only || r.outcome === only);
  if (isLoading) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Loading the results…
      </Typography>
    );
  }
  // A run that failed to load leaves its scenarios out, so nothing is counted
  // until every run is read: partial counts would read as measured results.
  if (isError) {
    return (
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {`Could not load ${trial}'s results.`}
        </Typography>
        <Button size="small" disabled={isFetching} onClick={() => refetch()}>
          Try again
        </Button>
      </Stack>
    );
  }
  if (!rows.length) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        {candidate?.check_runs?.length
          ? `${trial}'s check runs have no scenario results to compare.`
          : `${trial} has no check results: it stopped before its checks ran.`}
      </Typography>
    );
  }
  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Box
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          flexWrap="wrap"
          rowGap={0.75}
        >
          {OUTCOME_ORDER.map((k) => {
            const on = only === k;
            return (
              <Box
                key={k}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                onClick={() => setOnly(on ? null : k)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ")
                    setOnly(on ? null : k);
                }}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  height: 22,
                  px: 0.75,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: on ? "text.primary" : "divider",
                  bgcolor: on ? "action.selected" : "transparent",
                  color: "text.primary",
                  typography: "s3",
                  fontWeight: 500,
                  cursor: "pointer",
                  userSelect: "none",
                  transition: "background-color 120ms, border-color 120ms",
                  "&:hover": {
                    bgcolor: on ? "action.selected" : "action.hover",
                  },
                }}
              >
                {`${OUTCOME[k]} ${counts[k]}`}
              </Box>
            );
          })}
        </Stack>
      </Box>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {shown.map((r) => {
          const run = runByScenario.get(r.key);
          const runHref = run
            ? paths.dashboard.simulate.environments.execution(
                fix.environment_id,
                run.run_id,
                run.test_execution_id,
              )
            : null;
          return (
            <Stack
              key={r.key}
              data-testid={`scenario-${r.key}`}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2, py: 0.875 }}
            >
              <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
                {r.name}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 70,
                  flexShrink: 0,
                }}
              >
                {splitOf(fix.request, r.key)}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  width: 84,
                  flexShrink: 0,
                  color:
                    r.outcome === "fixed"
                      ? BUILD_TONES.green
                      : r.outcome === "broke"
                        ? BUILD_TONES.red
                        : "text.secondary",
                }}
              >
                {OUTCOME[r.outcome]}
              </Typography>
              {runHref && (
                <Link
                  component={RouterLink}
                  href={runHref}
                  state={{ backTo: location.pathname }}
                  sx={{ typography: "s3", flexShrink: 0 }}
                >
                  Open run
                </Link>
              )}
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}

FixScenariosTab.propTypes = {
  fix: PropTypes.object.isRequired,
  candidateId: PropTypes.string,
};
