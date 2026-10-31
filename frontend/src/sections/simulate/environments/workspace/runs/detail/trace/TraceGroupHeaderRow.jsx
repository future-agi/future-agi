import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
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
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { SCORING_STATUS } from "../../runs.constants";
import {
  HEAD_ROW_PX,
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
// The label line sizes its column up to this, so most labels fit and only the
// longest are cut.
const GROUP_LABEL_FIT = 560;
const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

// An invisible copy of the label line that asks the column for its width, up
// to GROUP_LABEL_FIT. The visible line is kept out of the column's sizing, so
// a long label can't stretch the column past that.
function GroupLabelSizer({ text }) {
  return (
    <Box
      aria-hidden
      sx={{
        height: 0,
        overflow: "hidden",
        visibility: "hidden",
        whiteSpace: "nowrap",
        maxWidth: GROUP_LABEL_FIT,
        pl: 3.5,
        typography: "s2",
        fontWeight: "fontWeightBold",
      }}
    >
      {text}
    </Box>
  );
}
GroupLabelSizer.propTypes = { text: PropTypes.string };

// How many scenarios a group holds. Each scenario runs `trials` times, so its
// calls divide evenly, unless the axis can split one scenario's repeats across
// groups; then the group can only count calls.
function countLabel(group, trials, trialsSplit) {
  const calls = group.count ?? group.rows.length;
  if (trials <= 1) return plural(calls, "scenario");
  if (trialsSplit || calls % trials) return plural(calls, "call");
  return plural(calls / trials, "scenario");
}

const rowHover = (t) => {
  const tint = alpha(
    t.palette.text.primary,
    t.palette.mode === "dark" ? 0.04 : 0.025,
  );
  return `linear-gradient(${tint}, ${tint})`;
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
  trials = 1,
  trialsSplit = false,
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

  const numCell = (value, suffix = "", metric, aggregation = "Avg") => {
    const bad = metric
      ? isBad(metric, typeof value === "number" ? value : Number(value))
      : false;
    return (
      <TableCell sx={numCellSx}>
        {value == null && loading ? (
          <CellSkeleton />
        ) : value == null ? (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            -
          </Typography>
        ) : (
          <Stack alignItems="flex-start" spacing={0.25}>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              {bad && (
                <Iconify
                  icon="solar:danger-triangle-bold"
                  width={13}
                  sx={{ color: BUILD_TONES.red, flexShrink: 0 }}
                />
              )}
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: "fontWeightBold",
                  fontVariantNumeric: "tabular-nums",
                  color: bad ? BUILD_TONES.red : "text.primary",
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
        )}
      </TableCell>
    );
  };

  // "Refunds · 4 scenarios · 80% passed 4/5": the group's pass rate read the
  // way the run's is, passed out of the calls that were scored. One line, so
  // the pinned row keeps its height.
  const count = ` · ${countLabel(group, trials, trialsSplit)}`;
  const measured = group.measured ?? 0;
  const pct = measured ? Math.round(((group.passed ?? 0) / measured) * 100) : 0;
  const passText = measured
    ? ` · ${pct}% passed ${group.passed ?? 0}/${measured}`
    : "";
  const label = (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        // Out of the column's sizing: the sizer below asks for the width.
        sx={{ width: 0, minWidth: "100%" }}
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
          title={group.label}
          sx={{
            typography: "s2",
            fontWeight: "fontWeightBold",
            color: "text.primary",
            minWidth: 0,
            flex: "0 1 auto",
          }}
        >
          {group.label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            whiteSpace: "nowrap",
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {count}
          {measured > 0 && (
            <>
              {" · "}
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: "fontWeightSemiBold" }}
              >
                {`${pct}%`}
              </Box>
              {` passed ${group.passed ?? 0}/${measured}`}
            </>
          )}
        </Typography>
      </Stack>
      <GroupLabelSizer text={`${group.label}${count}${passText}`} />
    </>
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
      {show("csat") && numCell(a.csat, "", "csat")}
      {show("turns") && numCell(a.turns, "", "turns")}
      {show("latency") && numCell(a.latency, "ms", "latency")}
      {show("stopLatency") && numCell(a.stopLatency, "ms")}
      {show("aiInterruptions") && numCell(a.aiInterruptions)}
      {show("tokens") && numCell(a.tokens, "", undefined, "Total")}
      {showEvals &&
        evals.map((e) => {
          const ea = a.evals?.[e.id];
          if (!ea || !ea.scored) {
            // A finished call can still be waiting on this eval's grade.
            const grading = group.rows.some((t) =>
              t.evalResults?.some(
                (r) => r.id === e.id && r.status === SCORING_STATUS.PENDING,
              ),
            );
            return (
              <TableCell key={`eval-${e.id}`} sx={numCellSx}>
                {loading || grading ? (
                  <CellSkeleton />
                ) : (
                  <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                    -
                  </Typography>
                )}
              </TableCell>
            );
          }
          const meanScore = ea.scoreSum / ea.scored;
          const rate = Math.round(meanScore * 100);
          return (
            <TableCell key={`eval-${e.id}`} sx={{ ...numCellSx, p: 0 }}>
              <Box
                sx={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  alignItems: "flex-start",
                  px: 2,
                  py: 1.5,
                  bgcolor: interpolateColorBasedOnScore(meanScore, 1),
                  color: "text.primary",
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
                  {rate}%
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  Avg · {ea.scored} scored
                </Typography>
              </Box>
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
  // The run's repeats per scenario, and whether this axis can split them.
  trials: PropTypes.number,
  trialsSplit: PropTypes.bool,
  // The group's tick: whether it has calls to tick, and their state.
  checkbox: PropTypes.shape({
    selectable: PropTypes.bool,
    checked: PropTypes.bool,
    indeterminate: PropTypes.bool,
    onChange: PropTypes.func,
  }),
};
