import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { heatColor } from "src/sections/common/Metrics/metricFormat";
import { fmtCellTip, fmtCi, fmtValue } from "../workbenchFormat";
import { ChartFrame } from "./chartKit";

/**
 * Heatmap (R2-SPEC §6.3.3 #5): groups × columns as colour (the Matrix in
 * miniature, the repo's score heat); a cell opens the Matrix filtered to it.
 */
const HeatmapChart = ({ pivot, groups, measure, onOpenCell, title }) => {
  const cols = pivot?.columns ?? [];
  const values = (groups ?? [])
    .flatMap((g) => cols.map((c) => pivot.cells[g]?.[c.key]?.value))
    .filter((v) => v != null);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const def =
    measure?.kind === "rate"
      ? { output_type: "pass_fail", higher_is_better: true }
      : {
          output_type: "score",
          min,
          max: max === min ? min + 1 : max,
          higher_is_better: measure?.higherIsBetter !== false,
        };
  const empty = !values.length || pivot?.failedEval;
  return (
    <ChartFrame title={title} empty={empty}>
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `minmax(120px, 200px) repeat(${cols.length}, minmax(64px, 1fr))`,
            gap: "2px",
          }}
        >
          <Box />
          {cols.map((c) => (
            <Typography
              key={c.key}
              typography="s2"
              fontWeight={600}
              align="center"
              noWrap
              sx={{ py: 0.5 }}
            >
              {c.label}
            </Typography>
          ))}
          {(groups ?? []).map((g) => (
            <React.Fragment key={g}>
              <Typography
                typography="s2"
                noWrap
                title={g}
                sx={{ alignSelf: "center", pr: 1 }}
              >
                {g}
              </Typography>
              {cols.map((c) => {
                const cell = pivot.cells[g]?.[c.key];
                const v = cell?.value;
                return (
                  <CustomTooltip
                    key={c.key}
                    show
                    size="small"
                    placement="top"
                    arrow
                    title={[
                      `${c.label} · ${g}`,
                      cell?.rows
                        ? fmtCellTip(measure, cell)
                        : "No rows in this group.",
                      fmtCi(measure, cell?.ci),
                      cell?.rows ? "Click to open in the Matrix" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  >
                    <Box
                      role="button"
                      tabIndex={cell?.rows ? 0 : -1}
                      onClick={() => cell?.rows && onOpenCell(g, c, cell)}
                      onKeyDown={(e) =>
                        e.key === "Enter" &&
                        cell?.rows &&
                        onOpenCell(g, c, cell)
                      }
                      sx={{
                        height: 34,
                        borderRadius: 0.5,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        cursor: cell?.rows ? "pointer" : "default",
                        bgcolor:
                          v == null
                            ? "action.hover"
                            : heatColor(def, v) ?? "action.hover",
                        "&:hover": {
                          outline: (t) => `2px solid ${t.palette.text.primary}`,
                          outlineOffset: -2,
                        },
                      }}
                    >
                      <Typography
                        typography="s2"
                        color={v == null ? "text.disabled" : "text.primary"}
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        {fmtValue(measure, v)}
                      </Typography>
                    </Box>
                  </CustomTooltip>
                );
              })}
            </React.Fragment>
          ))}
        </Box>
      </Box>
    </ChartFrame>
  );
};

HeatmapChart.propTypes = {
  pivot: PropTypes.object,
  groups: PropTypes.array,
  measure: PropTypes.object,
  onOpenCell: PropTypes.func.isRequired,
  title: PropTypes.node,
};

export default HeatmapChart;
