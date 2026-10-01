import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  TableCell,
  TableRow,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import {
  HEAD_ROW_PX,
  PENDING_EVAL_STATUS,
  isBad,
} from "./traceTable.constants";
import { CellSkeleton } from "./traceCells";

const DESC_KEYS = [
  "callDetails",
  "status",
  "persona",
  "scenario",
  "idealOutcome",
  "conversationBranch",
];
const rowHover = (t) => {
  const tint = alpha(
    t.palette.text.primary,
    t.palette.mode === "dark" ? 0.04 : 0.025,
  );
  return `linear-gradient(${tint}, ${tint})`;
};

// The collapsible group header: chevron + label + count on the first descriptive
// column, a per-column aggregate summary on the rest, and a heat-tinted mean
// score per eval column. Clicking anywhere toggles the group.
export default function TraceGroupHeaderRow({
  group,
  collapsed,
  onToggle,
  show,
  showEvals,
  evals,
  loading = false,
}) {
  // Pinned under the head row while its group's calls scroll past; the next
  // group's row slides over it. Opaque for that, so the hover tint layers over
  // the paper instead of replacing it.
  const cellSx = {
    position: "sticky",
    top: HEAD_ROW_PX,
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
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  const numCellSx = { ...cellSx, textAlign: "left" };

  const descColumns = DESC_KEYS.filter((k) => show(k));
  const a = group.agg || {};
  const uniqueBy = (fn) => new Set(group.rows.map(fn).filter(Boolean)).size;
  const personaCount = uniqueBy((t) => t.persona);

  const descSummary = (key) => {
    if (key === "status") {
      // The group's calls load a page at a time, so only count once all of
      // them are here — a partial count would read as the whole group.
      if (group.rows.length < group.count) return "-";
      const done = group.rows.filter(
        (t) => t.executionStatus === "completed",
      ).length;
      return `${done}/${group.count} completed`;
    }
    if (key === "persona")
      return personaCount
        ? `${personaCount} persona${personaCount === 1 ? "" : "s"}`
        : "-";
    if (key === "scenario")
      return `${group.count} scenario${group.count === 1 ? "" : "s"}`;
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
          fontWeight: "fontWeightBold",
          color: "text.primary",
        }}
      >
        {group.label}
      </Typography>
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}
      >
        · {group.count} task{group.count === 1 ? "" : "s"}
      </Typography>
    </Stack>
  );


  return (
    <TableRow onClick={onToggle}>
      {descColumns.length === 0 ? (
        <TableCell sx={{ ...cellSx, pl: 2, overflow: "hidden" }}>
          {label}
        </TableCell>
      ) : (
        descColumns.map((key, i) => (
          <TableCell
            key={key}
            sx={{ ...cellSx, overflow: "hidden" }}
          >
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
                (r) => r.id === e.id && r.status === PENDING_EVAL_STATUS,
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
            <TableCell
              key={`eval-${e.id}`}
              sx={{ ...numCellSx, p: 0 }}
            >
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
  loading: PropTypes.bool,
};
