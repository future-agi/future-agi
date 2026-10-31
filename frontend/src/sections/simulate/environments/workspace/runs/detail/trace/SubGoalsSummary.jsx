import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { runOutcome } from "./traceTable.constants";

const verdictOf = (passed) =>
  passed === true ? "passed" : passed === false ? "failed" : "inconclusive";

// The roll-up word, read from the sub-goals themselves: any missed one fails
// the line, any unsure one leaves it inconclusive.
function rollup(results) {
  if (results.some((r) => r.passed === false)) return "failed";
  if (results.some((r) => r.passed == null)) return "inconclusive";
  return "passed";
}

const ROLLUP_ICON = {
  failed: "mdi:close",
  inconclusive: "solar:question-circle-linear",
  passed: "mdi:check",
};

/**
 * A call's sub-goals under Run details, as Vel reads them: one line ("Failed ·
 * 4 of 6 met"), then only the ones that weren't met, since they're what the row
 * is read for. Every sub-goal is one click away with Show all.
 */
export default function SubGoalsSummary({ results }) {
  const [expanded, setExpanded] = useState(false);
  if (!results?.length) return null;

  const met = results.filter((r) => r.passed === true).length;
  const verdict = runOutcome(rollup(results));
  // Keyed by position too: a scenario can repeat a sub-goal's name.
  const rows = results.map((r, index) => ({ ...r, index }));
  const listed = expanded ? rows : rows.filter((r) => r.passed !== true);

  return (
    <Stack spacing={0.5} sx={{ mt: 0.75 }}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Iconify
          icon={ROLLUP_ICON[rollup(results)]}
          width={13}
          sx={{ color: verdict.color, flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
        >
          {`${verdict.label} · ${met} of ${results.length} met`}
        </Typography>
        <ButtonBase
          aria-expanded={expanded}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((open) => !open);
          }}
          onKeyDown={(e) => e.stopPropagation()}
          sx={{
            typography: "s3",
            color: "primary.main",
            fontWeight: 500,
            borderRadius: 0.5,
            px: 0.25,
            "&:hover": { textDecoration: "underline" },
          }}
        >
          {expanded ? "Hide" : "Show all"}
        </ButtonBase>
      </Stack>
      {listed.map((goal) => {
        const result = runOutcome(verdictOf(goal.passed));
        return (
          <Stack
            key={`${goal.name}-${goal.index}`}
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ pl: 2.25 }}
          >
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {goal.name}
            </Typography>
            {goal.passed == null ? (
              <Typography sx={{ typography: "s3", color: result.color }}>
                {result.label}
              </Typography>
            ) : (
              <Box
                role="img"
                aria-label={result.label}
                title={result.label}
                sx={{ display: "flex", color: result.color }}
              >
                <Iconify
                  icon={goal.passed ? "mdi:check" : "mdi:close"}
                  width={16}
                  aria-hidden="true"
                />
              </Box>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}
SubGoalsSummary.propTypes = {
  results: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, passed: PropTypes.bool }),
  ),
};
