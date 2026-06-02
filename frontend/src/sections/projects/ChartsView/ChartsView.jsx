import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Grid,
  Skeleton,
  Typography,
  useTheme,
  Button,
} from "@mui/material";
import {
  StyledIntervalSelect,
  StyledIntervalMenuItem,
} from "../SharedComponents";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { Helmet } from "react-helmet-async";
import ChartsGenerator from "./ChartsGenerator";
import ChartsDateTimeRangePicker from "./ChartsDateTimeRangePicker";
import EvaluationCharts from "./EvaluationCharts";
import { useChartsViewContext } from "./ChartsViewProvider/ChartsViewContext";
import { normalizeTimestamp } from "./ChartsViewProvider/common";
import SvgColor from "src/components/svg-color";

const DateRangeButtonOptions = [
  { title: "Hour", value: "hour" },
  { title: "Day", value: "day" },
  { title: "Week", value: "week" },
  { title: "Month", value: "month" },
];
const metricUnits = {
  latency: "ms",
  tokens: "",
  traffic: "spans",
  cost: "$",
};

const metricYLabels = {
  latency: "Latency in (ms)",
  tokens: "Tokens",
  traffic: "Traffic in (spans)",
  cost: "Cost in ($)",
};

const ChartsView = () => {
  const {
    selectedInterval,
    setSelectedInterval,
    parentDateFilter,
    setParentDateFilter,
    isMoreThan7Days,
    filters,
    handleZoomChange,
    zoomRange,
    isLessThan90Days,
  } = useChartsViewContext();

  const [, setIsData] = useState(false);
  const customDatePickerAnc = useRef(null);
  const [dateOption, setDateOption] = useState("30D");

  const { observeId } = useParams();
  const queryClient = useQueryClient();
  const theme = useTheme();
  const { setHeaderConfig } = useObserveHeader();

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

  const handleDataOptionChange = (option) => {
    if (option === "Hour" && isMoreThan7Days) {
      setSelectedInterval("Day");
      return;
    }
    setSelectedInterval(option);
  };

  useEffect(() => {
    if (isMoreThan7Days && selectedInterval === "Hour") {
      setSelectedInterval("Day");
    } else if (isLessThan90Days && selectedInterval === "Month") {
      setSelectedInterval("Week");
    }
  }, [isMoreThan7Days, selectedInterval, isLessThan90Days]);

  const navigate = useNavigate();

  // const {
  //   data: socketGraphData,
  //   isConnected,
  //   error: socketError,
  //   isLoading: socketLoading
  // } = useChartWebSocket(observeId, filters, selectedInterval);

  // const evaluations =
  //   socketGraphData?.evaluations &&
  //     typeof socketGraphData?.evaluations === "object" &&
  //     !Array.isArray(socketGraphData?.evaluations)
  //     ? socketGraphData?.evaluations
  //     : null;

  // const evaluationCharts = evaluations
  //   ? Object.entries(evaluations).map(([id, evaluation]) => ({
  //     id: `chart-${id}`,
  //     label: evaluation.name,
  //     unit: "%",
  //     yAxisLabel: `${evaluation.name} in (%)`,
  //     isEvaluationChart: true,
  //     series: evaluation?.data?.length > 0 && evaluation?.data?.map((item) => ({
  //       name: item?.name,
  //       data: item?.value?.length > 0 && item?.value?.map((data) => ({
  //         x: new Date(data.timestamp).getTime(),
  //         y: data.value,
  //       })),
  //     }))
  //     ,
  //   }))
  //   : [];

  // const chartCategories = useMemo(() => {
  //   // Check if socketGraphData and systemMetrics exist and are non-empty
  //   if (socketGraphData?.systemMetrics && Object.keys(socketGraphData.systemMetrics).length > 0) {
  //     setIsData(true)
  //     return [
  //       {
  //         label: "System Metrics",
  //         charts: [
  //           {
  //             id: "chart-1",
  //             label: "Latency",
  //             unit: metricUnits.latency,
  //             yAxisLabel: metricYLabels.latency,
  //             isEvaluationChart: false,
  //             series: [
  //               {
  //                 name: "Latency",
  //                 data: socketGraphData.systemMetrics.latency?.map((item) => ({
  //                   x: new Date(item.timestamp).getTime(),
  //                   y: item.latency,
  //                 })) || [],
  //               },
  //             ],
  //           },
  //           {
  //             id: "chart-2",
  //             label: "Tokens",
  //             unit: metricUnits.tokens,
  //             yAxisLabel: metricYLabels.tokens,
  //             isEvaluationChart: false,
  //             series: [
  //               {
  //                 name: "Tokens",
  //                 data: socketGraphData.systemMetrics.tokens?.map((item) => ({
  //                   x: new Date(item.timestamp).getTime(),
  //                   y: item.tokens,
  //                 })) || [],
  //               },
  //             ],
  //           },
  //           {
  //             id: "chart-3",
  //             label: "Traffic",
  //             unit: metricUnits.traffic,
  //             yAxisLabel: metricYLabels.traffic,
  //             isEvaluationChart: false,
  //             series: [
  //               {
  //                 name: "Traffic",
  //                 data: socketGraphData.systemMetrics.traffic?.map((item) => ({
  //                   x: new Date(item.timestamp).getTime(),
  //                   y: item.traffic,
  //                 })) || [],
  //               },
  //             ],
  //           },
  //           {
  //             id: "chart-4",
  //             label: "Cost",
  //             unit: metricUnits.cost,
  //             yAxisLabel: metricYLabels.cost,
  //             isEvaluationChart: false,
  //             series: [
  //               {
  //                 name: "Cost",
  //                 data: socketGraphData.systemMetrics.cost?.map((item) => ({
  //                   x: new Date(item.timestamp).getTime(),
  //                   y: item.cost,
  //                 })) || [],
  //               },
  //             ],
  //           },
  //         ],
  //       },
  //     ];
  //   }

  //   return [];
  // }, [socketGraphData]);

  // if (evaluationCharts.length > 0) {
  //   const existingCategoryIndex = chartCategories.findIndex(category => category.label === "Evaluation Metrics");

  //   if (existingCategoryIndex !== -1) {
  //     chartCategories[existingCategoryIndex].charts = evaluationCharts;
  //   } else {
  //     chartCategories.push({
  //       label: "Evaluation Metrics",
  //       charts: evaluationCharts,
  //     });
  //   }
  // }
  const {
    data: graphData,
    isLoading,
    refetch: refetchSystemMetrics,
    isRefetching: isRefetchingSystemMetrics,
  } = useQuery({
    queryKey: ["get-graph-data", observeId, filters, selectedInterval],
    queryFn: async () => {
      const response = await axios.get(endpoints.project.showCharts(), {
        params: {
          project_id: observeId,
          filters: JSON.stringify(filters),
          interval: selectedInterval?.toLowerCase(),
        },
      });
      return response.data;
    },
    enabled: Boolean(observeId) && filters?.length > 0,
  });

  const chartCategories = useMemo(() => {
    if (
      graphData?.result?.system_metrics &&
      Object.keys(graphData?.result?.system_metrics)?.length > 0
    ) {
      setIsData(true);
      return [
        {
          label: "System Metrics",
          charts: [
            {
              id: "chart-1",
              label: "Latency",
              unit: metricUnits?.latency,
              yAxisLabel: metricYLabels?.latency,
              isEvaluationChart: false,
              series: [
                {
                  name: "Latency",
                  data:
                    graphData?.result?.system_metrics?.latency?.map((item) => ({
                      x: normalizeTimestamp(item?.timestamp),
                      y: item?.latency,
                    })) || [],
                },
              ],
            },
            {
              id: "chart-2",
              label: "Tokens",
              unit: metricUnits?.tokens,
              yAxisLabel: metricYLabels?.tokens,
              isEvaluationChart: false,
              series: [
                {
                  name: "Tokens",
                  data:
                    graphData?.result?.system_metrics?.tokens?.map((item) => ({
                      x: normalizeTimestamp(item?.timestamp),
                      y: item?.tokens,
                    })) || [],
                },
              ],
            },
            {
              id: "chart-3",
              label: "Traffic",
              unit: metricUnits?.traffic,
              yAxisLabel: metricYLabels?.traffic,
              isEvaluationChart: false,
              series: [
                {
                  name: "Traffic",
                  data:
                    graphData?.result?.system_metrics?.traffic?.map((item) => ({
                      x: normalizeTimestamp(item?.timestamp),
                      y: item?.traffic,
                    })) || [],
                },
              ],
            },
            {
              id: "chart-4",
              label: "Cost",
              unit: metricUnits?.cost,
              yAxisLabel: metricYLabels?.cost,
              isEvaluationChart: false,
              series: [
                {
                  name: "Cost",
                  data:
                    graphData?.result?.system_metrics?.cost?.map((item) => ({
                      x: normalizeTimestamp(item?.timestamp),
                      y: item?.cost,
                    })) || [],
                },
              ],
            },
          ],
        },
      ];
    }

    return [];
  }, [graphData]);

  const refreshGrid = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["get-graph-data"],
    });
    refetchSystemMetrics();
    queryClient.invalidateQueries({
      queryKey: ["chart-data"], // Invalidates all chart queries
    });
  }, [queryClient, refetchSystemMetrics]);

  useEffect(() => {
    setHeaderConfig({
      text: "Charts",
      refreshData: refreshGrid,
    });
  }, [refreshGrid, setHeaderConfig]);

  return (
    <Box
      sx={{
        paddingX: theme.spacing(2),
      }}
    >
      <Helmet>
        <title>Observe - Charts</title>
      </Helmet>

      {/* Date Range Picker and Button Group */}
      <Box
        display={"flex"}
        flexDirection={"column"}
        gap={theme.spacing(2)}
        sx={{
          position: "sticky",
          zIndex: 100,
          top: 0,
          backgroundColor: "background.paper",
          paddingY: theme.spacing(2),
        }}
      >
        {/* <Typography variant="m1" fontWeight={"fontWeightSemiBold"}>Charts</Typography> */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            // marginBottom: theme.spacing(2),
          }}
        >
          <ChartsDateTimeRangePicker
            setParentDateFilter={setParentDateFilter}
            setDateOption={setDateOption}
            dateOption={dateOption}
            zoomRange={zoomRange}
            observeId={observeId}
          />
          <Box sx={{ display: "flex", gap: theme.spacing(1) }}>
            <Button
              variant="outlined"
              size="small"
              sx={{
                px: 1.5,
              }}
              disabled={isLoading}
              startIcon={
                <SvgColor
                  key={isRefetchingSystemMetrics ? "spinning" : "static"}
                  sx={{
                    animation: "spin 1s linear",
                    animationFillMode: "forwards",
                    "@keyframes spin": {
                      "0%": { transform: "rotate(0deg)" },
                      "100%": { transform: "rotate(360deg)" },
                    },
                  }}
                  src="/assets/icons/ic_reload.svg"
                />
              }
              onClick={refreshGrid}
            >
              Refresh
            </Button>
            <Button
              variant="outlined"
              size="small"
              sx={{ width: "160px" }}
              onClick={() => {
                navigate(
                  `/dashboard/observe/${observeId}/llm-tracing?selectedInterval-0=${selectedInterval.toLowerCase()}&primaryTraceDateFilter=${JSON.stringify(
                    {
                      dateFilter: parentDateFilter,
                      dateOption: dateOption,
                    },
                  )}`,
                );
              }}
              startIcon={<SvgColor src="/assets/icons/navbar/ic_llm.svg" />}
            >
              View Traces
            </Button>
            <StyledIntervalSelect
              value={selectedInterval}
              onChange={(e) => handleDataOptionChange(e.target.value)}
              size="small"
              MenuProps={{
                PaperProps: {
                  sx: {
                    borderRadius: theme.spacing(0.5),
                    mt: 0.5,
                  },
                },
              }}
            >
              {DateRangeButtonOptions.map((option) => (
                <StyledIntervalMenuItem
                  key={option.title}
                  value={option.title}
                  disabled={
                    (isMoreThan7Days && option.title === "Hour") ||
                    (isLessThan90Days && option.title === "Month")
                  }
                  ref={(ref) => {
                    if (option.title === "Custom") {
                      customDatePickerAnc.current = ref;
                    }
                  }}
                >
                  <Typography typography="s2">{option.title}</Typography>
                </StyledIntervalMenuItem>
              ))}
            </StyledIntervalSelect>
          </Box>
        </Box>
      </Box>

      {/* Chart Categories and Charts */}
      <Box sx={{ paddingTop: theme.spacing(3) }}>
        {isLoading ? (
          <>
            <Skeleton
              variant="text"
              width={150}
              height={60}
              sx={{ marginBottom: theme.spacing(2) }}
            />
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <Skeleton variant="rectangular" width="100%" height={250} />
              </Grid>
              <Grid item xs={12} sm={4}>
                <Skeleton variant="rectangular" width="100%" height={250} />
              </Grid>
              <Grid item xs={12} sm={4}>
                <Skeleton variant="rectangular" width="100%" height={250} />
              </Grid>
              <Grid item xs={12} sm={4}>
                <Skeleton variant="rectangular" width="100%" height={250} />
              </Grid>
            </Grid>
          </>
        ) : (
          chartCategories?.map((category) => (
            <div
              key={category.label}
              style={{ marginBottom: theme.spacing(2.5) }}
            >
              {isLoading ? (
                <Skeleton
                  variant="text"
                  width={100}
                  height={30}
                  sx={{ marginBottom: theme.spacing(2) }}
                />
              ) : (
                <Typography
                  variant="body1"
                  fontWeight={"fontWeightSemiBold"}
                  gutterBottom
                  sx={{
                    marginBottom: theme.spacing(2),
                    color: theme.palette.text.primary,
                  }}
                >
                  {category.label}
                </Typography>
              )}

              <Grid container spacing={2}>
                {category?.charts.map((chart) => (
                  <Grid
                    item
                    xs={12}
                    md={4}
                    key={chart.id}
                    style={{
                      cursor: "pointer",
                      transition: "none",
                    }}
                  >
                    {isLoading ? (
                      <Skeleton
                        variant="rectangular"
                        width="100%"
                        height={200}
                      />
                    ) : (
                      <ChartsGenerator
                        id={chart.id}
                        label={chart.label}
                        unit={chart.unit}
                        yAxisLabel={chart.yAxisLabel}
                        isEvaluationChart={chart.isEvaluationChart}
                        series={chart.series}
                        onZoom={handleZoomChange}
                        groupName="system-metrics"
                      />
                    )}
                  </Grid>
                ))}
              </Grid>
            </div>
          ))
        )}
      </Box>
      <Box sx={{ mt: 2 }}>
        <EvaluationCharts observeId={observeId} />
      </Box>
    </Box>
  );
};

export default ChartsView;
