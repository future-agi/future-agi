import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import TrialHeatmap from "./TrialHeatmap";
import { scenarioTitle } from "../../_mock/scenarios";

const OUTCOME = {
  fixed: "Fixed",
  broke: "Broke",
  "still-failing": "Still failing",
  flaky: "Flaky",
  inconclusive: "Inconclusive",
  "off-script": "Not counted",
  same: "Unchanged",
};
const OUTCOME_ORDER = [
  "fixed",
  "broke",
  "still-failing",
  "flaky",
  "inconclusive",
  "off-script",
  "same",
];
/* The chip says why, since "not counted" alone doesn't. */
const CHIP = { "off-script": "Caller went off script" };

/*
  What the winner did to each scenario — fixed, broke, still failing,
  unchanged — on the held-back set it never saw and the target scenarios it
  was tuned on. Every trial against every scenario is one click further.
*/
export default function ScenarioOutcomes({ result, winner }) {
  const [only, setOnly] = useState(null);
  const [grid, setGrid] = useState(false);
  const rows = [
    ...(result.heldTasks || []).map((t) => ({
      ...t,
      split: "Held back",
      outcome: result.heldPerScenario?.[t.id] || "same",
    })),
    ...(result.trainMeasuredTasks || []).map((t) => ({
      ...t,
      split: "Target",
      outcome: winner?.perScenario?.[t.id] || "same",
    })),
  ];
  const counts = Object.fromEntries(
    OUTCOME_ORDER.map((k) => [k, rows.filter((x) => x.outcome === k).length]),
  );
  const shown = rows
    .filter((x) => !only || x.outcome === only)
    .sort(
      (a, b) =>
        OUTCOME_ORDER.indexOf(a.outcome) - OUTCOME_ORDER.indexOf(b.outcome),
    );
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
        flexWrap="wrap"
        rowGap={0.75}
      >
        {OUTCOME_ORDER.filter((k) => k !== "off-script" || counts[k]).map(
          (k) => (
            <Chip
              key={k}
              size="small"
              clickable
              onClick={() => setOnly(only === k ? null : k)}
              label={`${CHIP[k] || OUTCOME[k]} ${counts[k]}`}
              sx={{
                border: "1px solid",
                color: "text.primary",
                borderColor: only === k ? "text.primary" : "divider",
                bgcolor: only === k ? "action.selected" : "transparent",
              }}
            />
          ),
        )}
        <Box flex={1} />
        <Button
          size="small"
          onClick={() => setGrid((g) => !g)}
          sx={{ color: "text.secondary" }}
        >
          {grid ? "Hide every run" : "Every run"}
        </Button>
      </Stack>
      {grid ? (
        <TrialHeatmap
          trials={result.trials}
          scenarios={result.trainMeasuredTasks || []}
          winner={winner}
        />
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
          sx={{ maxHeight: 420, overflowY: "auto" }}
        >
          {shown.map((x) => (
            <Stack
              key={`${x.split}-${x.id}`}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2, py: 0.875 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                {scenarioTitle(x.title)}
              </Typography>
              {x.fromIssue && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  {`${x.fromIssue}${x.before === "flaky" ? " · inconclusive" : x.before === "passed" ? " · didn't reproduce" : ""}`}
                </Typography>
              )}
              {x.outcome === "off-script" && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  caller went off script
                </Typography>
              )}
              {x.fresh && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  fresh variation
                </Typography>
              )}
              {x.critical && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  must-pass
                </Typography>
              )}
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 70,
                  flexShrink: 0,
                }}
              >
                {x.split}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  width: 84,
                  flexShrink: 0,
                  color:
                    x.outcome === "fixed"
                      ? "status.success.fg"
                      : x.outcome === "broke"
                        ? "status.danger.fg"
                        : "text.secondary",
                }}
              >
                {OUTCOME[x.outcome]}
              </Typography>
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
}
ScenarioOutcomes.propTypes = {
  result: PropTypes.object,
  winner: PropTypes.object,
};
