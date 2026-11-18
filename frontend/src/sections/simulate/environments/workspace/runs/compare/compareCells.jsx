import PropTypes from "prop-types";
import { Box, Stack, TableCell, Typography } from "@mui/material";
import { visuallyHidden } from "@mui/utils";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { runColor, runColorValue } from "../runs.constants";
import { bodyCellSx, runOutcome } from "../detail/trace/traceTable.constants";
import { SubGoalVerdicts } from "../detail/trace/traceRowCells";
import { COMPARE_COPY } from "../../workspace.constants";

// A compared run's colour: its place in the comparison, so no two compared
// runs share one; a run not in it takes its ordinal's, as on the runs list.
export const compareRunColor = ({ ordinal, colorIndex }) =>
  runColor(colorIndex == null ? ordinal : colorIndex + 1);
// The same colour as a value, for alpha().
export const compareRunColorValue = (theme, { ordinal, colorIndex }) =>
  runColorValue(theme, colorIndex == null ? ordinal : colorIndex + 1);

// A run's colour as a small dot; its number is for screen readers only.
export function RunDot({ ordinal, colorIndex }) {
  return (
    <Box
      role="img"
      aria-label={`Run ${ordinal}`}
      sx={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        flexShrink: 0,
        bgcolor: compareRunColor({ ordinal, colorIndex }),
      }}
    />
  );
}
RunDot.propTypes = {
  ordinal: PropTypes.number,
  colorIndex: PropTypes.number,
};

// A change against the baseline: an arrow the way it moved, green when that's
// better and red when worse. Screen readers hear both in words.
export function DiffArrow({ change }) {
  if (!change) return null;
  const tone = change.better ? "status.success.fg" : "status.danger.fg";
  return (
    <Stack
      component="span"
      direction="row"
      alignItems="center"
      spacing={0.25}
      data-testid="diff-arrow"
      sx={{ color: tone, flexShrink: 0 }}
    >
      <Box component="span" sx={visuallyHidden}>
        {COMPARE_COPY.diffSpoken(change)}
      </Box>
      <Iconify
        icon={change.up ? "solar:arrow-up-linear" : "solar:arrow-down-linear"}
        width={12}
        aria-hidden
      />
      <Typography
        component="span"
        aria-hidden
        sx={{
          typography: "s3",
          fontWeight: 500,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {change.text}
      </Typography>
    </Stack>
  );
}
DiffArrow.propTypes = {
  change: PropTypes.shape({
    up: PropTypes.bool,
    better: PropTypes.bool,
    text: PropTypes.string,
  }),
};

// The compare-only Status column: the call's verdict, what the baseline's was
// when it differs, and its sub-goals met, listed on hover.
export function CompareStatusCell({ task, diff = null }) {
  const outcome = runOutcome(task.status);
  const goals = task.subGoalResults ?? [];
  const met = goals.filter((g) => g.passed === true).length;
  return (
    <TableCell sx={bodyCellSx}>
      <Stack spacing={0.5} alignItems="flex-start">
        <StatusBadge
          status={task.status}
          label={outcome.label}
          role={outcome.role}
          variant="dot"
        />
        {diff?.was && (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {COMPARE_COPY.was(diff.was)}
          </Typography>
        )}
        {goals.length > 0 && (
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <CustomTooltip
              show
              card
              title={<SubGoalVerdicts goals={goals} sx={{ py: 0.5 }} />}
            >
              <Typography
                component="span"
                // Focusable, so the list opens from the keyboard too.
                tabIndex={0}
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  textDecoration: "underline dotted",
                  textUnderlineOffset: 3,
                  cursor: "help",
                }}
              >
                {COMPARE_COPY.subGoals(met, goals.length)}
              </Typography>
            </CustomTooltip>
            <DiffArrow change={diff?.subGoals} />
          </Stack>
        )}
      </Stack>
    </TableCell>
  );
}
CompareStatusCell.propTypes = {
  task: PropTypes.object.isRequired,
  diff: PropTypes.object,
};
