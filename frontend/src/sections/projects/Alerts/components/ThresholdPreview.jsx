import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import AlertsChart from "./AlertsChart";

export default function ThresholdPreview({ definition, monitorId }) {
  const theme = useTheme();
  const key = JSON.stringify([monitorId, definition]);
  const requestRef = useRef(null);
  const [state, setState] = useState(null);
  const current = state?.key === key ? state : null;
  const result = current?.result;

  useEffect(() => {
    // Form edits invalidate the count immediately and cancel the old request.
    return () => {
      const controller = requestRef.current;
      controller?.abort();
      setState((previous) =>
        previous?.request === controller && previous?.loading ? null : previous,
      );
    };
  }, [key]);

  const preview = async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setState({ key, request: controller, loading: true });
    try {
      const response = await axios.post(
        `${endpoints.project.createMonitor}${monitorId ? `${monitorId}/` : ""}preview/`,
        definition,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        setState({ key, result: response.data.result });
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        const body = error.response?.data;
        const detail = body?.details ?? body?.result ?? body?.detail;
        setState({
          key,
          error:
            typeof detail === "string"
              ? detail
              : detail && typeof detail === "object"
                ? Object.entries(detail)
                    .map(
                      ([field, messages]) =>
                        `${field}: ${[].concat(messages).join(" ")}`,
                    )
                    .join(" ")
                : "Preview unavailable. Check the definition and try again.",
        });
      }
    }
  };

  const series = result?.series ?? [];
  const evaluated = result?.evaluated_count ?? result?.bucket_count ?? 0;
  const chartSeries = [
    {
      name: "Metric",
      data: series.map((point) => ({ x: point.timestamp, y: point.value })),
    },
    {
      name: "Critical threshold",
      data: series.map((point) => ({
        x: point.timestamp,
        y: point.critical_threshold,
      })),
    },
    {
      name: "Warning threshold",
      data: series.map((point) => ({
        x: point.timestamp,
        y: point.warning_threshold,
      })),
    },
  ];
  const ink = theme.palette.text.primary;
  const options = {
    chart: {
      type: "line",
      animations: { enabled: false },
      toolbar: { show: false },
      foreColor: ink,
    },
    // Identity does not rely on hue: metric is solid, thresholds are dashed.
    colors: [
      ink,
      theme.palette.mode === "dark"
        ? theme.palette.error.light
        : theme.palette.error.main,
      theme.palette.mode === "dark"
        ? theme.palette.warning.light
        : theme.palette.warning.dark,
    ],
    stroke: { width: 2, curve: "straight", dashArray: [0, 6, 2] },
    markers: {
      size: 0,
      hover: { size: 4 },
      discrete: series.flatMap((point, index) =>
        ["critical", "warning"].includes(point.status)
          ? [
              {
                seriesIndex: 0,
                dataPointIndex: index,
                size: 4,
                fillColor: ink,
                strokeColor: theme.palette.background.paper,
              },
            ]
          : [],
      ),
    },
    xaxis: { type: "datetime", crosshairs: { show: true } },
    yaxis: {
      labels: {
        formatter: (value) =>
          value == null ? "" : Number(value.toFixed(3)).toLocaleString(),
      },
    },
    tooltip: {
      shared: true,
      intersect: false,
      theme: theme.palette.mode,
    },
    legend: { show: true, position: "bottom" },
    grid: { borderColor: theme.palette.divider },
    noData: { text: "No data" },
  };

  return (
    <Stack
      component="section"
      aria-label="Historical threshold preview"
      gap={2}
      sx={{
        p: 3,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={2}
      >
        <Box>
          <Typography variant="subtitle1">Historical preview</Typography>
          <Typography variant="body2" color="text.secondary">
            Try these thresholds before saving. Up to 7 days and 168 complete
            intervals; no notifications are sent.
          </Typography>
        </Box>
        <Button
          type="button"
          variant="outlined"
          onClick={preview}
          disabled={current?.loading || !definition?.project}
        >
          {current?.loading ? "Previewing…" : "Preview"}
        </Button>
      </Stack>
      {current?.error && <Alert severity="error">{current.error}</Alert>}
      {result && (
        <Stack gap={2} aria-live="polite">
          {result.reason === "interval_exceeds_window" ? (
            <Typography>
              Not enough history: this metric needs more than the 7-day preview
              window.
            </Typography>
          ) : series.length === 0 ? (
            <Typography>
              No complete intervals with data in this window.
            </Typography>
          ) : (
            <>
              <Typography variant="subtitle1">
                {evaluated > 0
                  ? `Would have fired in ${result.fire_count} of ${evaluated} intervals`
                  : "Insufficient history to estimate fires in this window."}
              </Typography>
              {evaluated < series.length && (
                <Typography variant="body2" color="text.secondary">
                  {series.length - evaluated} intervals lack sufficient history
                  and are excluded from the count.
                </Typography>
              )}
              <Box sx={{ minWidth: 0 }}>
                <AlertsChart series={chartSeries} options={options} />
              </Box>
              <Box component="details" sx={{ mt: 2 }}>
                <Box component="summary" sx={{ cursor: "pointer" }}>
                  View interval values
                </Box>
                <Box sx={{ overflowX: "auto", maxHeight: 260 }}>
                  <Box
                    component="table"
                    sx={{
                      width: "100%",
                      textAlign: "left",
                      "& th, & td": {
                        p: 1,
                        borderBottom: 1,
                        borderColor: "divider",
                      },
                    }}
                  >
                    <caption>
                      Historical metric values and effective thresholds
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Interval start</th>
                        <th scope="col">Value</th>
                        <th scope="col">Critical threshold</th>
                        <th scope="col">Warning threshold</th>
                        <th scope="col">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {series.map((point) => (
                        <tr key={point.timestamp}>
                          <td>{new Date(point.timestamp).toLocaleString()}</td>
                          <td>{point.value ?? "No data"}</td>
                          <td>{point.critical_threshold ?? "—"}</td>
                          <td>{point.warning_threshold ?? "—"}</td>
                          <td>{point.status.replaceAll("_", " ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </Box>
                </Box>
              </Box>
            </>
          )}
          <Typography variant="caption" color="text.secondary">
            {new Date(result.window_start).toLocaleString()} to{" "}
            {new Date(result.window_end).toLocaleString()}
            {result.window_limited
              ? ". Window shortened to retain the chosen interval within the bucket limit."
              : "."}{" "}
            Historical estimates use complete intervals, not notification
            deliveries. Intervals without metric values are excluded.
          </Typography>
        </Stack>
      )}
    </Stack>
  );
}

ThresholdPreview.propTypes = {
  definition: PropTypes.object.isRequired,
  monitorId: PropTypes.string,
};
