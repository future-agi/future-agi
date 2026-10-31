import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import RunWidgetChart from "./RunWidgetChart";
import { runMetricCatalog, runWidgetQuery } from "./runWidgetQuery";
import { useRunWidgetCalls } from "./useRunWidgetCalls";

/** A widget saved from the editor, drawn from this run's calls. */
export default function RunQueryWidget({ widget, executionId, runLabel }) {
  const { tasks, isLoading, isError } = useRunWidgetCalls(executionId);
  const result = useMemo(
    () =>
      runWidgetQuery({ ...widget.query, range: "this" }, tasks, {
        catalog: runMetricCatalog(tasks),
        runLabel,
      }),
    [widget.query, tasks, runLabel],
  );
  const visible = Array.isArray(widget.visible_series)
    ? new Set(widget.visible_series)
    : null;

  if (isLoading || isError)
    return (
      <Stack alignItems="center" justifyContent="center" sx={{ height: 260 }}>
        {isLoading ? (
          <CircularProgress size={22} />
        ) : (
          <Typography typography="s2" sx={{ color: "text.secondary" }}>
            The run&apos;s calls could not be loaded
          </Typography>
        )}
      </Stack>
    );
  return (
    <Box sx={{ height: 260, px: 1.5, pb: 1.5 }}>
      <RunWidgetChart
        series={result.series}
        buckets={result.buckets}
        chartType={widget.chart_type || "line"}
        axisConfig={widget.axis_config}
        visible={visible}
        compact
      />
    </Box>
  );
}
RunQueryWidget.propTypes = {
  widget: PropTypes.shape({
    query: PropTypes.object,
    chart_type: PropTypes.string,
    axis_config: PropTypes.object,
    visible_series: PropTypes.array,
  }).isRequired,
  executionId: PropTypes.string,
  runLabel: PropTypes.string,
};
