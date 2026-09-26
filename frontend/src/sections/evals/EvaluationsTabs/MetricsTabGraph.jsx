import { Alert, Box, Button, Grid, Skeleton, useTheme } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import EvaluationDateTimeRangePicker from "../EvalsChartsView/EvaluationDateTimeRangePicker";
import ReactApexChart from "react-apexcharts";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { endOfToday, sub } from "date-fns";
import { useLocation, useParams } from "react-router";
import { getRandomId } from "src/utils/utils";
import PropTypes from "prop-types";
import { readEvalMetrics } from "../utils/eval_metrics_read";

const MetricsTabGraph = ({ setDateFilter }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [selectedInterval] = useState("Day");
  const chartRef = useRef(null);
  const chartId = useMemo(() => getRandomId(), []);

  const { evalId } = useParams();
  const { state } = useLocation();

  const convertToISO = (dateArray) => {
    return dateArray.map((date) => new Date(date).toISOString());
  };

  const [parentDateFilter, setParentDateFilter] = useState(() => {
    const defaultDates =
      state?.report?.startDate && state?.report?.endDate
        ? [state.report.startDate, state.report.endDate]
        : [sub(new Date(), { days: 30 }), endOfToday()];

    return convertToISO(defaultDates);
  });

  useEffect(() => {
    trackEvent(Events.durationSelected, {
      [PropertyName.formFields]: { dateRange: parentDateFilter },
    });
  }, [parentDateFilter, selectedInterval]);

  useEffect(() => {
    trackEvent(Events.timeframeSelected, {
      [PropertyName.click]: selectedInterval,
    });
  }, [selectedInterval]);

  const [zoomRange] = useState([null, null]);

  const filters = useMemo(() => {
    const currentFilter = {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: convertToISO(parentDateFilter),
      },
    };
    if (setDateFilter) {
      setDateFilter(currentFilter);
    }
    return [currentFilter];
  }, [parentDateFilter, setDateFilter]);

  const {
    data: graphData,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["get-graph-data", evalId, filters, selectedInterval],
    queryFn: ({ signal }) =>
      readEvalMetrics(
        ({ signal: requestSignal, timeout }) =>
          axios.get(endpoints.develop.eval.getEvalMetrics, {
            signal: requestSignal,
            timeout,
            params: {
              eval_template_id: evalId,
              filters: JSON.stringify(filters),
            },
          }),
        signal,
      ),
    enabled: Boolean(evalId),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const chartData = useMemo(() => {
    const countGraphData =
      graphData?.result?.api_call_count?.count_graph_data || [];
    const avgGraphData = graphData?.result?.average?.avg_graph_data || [];

    const primaryData = [];
    const trafficData = [];

    for (const item of countGraphData || []) {
      const avgValue = avgGraphData.find(
        (temp) => temp.timestamp === item.timestamp,
      );
      primaryData.push({ x: item.timestamp, y: item.value });
      trafficData.push({ x: item.timestamp, y: avgValue.value });
    }

    const lineSeriesName = `No of Calls`;
    const barSeriesName = `Value`;

    return {
      series: [
        {
          name: lineSeriesName,
          type: "line",
          data: primaryData,
          color: "#CC91EA",
        },
        {
          name: barSeriesName,
          type: "column",
          data: trafficData,
          color: isDark ? "#3f3f46" : "#F0ECFF",
        },
      ],
      options: {
        chart: {
          id: chartId,
          height: 200,
          type: "line",
          stacked: false,
          background: "transparent",
          foreColor: isDark ? "#a1a1aa" : undefined,
          toolbar: {
            show: false,
          },
        },
        theme: {
          mode: isDark ? "dark" : "light",
        },
        stroke: {
          width: 2,
          curve: "smooth",
        },
        plotOptions: {
          bar: {
            columnWidth: "50%",
          },
        },
        dataLabels: {
          enabledOnSeries: [1],
        },
        xaxis: {
          type: "datetime",
        },
        yaxis: [
          {
            seriesName: lineSeriesName,
            title: {
              text: lineSeriesName,
            },
            opposite: false,
          },
          {
            seriesName: barSeriesName,
            title: {
              text: barSeriesName,
            },
            opposite: true,
          },
        ],
        tooltip: {
          theme: isDark ? "dark" : "light",
          shared: true,
          intersect: false,
        },
        grid: {
          borderColor: isDark ? "#27272a" : undefined,
        },
        legend: {
          position: "top",
          horizontalAlign: "left",
        },
      },
    };
  }, [graphData?.result, isDark, chartId]);

  return (
    <Box sx={{ paddingY: 1, height: "100%", overflow: "hidden" }}>
      {/* Date Range Picker and Button Group */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 1.5,
          marginTop: 1,
        }}
      >
        <EvaluationDateTimeRangePicker
          setParentDateFilter={setParentDateFilter}
          zoomRange={zoomRange}
        />
      </Box>

      {/* Chart Categories and Charts */}
      <Box sx={{ height: "90%", overflowY: "auto" }}>
        {isError && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => refetch()}>
                Retry
              </Button>
            }
            sx={{ mb: 1 }}
          >
            {graphData
              ? "Could not refresh evaluation metrics. The previous graph is still shown."
              : error?.message || "Could not load evaluation metrics."}
          </Alert>
        )}
        {isLoading ? (
          <>
            <Skeleton variant="text" width={150} height={40} />
            <Grid container>
              <Grid item xs={12}>
                <Skeleton variant="rectangular" width="100%" height={200} />
              </Grid>
            </Grid>
          </>
        ) : graphData ? (
          <>
            <ReactApexChart
              ref={chartRef}
              options={chartData.options}
              series={chartData.series}
              type="line"
              height={150} // Adjust height as needed
            />
          </>
        ) : null}
      </Box>
    </Box>
  );
};

export default MetricsTabGraph;

MetricsTabGraph.propTypes = {
  setDateFilter: PropTypes.func,
};
