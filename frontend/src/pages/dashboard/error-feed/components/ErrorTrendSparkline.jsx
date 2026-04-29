import React, { useEffect, useMemo, useRef } from "react";
import ApexCharts from "apexcharts";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { format } from "date-fns";

function toDateKey(d) {
  // UTC date key YYYY-MM-DD — avoids timezone mismatches
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

function padToRange(data, days = 14) {
  if (!data?.length) return [];
  const now = new Date();
  const buckets = new Map();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i),
    );
    buckets.set(toDateKey(d), { timestamp: d.getTime(), value: 0 });
  }
  for (const pt of data) {
    const key = toDateKey(new Date(pt.timestamp));
    if (buckets.has(key)) {
      buckets.get(key).value += pt.value;
    }
  }
  return Array.from(buckets.values());
}

function ErrorTrendSparkline({ data }) {
  const chartRef = useRef(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const barColor = isDark ? "#52525b" : "#C8C4D4";
  const tooltipBg = isDark ? "#1c1c1e" : "#ffffff";
  const tooltipBorder = isDark ? "#3f3f46" : "#e4e4e7";
  const tooltipText = isDark ? "#f4f4f5" : "#18181b";
  const tooltipSub = isDark ? "#a1a1aa" : "#71717a";

  const paddedData = useMemo(() => padToRange(data), [data]);

  useEffect(() => {
    if (!paddedData?.length || !chartRef.current) return;

    const chartOptions = {
      chart: {
        type: "area",
        height: 36,
        width: "100%",
        sparkline: { enabled: true },
        toolbar: { show: false },
        background: "transparent",
        animations: { enabled: false },
      },
      stroke: {
        curve: "straight",
        width: 1.5,
      },
      fill: {
        type: "gradient",
        gradient: {
          shadeIntensity: 1,
          opacityFrom: isDark ? 0.3 : 0.4,
          opacityTo: 0.05,
          stops: [0, 100],
        },
      },
      states: {
        hover: { filter: { type: "lighten", value: 0.1 } },
        active: { filter: { type: "none" } },
      },
      colors: [barColor],
      series: [
        {
          name: "Events",
          data: paddedData.map((item) => ({
            x: item.timestamp,
            y: item.value,
          })),
        },
      ],
      dataLabels: { enabled: false },
      tooltip: {
        enabled: true,
        shared: false,
        followCursor: true,
        custom: ({ series, seriesIndex, dataPointIndex, w }) => {
          const val = series[seriesIndex][dataPointIndex];
          const raw = w.globals.seriesX[seriesIndex][dataPointIndex];
          const dateStr = raw
            ? format(new Date(raw), "MMM d, yyyy h:mm a")
            : "";
          return `
            <div style="
              background:${tooltipBg};
              border:1px solid ${tooltipBorder};
              border-radius:8px;
              padding:8px 12px;
              font-family:Inter,sans-serif;
              min-width:160px;
              box-shadow:0 4px 12px rgba(0,0,0,0.25);
            ">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span style="
                  width:8px;height:8px;border-radius:50%;
                  background:${barColor};display:inline-block;flex-shrink:0;
                "></span>
                <span style="font-size:12px;color:${tooltipText};font-weight:500;">Events</span>
                <span style="font-size:12px;color:${tooltipText};font-weight:700;margin-left:auto;">
                  ${val?.toLocaleString() ?? "—"}
                </span>
              </div>
              <div style="font-size:11px;color:${tooltipSub};">${dateStr}</div>
            </div>
          `;
        },
      },
      xaxis: { type: "datetime" },
      yaxis: { show: false, min: 0 },
      grid: { padding: { top: 2, bottom: 0, left: 0, right: 0 } },
    };

    const chart = new ApexCharts(chartRef.current, chartOptions);
    chart.render();

    return () => {
      try {
        chart.destroy();
      } catch {
        /* ignore */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    data,
    barColor,
    isDark,
    tooltipBg,
    tooltipBorder,
    tooltipText,
    tooltipSub,
  ]);

  if (!data?.length)
    return <span style={{ color: "#938FA3", fontSize: 12 }}>—</span>;

  return (
    <Box sx={{ width: "100%" }}>
      <div
        ref={chartRef}
        style={{
          width: "100%",
          height: 36,
          display: "flex",
          alignItems: "center",
        }}
      />
    </Box>
  );
}

ErrorTrendSparkline.propTypes = {
  data: PropTypes.array,
};

export default React.memo(ErrorTrendSparkline);
