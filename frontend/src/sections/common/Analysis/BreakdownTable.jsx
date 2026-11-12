import React from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import MovementButtons from "src/sections/common/Metrics/MovementButtons";
import {
  formatDelta,
  formatValue,
  deltaTone,
} from "src/sections/common/Metrics/metricFormat";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";
import AnalysisCard from "./AnalysisCard";
import { aggOf } from "./MetricCard";

const tabular = { fontVariantNumeric: "tabular-nums" };

const selectSx = {
  height: 28,
  typography: "s2",
  "& .MuiSelect-select": { py: 0.25, pl: 1 },
};

/**
 * BreakdownTable — a metric per group (Split, Metadata · key) and run, the
 * delta vs the baseline run and ↑/↓ counts (PRODUCT-SPEC §3.13 M8.5). A
 * count opens the grid filtered to that group and movement; groups under 10
 * rows are greyed "Low sample". Shared by the experiment and compare Summary.
 */
const BreakdownTable = ({
  breakdown,
  options,
  metrics,
  runs,
  colors,
  baselineId,
  baselineLabel,
  groupBy,
  onGroupBy,
  onMetric,
  cellHref,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const metric = metrics.find((m) => m.key === breakdown?.metric);
  const candidates = runs.filter((r) => r.run_id !== baselineId);
  const highlight = breakdown?.highlight;
  const agg = metric ? aggOf(metric) : "avg";

  return (
    <AnalysisCard
      id="analysis-breakdown"
      icon="mdi:table-split-cell"
      title="Breakdown"
      actions={
        <Stack direction="row" gap={1} alignItems="center">
          <Typography typography="s2" color="text.secondary">
            By
          </Typography>
          <Select
            size="small"
            value={groupBy}
            onChange={(e) => onGroupBy(e.target.value)}
            sx={selectSx}
          >
            {options.map((o) => (
              <MenuItem key={o.value} value={o.value} sx={{ typography: "s2" }}>
                {o.label}
              </MenuItem>
            ))}
          </Select>
          <Typography typography="s2" color="text.secondary">
            Metric
          </Typography>
          <Select
            size="small"
            value={breakdown?.metric ?? ""}
            onChange={(e) => onMetric(e.target.value)}
            sx={{ ...selectSx, minWidth: 140 }}
          >
            {metrics.map((m) => (
              <MenuItem key={m.key} value={m.key} sx={{ typography: "s2" }}>
                {m.name}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      }
      subtitle={
        highlight
          ? `Largest drop: ${highlight.group_label} on ${highlight.metric_name} (${formatDelta(
              metrics.find((m) => m.key === highlight.metric),
              highlight.delta,
            )}, ${highlight.regressed} rows regressed)`
          : null
      }
      dense
    >
      {!metric || !breakdown?.groups?.length ? (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 1.5, pb: 1.5 }}
        >
          No groups to break down by.
        </Typography>
      ) : (
        <Table
          size="small"
          sx={{ "& td, & th": { typography: "s2", borderColor: "divider" } }}
        >
          <TableHead>
            <TableRow sx={{ bgcolor: "background.neutral" }}>
              <TableCell sx={{ fontWeight: 600, pl: 1.5 }}>Group</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>
                n
              </TableCell>
              {runs.map((r) => (
                <TableCell
                  key={r.run_id}
                  align="right"
                  sx={{ fontWeight: 600 }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="flex-end"
                    gap={0.75}
                  >
                    <Box
                      sx={{
                        width: 3,
                        height: 14,
                        borderRadius: 1,
                        bgcolor: colors?.[r.run_id]?.solid,
                      }}
                    />
                    {r.label}
                  </Stack>
                </TableCell>
              ))}
              {baselineId &&
                candidates.map((r) => (
                  <TableCell
                    key={`d-${r.run_id}`}
                    align="right"
                    sx={{ fontWeight: 600 }}
                  >
                    {candidates.length > 1 ? `${r.label} ` : ""}Δ vs{" "}
                    {baselineLabel}
                  </TableCell>
                ))}
              {baselineId &&
                candidates.map((r) => (
                  <TableCell
                    key={`m-${r.run_id}`}
                    sx={{ fontWeight: 600, pr: 1.5 }}
                  >
                    {candidates.length > 1 ? `${r.label} ` : ""}Rows
                  </TableCell>
                ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {breakdown.groups.map((g) => {
              const isHighlight =
                highlight?.group_key === g.key &&
                highlight.metric === breakdown.metric;
              return (
                <TableRow
                  key={g.key}
                  sx={{
                    opacity: g.low_sample ? 0.55 : 1,
                    ...(isHighlight && {
                      bgcolor: alpha(theme.palette.red[500], 0.06),
                    }),
                  }}
                >
                  <TableCell sx={{ pl: 1.5 }}>
                    <Stack direction="row" alignItems="center" gap={1}>
                      {g.color ? (
                        <SplitChip
                          split={{ id: g.key, name: g.label, color: g.color }}
                          size="small"
                        />
                      ) : (
                        <Typography typography="s2">{g.label}</Typography>
                      )}
                      {g.low_sample && (
                        <Typography typography="s3" color="text.disabled">
                          Low sample
                        </Typography>
                      )}
                    </Stack>
                  </TableCell>
                  <TableCell align="right" sx={tabular}>
                    {g.n}
                  </TableCell>
                  {runs.map((r) => (
                    <TableCell key={r.run_id} align="right" sx={tabular}>
                      {formatValue(metric, g.values?.[r.run_id]?.value, {
                        agg,
                      })}
                    </TableCell>
                  ))}
                  {baselineId &&
                    candidates.map((r) => {
                      const vs = g.vs_baseline?.[r.run_id];
                      const tone = deltaTone(metric, vs?.delta, {
                        significant: true,
                      });
                      const color =
                        tone === "good"
                          ? theme.palette.green[600]
                          : tone === "bad"
                            ? theme.palette.red[600]
                            : theme.palette.text.secondary;
                      return (
                        <TableCell
                          key={`d-${r.run_id}`}
                          align="right"
                          sx={tabular}
                        >
                          <CustomTooltip
                            show
                            size="small"
                            placement="top"
                            arrow
                            title={`${r.label} vs ${baselineLabel} on ${vs?.n_shared ?? 0} shared rows in ${g.label}`}
                          >
                            <Box
                              component="span"
                              sx={{
                                color,
                                fontWeight: 600,
                                px: 0.75,
                                py: 0.25,
                                borderRadius: 0.5,
                                bgcolor:
                                  tone === "good"
                                    ? alpha(theme.palette.green[500], 0.12)
                                    : tone === "bad"
                                      ? alpha(theme.palette.red[500], 0.12)
                                      : "transparent",
                              }}
                            >
                              {formatDelta(metric, vs?.delta)}
                            </Box>
                          </CustomTooltip>
                        </TableCell>
                      );
                    })}
                  {baselineId &&
                    candidates.map((r) => {
                      const vs = g.vs_baseline?.[r.run_id];
                      return (
                        <TableCell key={`m-${r.run_id}`} sx={{ pr: 1.5 }}>
                          <MovementButtons
                            improved={vs?.improved ?? 0}
                            regressed={vs?.regressed ?? 0}
                            onSelect={(dir) =>
                              navigate(
                                cellHref(
                                  g.pill,
                                  breakdown.metric,
                                  dir,
                                  r.run_id,
                                ),
                              )
                            }
                          />
                        </TableCell>
                      );
                    })}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </AnalysisCard>
  );
};

BreakdownTable.propTypes = {
  breakdown: PropTypes.object,
  options: PropTypes.array.isRequired,
  metrics: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  baselineLabel: PropTypes.string,
  groupBy: PropTypes.string,
  onGroupBy: PropTypes.func.isRequired,
  onMetric: PropTypes.func.isRequired,
  cellHref: PropTypes.func.isRequired,
};

export default BreakdownTable;
