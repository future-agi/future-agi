import { useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useMemo } from "react";
import ReactApexChart from "react-apexcharts";

/**
 * Eval usage chart — dual axis:
 *   Left (bar):  Volume (call count per day)
 *   Right (line): Value (avg score for score type, pass rate for pass/fail)
 *
 * Backend zero-fills all dates. Data shape per point:
 *   { timestamp, calls, avg_latency_ms, avg_score, pass_count, fail_count }
 *
 * outputType: "pass_fail" | "percentage" | "deterministic"
 */
const UsageChart = ({ data, outputType = "pass_fail" }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const showValueAxis = outputType !== "deterministic";

  const { volumeData, valueData, valueLabel } = useMemo(() => {
    if (!data?.length) return { volumeData: [], valueData: [], valueLabel: "" };

    const vol = [];
    const val = [];

    for (const d of data) {
      const ts = new Date(d.timestamp).getTime();
      vol.push({ x: ts, y: d.calls || 0 });

      if (outputType === "pass_fail") {
        // Pass rate: pass / (pass + fail), or null if no evals
        const total =
          (d.passCount || d.pass_count || 0) +
          (d.failCount || d.fail_count || 0);
        val.push({
          x: ts,
          y: total > 0 ? (d.passCount || d.pass_count || 0) / total : null,
        });
      } else if (outputType === "percentage") {
        // Avg score 0-1
        val.push({ x: ts, y: d.avgScore ?? d.avg_score ?? null });
      }
    }

    const label = "Task Completion Rate";
    return { volumeData: vol, valueData: val, valueLabel: label };
  }, [data, outputType]);

  const series = useMemo(() => {
    const s = [{ name: "Volume", type: "bar", data: volumeData }];
    if (showValueAxis && valueData.some((p) => p.y !== null)) {
      s.push({ name: valueLabel, type: "line", data: valueData });
    }
    return s;
  }, [volumeData, valueData, valueLabel, showValueAxis]);

  const options = useMemo(() => {
    const hasTwoAxes = series.length > 1;

    return {
      chart: {
        type: "line",
        toolbar: { show: false },
        zoom: { enabled: false },
        background: "transparent",
        fontFamily: "'IBM Plex Sans', sans-serif",
      },
      colors: hasTwoAxes ? ["#5BE49B", "#7B56DB"] : ["#7B56DB"],
      stroke: { width: hasTwoAxes ? [0, 2.5] : [0], curve: "smooth" },
      plotOptions: {
        bar: { columnWidth: "55%", borderRadius: 2 },
      },
      xaxis: {
        type: "datetime",
        tickAmount: Math.min(volumeData.length, 12),
        labels: {
          style: { fontSize: "10px", colors: theme.palette.text.secondary },
          datetimeUTC: false,
          datetimeFormatter: { day: "MMM dd", month: "MMM ''yy" },
        },
        axisBorder: { show: false },
        axisTicks: { show: false },
      },
      yaxis: hasTwoAxes
        ? [
            {
              title: {
                text: "Volume",
                style: {
                  fontSize: "10px",
                  color: theme.palette.text.secondary,
                },
              },
              labels: {
                style: {
                  fontSize: "10px",
                  colors: theme.palette.text.secondary,
                },
                formatter: (v) => (v != null ? Math.round(v).toString() : ""),
              },
              min: 0,
              forceNiceScale: true,
            },
            {
              opposite: true,
              title: {
                text: valueLabel,
                style: {
                  fontSize: "10px",
                  color: theme.palette.text.secondary,
                },
              },
              labels: {
                style: {
                  fontSize: "10px",
                  colors: theme.palette.text.secondary,
                },
                formatter: (v) => (v != null ? `${Math.round(v * 100)}%` : ""),
              },
              min: 0,
              max: 1,
              tickAmount: 4,
            },
          ]
        : [
            {
              labels: {
                style: {
                  fontSize: "10px",
                  colors: theme.palette.text.secondary,
                },
                formatter: (v) => (v != null ? Math.round(v).toString() : ""),
              },
              min: 0,
              forceNiceScale: true,
            },
          ],
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
        xaxis: { lines: { show: false } },
        padding: { left: 8, right: 8 },
      },
      legend: {
        position: "top",
        horizontalAlign: "right",
        fontSize: "11px",
        labels: { colors: theme.palette.text.secondary },
        markers: { width: 8, height: 8, radius: 2 },
      },
      tooltip: {
        theme: isDark ? "dark" : "light",
        shared: true,
        intersect: false,
        x: { format: "dd MMM yyyy" },
        y: {
          formatter: (val, { seriesIndex }) => {
            if (val == null) return "—";
            if (seriesIndex === 0) return `${Math.round(val)} calls`;
            return `${(val * 100).toFixed(1)}%`;
          },
        },
      },
      markers: {
        size: hasTwoAxes ? [0, 3] : [0],
        strokeWidth: 0,
        hover: { size: 5 },
      },
      dataLabels: { enabled: false },
    };
  }, [volumeData, valueLabel, series.length, isDark, theme]);

  if (!volumeData.length) return null;

  return (
    <ReactApexChart
      options={options}
      series={series}
      type="line"
      height={150}
    />
  );
};

UsageChart.propTypes = {
  data: PropTypes.array.isRequired,
  outputType: PropTypes.string,
};

export default UsageChart;
