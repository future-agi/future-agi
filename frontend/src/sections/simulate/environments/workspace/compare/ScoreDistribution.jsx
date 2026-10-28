import PropTypes from "prop-types";
import { useState } from "react";
import ReactApexChart from "react-apexcharts";
import { useTheme } from "@mui/material/styles";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { distributionFor } from "./compareStats";

/**
 * One eval's scores across the compared runs, in ten bands. The table says
 * which scenarios moved; this says whether a run got better everywhere.
 */
export default function ScoreDistribution({ rows, runs, evals }) {
  const theme = useTheme();
  const [picked, setPicked] = useState(null);
  const evalId = evals.some((e) => e.id === picked) ? picked : evals[0]?.id;
  if (!evalId) return null;

  const runIds = runs.map((r) => r.id);
  const bands = distributionFor(rows, runIds, evalId);
  const peak = Math.max(1, ...bands.flatMap((b) => b.counts));
  const axisLabels = {
    style: { colors: theme.palette.text.secondary, fontSize: "12px" },
  };

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, pt: 1.5 }}
      >
        <Typography
          sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", flex: 1 }}
        >
          Score distribution
        </Typography>
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ display: { xs: "none", md: "flex" } }}
        >
          {runs.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={0.625}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: r.color,
                }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {r.label}
              </Typography>
            </Stack>
          ))}
        </Stack>
        <TextField
          select
          size="small"
          value={evalId}
          onChange={(e) => setPicked(e.target.value)}
          inputProps={{ "aria-label": "Evaluation" }}
          sx={{
            minWidth: 190,
            "& .MuiInputBase-input": { typography: "s2", py: 0.75 },
          }}
        >
          {evals.map((e) => (
            <MenuItem key={e.id} value={e.id} sx={{ typography: "s2" }}>
              {e.name}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      <Box sx={{ px: 1.5, pt: 0.5 }}>
        <ReactApexChart
          type="bar"
          height={140}
          series={runs.map((r, i) => ({
            name: r.label,
            data: bands.map((b) => b.counts[i]),
          }))}
          options={{
            chart: {
              toolbar: { show: false },
              fontFamily: theme.typography.fontFamily,
              background: "transparent",
            },
            theme: { mode: theme.palette.mode },
            colors: runs.map((r) => r.color),
            plotOptions: { bar: { columnWidth: "48%", borderRadius: 2 } },
            dataLabels: { enabled: false },
            legend: { show: false },
            grid: {
              borderColor: theme.palette.divider,
              strokeDashArray: 4,
              xaxis: { lines: { show: false } },
            },
            xaxis: {
              categories: bands.map((b) => b.label),
              axisBorder: { show: false },
              axisTicks: { show: false },
              labels: axisLabels,
            },
            yaxis: {
              min: 0,
              max: peak,
              tickAmount: Math.min(peak, 3),
              labels: { ...axisLabels, formatter: (v) => `${Math.round(v)}` },
            },
            tooltip: { y: { formatter: (v) => `${v} calls` } },
          }}
        />
      </Box>
    </Box>
  );
}
ScoreDistribution.propTypes = {
  rows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
};
