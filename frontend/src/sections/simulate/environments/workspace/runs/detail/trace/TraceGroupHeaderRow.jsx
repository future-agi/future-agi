import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  TableCell,
  TableRow,
  Checkbox,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import { isBad, neutralCheckboxSx } from "./traceTable.constants";

const DESC_KEYS = [
  "callDetails",
  "persona",
  "scenario",
  "idealOutcome",
  "conversationBranch",
];
const rowHover = (t) =>
  alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.025);

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
  selected,
  onToggleGroup,
}) {
  const cellSx = {
    bgcolor: "transparent",
    borderBottom: "1px solid",
    borderColor: "divider",
    borderLeft: "none",
    cursor: "pointer",
    py: 1.25,
    px: 1.5,
    ".MuiTableRow-root:hover &": { bgcolor: rowHover },
    "&:not(:first-of-type)": { borderLeft: "none" },
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
        : "—";
    if (key === "scenario")
      return `${group.count} scenario${group.count === 1 ? "" : "s"}`;
    if (key === "idealOutcome")
      return `${group.count} outcome${group.count === 1 ? "" : "s"}`;
    if (key === "conversationBranch")
      return `${group.count} branch${group.count === 1 ? "" : "es"}`;
    return "—";
  };

  const numCell = (value, suffix = "", metric) => {
    const bad = metric
      ? isBad(metric, typeof value === "number" ? value : Number(value))
      : false;
    return (
      <TableCell sx={numCellSx}>
        {value == null ? (
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>
            —
          </Typography>
        ) : (
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
              {typeof value === "number" ? value.toLocaleString() : value}
              {suffix}
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

  const ids = group.rows.map((r) => r.id);
  const allOn = ids.length > 0 && ids.every((id) => selected?.has(id));
  const someOn = ids.some((id) => selected?.has(id)) && !allOn;

  return (
    <TableRow onClick={onToggle}>
      <TableCell
        sx={{
          width: 48,
          pl: 1.25,
          pr: 0,
          py: 0,
          verticalAlign: "middle",
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: "pointer",
          ".MuiTableRow-root:hover &": { bgcolor: rowHover },
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <Checkbox
          size="small"
          checked={allOn}
          indeterminate={someOn}
          onChange={() => onToggleGroup?.(group.rows)}
          sx={neutralCheckboxSx}
        />
      </TableCell>
      {descColumns.length === 0 ? (
        <TableCell sx={{ ...cellSx, pl: 2, overflow: "hidden" }}>
          {label}
        </TableCell>
      ) : (
        descColumns.map((key, i) => (
          <TableCell
            key={key}
            sx={{ ...cellSx, pl: i === 0 ? 2 : 1.5, overflow: "hidden" }}
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
      {show("tokens") && numCell(a.tokens)}
      {showEvals &&
        evals.map((e) => {
          const ea = a.evals?.[e.id];
          if (!ea || !ea.scored) {
            return (
              <TableCell key={`eval-${e.id}`} sx={numCellSx}>
                <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                  —
                </Typography>
              </TableCell>
            );
          }
          const meanScore = ea.scoreSum / ea.scored;
          const rate = Math.round(meanScore * 100);
          return (
            <TableCell
              key={`eval-${e.id}`}
              sx={{ ...numCellSx, p: 0, position: "relative" }}
            >
              <Box
                sx={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  alignItems: "center",
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
  selected: PropTypes.object,
  onToggleGroup: PropTypes.func,
};
