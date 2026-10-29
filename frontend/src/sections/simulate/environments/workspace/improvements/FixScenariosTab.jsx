import PropTypes from "prop-types";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { Box, Chip, Link, Stack, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useFixScenarioRows } from "src/api/simulate-environments/fixRuns";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";

import { winnerOf } from "./fixRunView";
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
  const picked = candidateId || winnerOf(fix)?.id;
  const { before, after, run, isLoading } = useFixScenarioRows(fix, picked);
  const [only, setOnly] = useState(null);
  const rows = scenarioOutcomes(before, after);
  const counts = Object.fromEntries(
    OUTCOME_ORDER.map((k) => [k, rows.filter((r) => r.outcome === k).length]),
  );
  const shown = rows.filter((r) => !only || r.outcome === only);
  const runHref = run
    ? paths.dashboard.simulate.environments.execution(
        fix.environment_id,
        run.run_id,
        run.test_execution_id,
      )
    : null;
  if (isLoading) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Loading the results…
      </Typography>
    );
  }
  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        flexWrap="wrap"
        rowGap={0.75}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {OUTCOME_ORDER.map((k) => (
          <Chip
            key={k}
            size="small"
            clickable
            onClick={() => setOnly(only === k ? null : k)}
            label={`${OUTCOME[k]} ${counts[k]}`}
            sx={{
              border: "1px solid",
              color: "text.primary",
              borderColor: only === k ? "text.primary" : "divider",
              bgcolor: only === k ? "action.selected" : "transparent",
            }}
          />
        ))}
      </Stack>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {shown.map((r) => (
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
        ))}
      </Stack>
    </Box>
  );
}

FixScenariosTab.propTypes = {
  fix: PropTypes.object.isRequired,
  candidateId: PropTypes.string,
};
