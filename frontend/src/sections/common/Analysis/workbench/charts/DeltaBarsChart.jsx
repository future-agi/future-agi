// Delta bars (W5-6 B5): per metric, the candidate's Δ vs the baseline with
// its 95% interval, from the same R5 analysis the Summary prints
// (metrics[].vs_baseline: delta, ci95, significant). Grey when the
// interval holds 0. Each metric has its own scale (units differ); evals in
// pts share one.
import React from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, Select, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { ChartFrame, useChartTheme } from "./chartKit";
import { deltaBarRows } from "./deltaBarsModel";

const BAR_W = 360;

const DeltaBarsChart = ({
  analysis,
  candidateId,
  candidates,
  onCand,
  baseLabel,
  candLabel,
}) => {
  const t = useChartTheme();
  const theme = useTheme();
  const rows = deltaBarRows(analysis, candidateId);
  return (
    <ChartFrame
      title={`Delta bars · ${candLabel} vs ${baseLabel}`}
      legend={
        candidates.length > 1 ? (
          <Select
            size="small"
            value={candidateId ?? ""}
            onChange={(e) => onCand(e.target.value)}
            aria-label="Candidate run"
            sx={{ height: 28, typography: "s2" }}
          >
            {candidates.map((c) => (
              <MenuItem key={c.id} value={c.id} sx={{ typography: "s1" }}>
                {c.label}
              </MenuItem>
            ))}
          </Select>
        ) : null
      }
      empty={!rows.length}
    >
      <Stack gap={0.5} sx={{ height: "100%", overflowY: "auto", pr: 1 }}>
        <Typography typography="s3" color="text.secondary">
          Δ vs {baseLabel} with its 95% interval · grey = not significant
        </Typography>
        {rows.map((r) => {
          const scale = BAR_W / 2 / r.reach;
          const x = (v) => BAR_W / 2 + v * scale;
          const color = r.significant
            ? r.better
              ? theme.palette.success.main
              : theme.palette.error.main
            : t.muted;
          return (
            <Stack
              key={r.key}
              direction="row"
              alignItems="center"
              gap={1}
              data-testid="delta-bar"
              data-metric={r.name}
              data-delta={r.delta}
              data-ci={r.ci95 ?? ""}
              data-significant={r.significant ? "1" : "0"}
            >
              <Typography
                typography="s2"
                noWrap
                title={r.name}
                sx={{ width: 150, flexShrink: 0 }}
              >
                {r.name}
              </Typography>
              <Box
                component="svg"
                viewBox={`0 0 ${BAR_W} 22`}
                sx={{ width: BAR_W, height: 22, flexShrink: 0 }}
              >
                <line
                  x1={BAR_W / 2}
                  x2={BAR_W / 2}
                  y1={0}
                  y2={22}
                  stroke={t.axis}
                />
                <rect
                  x={Math.min(x(0), x(r.shown))}
                  y={5}
                  width={Math.max(1, Math.abs(x(r.shown) - x(0)))}
                  height={12}
                  fill={color}
                  fillOpacity={r.significant ? 0.85 : 0.45}
                />
                {r.ciShown != null && (
                  <g stroke={t.ink} strokeWidth={1.5}>
                    <line
                      x1={x(r.shown - r.ciShown)}
                      x2={x(r.shown + r.ciShown)}
                      y1={11}
                      y2={11}
                    />
                    <line
                      x1={x(r.shown - r.ciShown)}
                      x2={x(r.shown - r.ciShown)}
                      y1={6}
                      y2={16}
                    />
                    <line
                      x1={x(r.shown + r.ciShown)}
                      x2={x(r.shown + r.ciShown)}
                      y1={6}
                      y2={16}
                    />
                  </g>
                )}
              </Box>
              <Typography
                typography="s2"
                noWrap
                sx={{
                  fontVariantNumeric: "tabular-nums",
                  color: r.significant ? color : "text.secondary",
                }}
              >
                {r.text}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
    </ChartFrame>
  );
};

DeltaBarsChart.propTypes = {
  analysis: PropTypes.object,
  candidateId: PropTypes.string,
  candidates: PropTypes.array.isRequired,
  onCand: PropTypes.func.isRequired,
  baseLabel: PropTypes.string,
  candLabel: PropTypes.string,
};

export default DeltaBarsChart;
