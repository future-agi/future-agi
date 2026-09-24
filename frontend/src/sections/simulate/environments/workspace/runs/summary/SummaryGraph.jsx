import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";
import { Box } from "@mui/material";
import ReactApexChart from "react-apexcharts";

// The eval-score trend: one line per applied eval, its score across the runs in
// chronological order (a broken line where a run never scored that eval). Ported
// from the designer's RunsSummary graph — a flat 0–100 line chart with discrete
// per-run markers, no legend (the legend lives above the chart) and no toolbar.
export default function SummaryGraph({ categories, series }) {
  const theme = useTheme();

  return (
    <Box sx={{ px: 0, pt: 0.5, pb: 0.5, width: "100%" }}>
      <ReactApexChart
        type="line"
        height={160}
        width="100%"
        series={series.map((s) => ({ name: s.name, data: s.data }))}
        options={{
          chart: {
            toolbar: { show: false },
            zoom: { enabled: false },
            animations: { enabled: false },
            fontFamily: theme.typography.fontFamily,
            background: "transparent",
            parentHeightOffset: 0,
            sparkline: { enabled: false },
          },
          theme: { mode: theme.palette.mode },
          colors: series.map((s) => s.color),
          stroke: { width: 2, curve: "straight" },
          markers: {
            size: 0,
            strokeWidth: 0,
            hover: { size: 5 },
            discrete: series.flatMap((s, si) =>
              categories.map((_, di) => ({
                seriesIndex: si,
                dataPointIndex: di,
                fillColor: s.color,
                strokeColor: s.color,
                size: 4,
                shape: "circle",
              })),
            ),
          },
          legend: { show: false },
          dataLabels: { enabled: false },
          grid: {
            borderColor: theme.palette.divider,
            strokeDashArray: 4,
            xaxis: { lines: { show: false } },
            padding: { left: 0, right: 0, top: 0, bottom: 0 },
          },
          xaxis: {
            categories,
            axisBorder: { show: false },
            axisTicks: { show: false },
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
              rotate: 0,
              hideOverlappingLabels: true,
              trim: false,
            },
            tooltip: { enabled: false },
          },
          yaxis: {
            min: 0,
            max: 100,
            tickAmount: 2,
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
              formatter: (v) => `${Math.round(v)}`,
            },
          },
          tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: (v) => (v == null ? "—" : `${v}%`) },
          },
        }}
      />
    </Box>
  );
}

SummaryGraph.propTypes = {
  categories: PropTypes.arrayOf(PropTypes.string).isRequired,
  series: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      color: PropTypes.string,
      data: PropTypes.arrayOf(PropTypes.number),
    }),
  ).isRequired,
};
