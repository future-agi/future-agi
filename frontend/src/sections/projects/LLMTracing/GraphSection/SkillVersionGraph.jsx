import React from "react";
import PropTypes from "prop-types";
import { Box, Typography, useTheme } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import { getExactAggregationReadState } from "src/utils/queryReadState";

export const skillVersionSeries = (result) =>
  result?.split_by === "skill_version" && getExactAggregationReadState(result) === "complete"
    ? result.series || []
    : [];

// Small multiples keep overlapping cohorts separate, on one shared scale.
// Version labels identify the panels; identity never relies on a hue cycle.
export default function SkillVersionGraph({ result }) {
  const theme = useTheme();
  const series = skillVersionSeries(result);
  const values = series.flatMap((item) => item.data.map((point) => point.value)).filter(Number.isFinite);
  const max = values.length ? Math.max(...values) : 0;
  return (
    <Box sx={{ px: 2, overflow: "auto", maxHeight: 600 }}>
      <Typography variant="s2">{result.metric_name}</Typography>
      <Typography variant="caption" color="text.secondary" display="block">
        Cohorts overlap; not additive. Associations, not proof that a skill caused an outcome.
      </Typography>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" }, gap: 2 }}>
        {series.map((item) => (
          <Box key={item.version_id} sx={{ minWidth: 0 }}>
            <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>{item.label}</Typography>
            {item.data.some((point) => point.incomplete > 0) && (
              <Typography variant="caption" color="text.secondary" display="block">
                Requests without a root span are not included in latency or failed-request rates.
              </Typography>
            )}
            <ReactApexChart
              type="line"
              height={180}
              series={[{ name: item.label, data: item.data.map((point) => ({ x: point.timestamp, y: point.value })) }]}
              options={{
                chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent" },
                theme: { mode: theme.palette.mode },
                colors: [theme.palette.text.secondary],
                stroke: { width: 2, curve: "straight" },
                markers: { size: 4 },
                grid: { borderColor: theme.palette.divider },
                xaxis: { type: "datetime" },
                yaxis: { min: 0, ...(max > 0 ? { max: max * 1.05 } : {}) },
                tooltip: { shared: true, intersect: false },
                dataLabels: { enabled: false },
                legend: { show: false },
              }}
            />
          </Box>
        ))}
      </Box>
      <Box component="details" sx={{ mt: 1, overflowX: "auto" }}>
        <summary>View values and sample counts</summary>
        <Box component="table" sx={{ width: "100%", textAlign: "left", typography: "caption" }}>
          <caption>{result.metric_name} by skill version</caption>
          <thead><tr><th scope="col">Version</th><th scope="col">Time</th><th scope="col">Value</th><th scope="col">Traces</th><th scope="col">Missing roots</th></tr></thead>
          <tbody>{series.flatMap((item) => item.data.map((point) => (
            <tr key={`${item.version_id}:${point.timestamp}`}>
              <th scope="row">{item.label}</th><td>{point.timestamp}</td><td>{point.value ?? "—"}</td>
              <td>{point.traces}{point.traces < 30 ? " · low sample" : ""}</td><td>{point.incomplete}</td>
            </tr>
          )))}</tbody>
        </Box>
      </Box>
    </Box>
  );
}

SkillVersionGraph.propTypes = {
  result: PropTypes.shape({
    metric_name: PropTypes.string.isRequired,
    split_by: PropTypes.string,
    series: PropTypes.arrayOf(PropTypes.shape({
      version_id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      data: PropTypes.arrayOf(PropTypes.shape({
        timestamp: PropTypes.string.isRequired,
        value: PropTypes.number,
        traces: PropTypes.number.isRequired,
        incomplete: PropTypes.number.isRequired,
      })).isRequired,
    })),
  }).isRequired,
};
