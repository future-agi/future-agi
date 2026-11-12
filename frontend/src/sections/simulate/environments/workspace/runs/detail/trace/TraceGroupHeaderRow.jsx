import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  Stack,
  Typography,
  TableCell,
  TableRow,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import {
  GROUP_METRIC_COLUMNS,
  HEAD_ROW_PX,
  evalGrading,
  isBad,
  neutralCheckboxSx,
  pinnedLeftSx,
} from "./traceTable.constants";
import { CellSkeleton } from "./traceCells";

const DESC_KEYS = [
  "callDetails",
  "persona",
  "scenario",
  "situation",
  "subGoals",
  "idealOutcome",
  "conversationBranch",
];
const rowHover = (t) =>
  `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`;

// A group's figure for one column: the value, red with a warning when bad,
// over how it was aggregated. A dash when there is none.
export function GroupMetricValue({
  value,
  suffix = "",
  metric,
  aggregation = "Avg",
}) {
  if (value == null) {
    return (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        -
      </Typography>
    );
  }
  const bad = metric
    ? isBad(metric, typeof value === "number" ? value : Number(value))
    : false;
  return (
    <Stack alignItems="flex-start" spacing={0.25}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        {bad && (
          <Iconify
            icon="solar:danger-triangle-bold"
            width={13}
            sx={{ color: "status.danger.solid", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            fontVariantNumeric: "tabular-nums",
            color: bad ? "status.danger.fg" : "text.primary",
          }}
        >
          {typeof value === "number"
            ? value.toLocaleString(undefined, {
                maximumFractionDigits: 1,
              })
            : value}
          {suffix}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {aggregation}
      </Typography>
    </Stack>
  );
}
GroupMetricValue.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  suffix: PropTypes.string,
  metric: PropTypes.string,
  aggregation: PropTypes.string,
};

// A group's mean score for one eval, heat-tinted, over how many calls it
// averages; a skeleton while grading and a dash when nothing is scored.
export function GroupEvalMean({ aggregate, grading = false, sx }) {
  if (!aggregate?.scored)
    return grading ? (
      <CellSkeleton />
    ) : (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        -
      </Typography>
    );
  const meanScore = aggregate.scoreSum / aggregate.scored;
  return (
    <Box
      sx={{
        bgcolor: interpolateColorBasedOnScore(meanScore, 1),
        color: "text.primary",
        ...sx,
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          fontVariantNumeric: "tabular-nums",
          color: "text.primary",
        }}
      >
        {Math.round(meanScore * 100)}%
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        Avg · {aggregate.scored} scored
      </Typography>
    </Box>
  );
}
GroupEvalMean.propTypes = {
  aggregate: PropTypes.shape({
    scored: PropTypes.number,
    scoreSum: PropTypes.number,
  }),
  grading: PropTypes.bool,
  sx: PropTypes.object,
};

// Fills the whole eval cell with the tint.
const evalFillSx = {
  position: "absolute",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  alignItems: "flex-start",
  px: 2,
  py: 1.5,
};

// The collapsible group header: chevron + label on the first descriptive
// column, a per-column aggregate summary on the rest, and a heat-tinted mean
// score per eval column. Clicking anywhere toggles the group.
export default function TraceGroupHeaderRow({
  group,
  collapsed,
  onToggle,
  show,
  showEvals,
  evals,
  top = HEAD_ROW_PX,
  loading = false,
  leadingCell = false,
  checkbox = null,
}) {
  // Pinned under the head row while its group's calls scroll past; the next
  // group's row slides over it. Opaque for that, so the hover tint layers over
  // the paper instead of replacing it.
  const cellSx = {
    position: "sticky",
    top,
    zIndex: 2,
    bgcolor: "background.paper",
    borderBottom: "1px solid",
    borderColor: "divider",
    cursor: "pointer",
    py: 1.25,
    px: 2,
    ".MuiTableRow-root:hover &": { backgroundImage: rowHover },
    // The same column dividers as the head and call rows, so the grid runs
    // unbroken through the group row.
    "&:not(:first-of-type)": {
      borderLeft: "1px solid",
      borderColor: "divider",
    },
  };
  const numCellSx = { ...cellSx, textAlign: "left" };

  const descColumns = DESC_KEYS.filter((k) => show(k));
  const a = group.agg || {};
  const uniqueBy = (fn) => new Set(group.rows.map(fn).filter(Boolean)).size;
  const personaCount = uniqueBy((t) => t.persona);

  const descSummary = (key) => {
    if (key === "persona")
      return personaCount
        ? `${personaCount} persona${personaCount === 1 ? "" : "s"}`
        : "-";
    // Left empty: a scenario count would repeat the task count (one call per
    // scenario trial).
    if (key === "scenario") return null;
    if (key === "situation")
      return `${group.count} situation${group.count === 1 ? "" : "s"}`;
    if (key === "subGoals") {
      // Counted from the rows here, so only once every call in the group is
      // on this page: a page's share would read as the whole group's.
      if (group.rows.length < group.count) return "-";
      const subGoalCount = new Set(
        group.rows.flatMap((t) =>
          (t.subGoalResults || []).map((goal) => goal.name),
        ),
      ).size;
      return subGoalCount
        ? `${subGoalCount} sub-goal${subGoalCount === 1 ? "" : "s"}`
        : "-";
    }
    if (key === "idealOutcome")
      return `${group.count} outcome${group.count === 1 ? "" : "s"}`;
    if (key === "conversationBranch")
      return `${group.count} branch${group.count === 1 ? "" : "es"}`;
    return "-";
  };

  const numCell = (key, value, suffix = "", metric, aggregation = "Avg") => (
    <TableCell key={key} sx={numCellSx}>
      {value == null && loading ? (
        <CellSkeleton />
      ) : (
        <GroupMetricValue
          value={value}
          suffix={suffix}
          metric={metric}
          aggregation={aggregation}
        />
      )}
    </TableCell>
  );

  const label = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ minWidth: 0 }}
    >
      <Iconify
        icon={
          collapsed
            ? "solar:alt-arrow-right-linear"
            : "solar:alt-arrow-down-linear"
        }
        width={13}
        sx={{ color: "text.subtitle", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
        }}
      >
        {group.label}
      </Typography>
    </Stack>
  );

  return (
    <TableRow onClick={onToggle}>
      {/* Keeps the grid aligned under the call rows' checkbox column. */}
      {leadingCell && (
        <TableCell
          sx={{
            ...cellSx,
            ...pinnedLeftSx,
            zIndex: 3,
            px: 1,
            width: 44,
            textAlign: "center",
          }}
        >
          {checkbox?.selectable && (
            <Checkbox
              size="small"
              checked={checkbox.checked}
              indeterminate={checkbox.indeterminate}
              // Ticks the group's calls; folding is the rest of the row's job.
              onClick={(e) => e.stopPropagation()}
              onChange={checkbox.onChange}
              inputProps={{ "aria-label": `Select calls in ${group.label}` }}
              sx={{ p: 0.25, ...neutralCheckboxSx }}
            />
          )}
        </TableCell>
      )}
      {descColumns.length === 0 ? (
        <TableCell sx={{ ...cellSx, pl: 2, overflow: "hidden" }}>
          {label}
        </TableCell>
      ) : (
        descColumns.map((key, i) => (
          <TableCell key={key} sx={{ ...cellSx, overflow: "hidden" }}>
            {i === 0 ? (
              label
            ) : (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                {descSummary(key)}
              </Typography>
            )}
          </TableCell>
        ))
      )}
      {GROUP_METRIC_COLUMNS.map(
        (c) =>
          show(c.key) &&
          numCell(c.key, a[c.field], c.suffix, c.metric, c.aggregation),
      )}
      {showEvals &&
        evals.map((e) => {
          const ea = a.evals?.[e.id];
          return (
            <TableCell
              key={`eval-${e.id}`}
              sx={ea?.scored ? { ...numCellSx, p: 0 } : numCellSx}
            >
              <GroupEvalMean
                aggregate={ea}
                // A finished call can still be waiting on this eval's grade.
                grading={loading || evalGrading(group.rows, e.id)}
                sx={evalFillSx}
              />
            </TableCell>
          );
        })}
    </TableRow>
  );
}
TraceGroupHeaderRow.propTypes = {
  group: PropTypes.object,
  collapsed: PropTypes.bool,
  onToggle: PropTypes.func,
  show: PropTypes.func,
  showEvals: PropTypes.bool,
  evals: PropTypes.array,
  top: PropTypes.number,
  loading: PropTypes.bool,
  leadingCell: PropTypes.bool,
  // The group's tick: whether it has calls to tick, and their state.
  checkbox: PropTypes.shape({
    selectable: PropTypes.bool,
    checked: PropTypes.bool,
    indeterminate: PropTypes.bool,
    onChange: PropTypes.func,
  }),
};
