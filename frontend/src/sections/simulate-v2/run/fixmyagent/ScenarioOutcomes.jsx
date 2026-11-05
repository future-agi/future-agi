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
  inconclusive: "Unmeasured",
  "off-script": "Unmeasured",
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
/* The plan's three lists — a case is in only one. */
/* The plan's three lists — a case is in only one. Each is a heading, so a
   row carries one result, not a second label beside it. */
const SETS = [
  { id: "Target", title: "Targets", note: "the failing cases it was tuned on" },
  {
    id: "Held back",
    title: "Held back",
    note: "never seen — they check the fix holds",
  },
  {
    id: "Protected",
    title: "Protected",
    note: "passing cases it must not break",
  },
];

const chipSx = (on) => ({
  border: "1px solid",
  color: "text.primary",
  borderColor: on ? "text.primary" : "divider",
  bgcolor: on ? "action.selected" : "transparent",
});

/*
  What the winner did to each scenario — fixed, broke, still failing,
  unchanged — on the held-back set it never saw and the target scenarios it
  was tuned on. Every candidate against every scenario is one click further.
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
  const taken = new Set(rows.map((x) => x.id));
  rows.push(
    ...(result.mustPassTasks || [])
      .filter((t) => !taken.has(t.id))
      .map((t) => ({
        ...t,
        split: "Protected",
        outcome: winner?.perScenario?.[t.id] || "same",
      })),
  );
  const inSet = rows;
  /* must-pass only says something when some rows aren't. */
  const markCritical = rows.some((x) => !x.critical);
  const counts = Object.fromEntries(
    OUTCOME_ORDER.map((k) => [k, inSet.filter((x) => x.outcome === k).length]),
  );
  const shown = inSet
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
        {OUTCOME_ORDER.filter((k) => counts[k] || only === k).map((k) => (
          <Chip
            key={k}
            size="small"
            clickable
            onClick={() => setOnly(only === k ? null : k)}
            label={`${k === "same" && inSet.filter((x) => x.outcome === "same").every((x) => x.split === "Protected") ? "Still passing" : CHIP[k] || OUTCOME[k]} ${counts[k]}`}
            sx={chipSx(only === k)}
          />
        ))}
        <Box flex={1} />
        <Button
          variant="outlined"
          size="small"
          onClick={() => setGrid((g) => !g)}
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
        >
          {SETS.map((g) => {
            const list = shown.filter((x) => x.split === g.id);
            if (!list.length) return null;
            return [
              <Stack
                key={`head-${g.id}`}
                direction="row"
                alignItems="baseline"
                spacing={1}
                sx={{ px: 2, pt: 1.5, pb: 0.75, bgcolor: "background.neutral" }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {`${g.title} · ${list.length}`}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {g.note}
                </Typography>
              </Stack>,
              ...list.map((x) => (
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
                  {x.fresh &&
                    !/^Fresh variation/.test(scenarioTitle(x.title)) && (
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
                  {x.critical && markCritical && (
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
                    {x.split === "Protected" && x.outcome === "same"
                      ? "Still passing"
                      : OUTCOME[x.outcome]}
                  </Typography>
                </Stack>
              )),
            ];
          })}
        </Stack>
      )}
    </Box>
  );
}
ScenarioOutcomes.propTypes = {
  result: PropTypes.object,
  winner: PropTypes.object,
};
