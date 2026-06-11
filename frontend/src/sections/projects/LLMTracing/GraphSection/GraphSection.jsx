import React, {
  useState,
  useEffect,
  useRef,
  useMemo,
  useCallback,
} from "react";
import { Box, Typography, useTheme } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import EmptyGraph from "src/assets/illustrations/empty-graph";
import _ from "lodash";
import { getRandomId, getUniqueColorPalette } from "src/utils/utils";
import { add, format, sub } from "date-fns";
import {
  isDateRangeLessThan90Days,
  isDateRangeMoreThan7Days,
} from "src/utils/dateTimeUtils";

import RightControl from "./RightControl";
import LeftControl from "./LeftControl";
import Legend from "./Legend";
import GraphSkeleton from "./GraphSkeleton";
import { formatYAxisValue, getYAxisUnit, getLineSeriesName } from "./common";
import SVGColor from "src/components/svg-color";
import { useLLMTracingStoreShallow } from "../states";
import { logger } from "src/utils/logger";
import { FILTER_FOR_HAS_EVAL } from "../common";

const deltaObject = {
  hour: { hours: 1 },
  day: { days: 1 },
  week: { weeks: 1 },
  month: { months: 1 },
};

const GraphSection = ({
  selectedTab,
  filters,
  showCompare,
  selectedGraphProperty,
  selectedGraphEvals,
  setSelectedGraphEvals,
  setSelectedGraphProperty,
  hasEvalFilter,
  selectedGraphAttributes,
  setSelectedGraphAttributes,
  compareType,
  dateFilter,
  setDateFilter,
  index,
  selectedInterval,
  setSelectedInterval,
  lineColor,
  trafficColor,
}) => {
  const [_height, setHeight] = useState(320);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedGraphConfig, setSelectedGraphConfig] = useState(null);
  const boxRef = useRef(null);
  const llmTracingStore = useLLMTracingStoreShallow((s) => ({
    [`${compareType}Collapsed`]: s[`${compareType}Collapsed`],
    [`set${_.capitalize(compareType)}Collapsed`]:
      s[`set${_.capitalize(compareType)}Collapsed`],
  }));

  const setCollapsed = useCallback(
    (collapsed) => {
      llmTracingStore[`set${_.capitalize(compareType)}Collapsed`](collapsed);
    },
    [llmTracingStore, compareType],
  );

  const isCollapsed = llmTracingStore[`${compareType}Collapsed`];

  const isMoreThan7Days = isDateRangeMoreThan7Days(dateFilter.dateFilter);
  const isLessThan90Days = isDateRangeLessThan90Days(dateFilter.dateFilter);

  const chartId = useMemo(() => getRandomId(), []);
  const chartRef = useRef(null);

  const { observeId } = useParams();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const handleMouseMove = useCallback(
    (e) => {
      if (isDragging) {
        const rect = boxRef.current.getBoundingClientRect();
        let newHeight = e.clientY - rect.y;
        newHeight = Math.max(0, newHeight);
        newHeight = Math.round(newHeight);
        setHeight(newHeight);
      }
    },
    [isDragging],
  );

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  useEffect(() => {
    if (isDragging) {
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    } else {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    }

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [handleMouseMove, isDragging]);

  const combinedFilters = useMemo(() => {
    const createdAtExists = filters?.some?.(
      (f) => f?.column_id === "created_at",
    );
    const startDate = dateFilter?.dateFilter?.[0];
    const endDate = dateFilter?.dateFilter?.[1];

    const defaultCreatedAtFilter =
      !createdAtExists && startDate && endDate
        ? [
            {
              column_id: "created_at",
              filter_config: {
                filter_type: "datetime",
                filter_op: "between",
                filter_value: [
                  new Date(startDate).toISOString(),
                  new Date(endDate).toISOString(),
                ],
              },
            },
          ]
        : [];

    return [
      ...(filters || []),
      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
      ...defaultCreatedAtFilter,
    ];
  }, [filters, dateFilter, hasEvalFilter]);

  const handleGraphConfigChange = (config) => {
    setSelectedGraphConfig(config ? { ...config } : null);
  };

  // Graph APIs

  // Trace Graph Data
  const {
    data: traceGraphData,
    isFetching: traceGraphLoading,
    isPending: traceGraphPending,
  } = useQuery({
    queryKey: [
      "llm-tracing-graph",
      "trace",
      observeId,
      selectedInterval,
      selectedGraphEvals,
      combinedFilters,
      selectedGraphConfig,
    ],
    queryFn: () =>
      axios.post(endpoints.project.getTraceGraphData(), {
        interval: selectedInterval,
        filters: combinedFilters,
        property: "average",
        req_data_config: selectedGraphConfig,
        project_id: observeId,
      }),
    enabled: selectedTab === "trace" && Boolean(selectedGraphConfig?.id),
    select: (data) => data.data?.result,
  });

  // Span Graph Data
  const {
    data: spanGraphData,
    isFetching: spanGraphLoading,
    isPending: spanGraphPending,
  } = useQuery({
    queryKey: [
      "llm-tracing-graph",
      "span",
      observeId,
      selectedGraphProperty,
      selectedInterval,
      selectedGraphEvals,
      combinedFilters,
      selectedGraphEvals,
    ],
    queryFn: () =>
      axios.post(endpoints.project.getSpanGraphData(), {
        interval: selectedInterval,
        filters: combinedFilters,
        property: "average",
        req_data_config: selectedGraphConfig,
        project_id: observeId,
      }),
    enabled: selectedTab === "spans" && Boolean(selectedGraphConfig?.id),
    select: (data) => data.data?.result,
  });

  const apiGraphData = selectedTab === "trace" ? traceGraphData : spanGraphData;
  const apiGraphLoading =
    selectedTab === "trace"
      ? traceGraphLoading && traceGraphPending
      : spanGraphLoading && spanGraphPending;

  const chartData = useMemo(() => {
    const primaryData = [];
    const trafficData = [];

    const evalData = Array.isArray(apiGraphData?.data) ? apiGraphData.data : [];

    for (const item of evalData) {
      if (item.timestamp != null) {
        // Remove timezone suffix to normalize format
        const normalizedTimestamp = item.timestamp.replace(/\+00:00$/, "");

        primaryData.push({ x: normalizedTimestamp, y: item.value ?? 0 });
        trafficData.push({
          x: normalizedTimestamp,
          y: item.primary_traffic ?? 0,
        });
      }
    }

    const lineSeriesName = getLineSeriesName(selectedGraphProperty);
    const isEval = selectedGraphConfig?.type === "EVAL";

    const series = [
      {
        name: lineSeriesName,
        type: "line",
        data: primaryData,
        color: lineColor || theme.palette.blue[600],
        group: "apexcharts-axis-0",
      },
    ];

    const yAxis = [
      {
        seriesName: lineSeriesName,
        title: {
          text:
            getYAxisUnit(_.toLower(selectedGraphProperty)) ||
            getYAxisUnit("default"),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        labels: {
          formatter: (val) => formatYAxisValue(val, selectedGraphProperty),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        opposite: false,
        forceNiceScale: true,
      },
    ];

    if (!isEval) {
      series.push({
        name: "Traffic",
        type: "column",
        data: trafficData,
        color: trafficColor,
        group: "apexcharts-axis-1",
      });
      yAxis.push({
        seriesName: "Traffic",
        title: {
          text: "Traffic",
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        labels: {
          formatter: (val) => formatYAxisValue(val),
          style: isCollapsed
            ? { fontSize: "6px", fontWeight: 400 }
            : { fontSize: "11px", fontWeight: 400 },
        },
        opposite: true,
      });
    }

    const xAxis = {
      type: "datetime",
      convertedCatToNumeric: false, // include this explicitly
      labels: {
        datetimeUTC: false,
        style: isCollapsed ? { fontSize: "6px" } : { fontSize: "11px" },
        offsetY: -4,
      },
    };

    return {
      series,
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
          events: {
            zoomed: (_, { xaxis }) => {
              const startDate = format(
                new Date(xaxis.min),
                "yyyy-MM-dd HH:mm:ss",
              );
              const endDate = format(
                new Date(xaxis.max),
                "yyyy-MM-dd HH:mm:ss",
              );
              setDateFilter({
                dateFilter: [startDate, endDate],
                dateOption: "Custom",
              });
            },
          },
        },
        theme: {
          mode: isDark ? "dark" : "light",
        },
        grid: {
          borderColor: isDark ? "#27272a" : theme.palette.divider,
          strokeDashArray: 6,
        },
        stroke: {
          width: 3,
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
        states: {
          hover: {
            filter: {
              type: "none",
            },
          },
        },
        xaxis: xAxis,
        yaxis: yAxis,
        tooltip: {
          theme: isDark ? "dark" : "light",
          shared: true,
          intersect: false,
        },
        legend: {
          show: false,
          // position: "top",
          // horizontalAlign: "left",
        },
      },
    };
  }, [
    apiGraphData,
    chartId,
    lineColor,
    selectedGraphProperty,
    selectedGraphConfig,
    isCollapsed,
    isDark,
  ]);

  const handleZoomIn = () => {
    const chart = chartRef.current?.chart;
    if (chart) {
      const xaxis = chart.w.globals.minX;
      const maxX = chart.w.globals.maxX;
      const range = maxX - xaxis;
      chart.zoomX(xaxis + range * 0.1, maxX - range * 0.1);
    }
  };

  const handleZoomOut = () => {
    const chart = chartRef.current?.chart;
    if (chart) {
      const xaxis = chart.w.globals.minX;
      const maxX = chart.w.globals.maxX;
      const range = maxX - xaxis;
      chart.zoomX(xaxis - range * 0.1, maxX + range * 0.1);
    }
  };

  const handleMoveAhead = () => {
    setDateFilter((e) => ({
      dateFilter: [
        add(new Date(e?.dateFilter?.[0]), deltaObject[selectedInterval]),
        add(new Date(e?.dateFilter?.[1]), deltaObject[selectedInterval]),
      ],
      dateOption: "Custom",
    }));
  };

  const handleMoveBack = () => {
    setDateFilter((e) => ({
      dateFilter: [
        sub(new Date(e.dateFilter?.[0]), deltaObject[selectedInterval]),
        sub(new Date(e.dateFilter?.[1]), deltaObject[selectedInterval]),
      ],
      dateOption: "Custom",
    }));
  };

  logger.debug({
    selectedGraphProperty,
    selectedGraphConfig,
    selectedGraphEvals,
    selectedGraphAttributes,
  });

  return (
    <Box
      sx={{
        // height: `${height}px`,
        position: "relative",
        transition: isDragging ? "none" : "height 400ms ease-in-out",
      }}
      // onMouseMove={handleMouseMove}
      // ref={boxRef}
    >
      <Box
        sx={{
          height: "100%",
          overflow: "hidden",
          paddingTop: theme.spacing(2),
          gap: theme.spacing(1),
          flexDirection: "column",
          display: "flex",
          border: "1px solid",
          borderColor: "divider",
          backgroundColor: "background.paper",

          borderRadius: 1,
          paddingX: 1,
          paddingY: "20px",
        }}
      >
        <Box
          sx={{
            padding: "12px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: 0.5,
            gap: theme.spacing(1),
            flexDirection: isCollapsed ? "row" : "column",
            display: "flex",
            position: "relative",
          }}
        >
          <Box sx={{ position: "absolute", top: 12, right: 12 }}>
            <SVGColor
              src="/assets/icons/custom/down-chevron.svg"
              sx={{
                width: 24,
                height: 24,
                rotate: isCollapsed ? "0deg" : "180deg",
                cursor: "pointer",
              }}
              onClick={() => setCollapsed(!isCollapsed)}
            />
          </Box>
          <Box
            sx={{
              gap: theme.spacing(2),
              flexDirection: "column",
              display: "flex",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                pb: 0.5,
                gap: 1,
              }}
            >
              <ShowComponent condition={showCompare}>
                <Box
                  sx={() => {
                    const { tagBackground: bg, tagForeground: text } =
                      getUniqueColorPalette(compareType === "primary" ? 1 : 3);
                    return {
                      width: theme.spacing(3),
                      height: theme.spacing(3.125),
                      borderRadius: theme.spacing(0.5),
                      backgroundColor: bg,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 600,
                      color: text,
                    };
                  }}
                >
                  {compareType === "primary" ? "A" : "B"}
                </Box>
              </ShowComponent>
              <Typography typography="m3" fontWeight="fontWeightMedium">
                {compareType === "primary" ? "Primary" : "Compare"} Graph
              </Typography>
            </Box>

            <ShowComponent
              condition={
                setSelectedGraphEvals !== undefined &&
                setSelectedGraphProperty !== undefined
              }
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <LeftControl
                  onGraphConfigChange={handleGraphConfigChange}
                  selectedGraphEvals={selectedGraphEvals}
                  selectedGraphProperty={selectedGraphProperty}
                  setSelectedGraphEvals={setSelectedGraphEvals}
                  setSelectedGraphProperty={setSelectedGraphProperty}
                  selectedGraphAttributes={selectedGraphAttributes}
                  setSelectedGraphAttributes={setSelectedGraphAttributes}
                />
              </Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Legend series={chartData.series} />
                <ShowComponent
                  condition={selectedGraphProperty && !isCollapsed}
                >
                  <RightControl
                    selectedInterval={selectedInterval}
                    setSelectedInterval={setSelectedInterval}
                    isMoreThan7Days={isMoreThan7Days}
                    isLessThan90Days={isLessThan90Days}
                    disabled={!selectedGraphProperty}
                    // Start of Selection
                    index={index}
                    onZoomIn={handleZoomIn}
                    onZoomOut={handleZoomOut}
                    onMoveAhead={handleMoveAhead}
                    onMoveBack={handleMoveBack}
                  />
                </ShowComponent>
              </Box>
            </ShowComponent>
          </Box>

          <Box
            sx={{
              gap: theme.spacing(1),
              flexDirection: "column",
              display: "flex",
              flex: 1,
              marginRight: isCollapsed ? "30px" : "0px",
              overflow: "hidden",
            }}
          >
            <ShowComponent
              condition={
                !selectedGraphProperty ||
                (!selectedGraphConfig &&
                  !selectedGraphEvals?.length &&
                  !Object.keys(selectedGraphAttributes || {}).length)
              }
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "center",
                  height: "100%",
                  alignItems: "center",
                  flexDirection: "column",
                }}
              >
                <EmptyGraph />
                <Typography
                  fontSize="14px"
                  fontWeight={700}
                  color="text.secondary"
                >
                  View Graph
                </Typography>
                <Typography fontSize="12px" fontWeight={400} color="text.muted">
                  Choose from the filter above to view your graph
                </Typography>
              </Box>
            </ShowComponent>

            <ShowComponent
              condition={
                selectedGraphProperty &&
                (selectedGraphConfig ||
                  selectedGraphEvals?.length > 0 ||
                  Object.keys(selectedGraphAttributes || {}).length > 0) &&
                !apiGraphLoading
              }
            >
              <ReactApexChart
                ref={chartRef}
                options={chartData.options}
                series={chartData.series}
                type="line"
                height={isCollapsed ? 124 : 248}
              />
            </ShowComponent>

            <ShowComponent condition={apiGraphLoading}>
              <Box sx={{ height: isCollapsed ? 124 : 248 }}>
                <GraphSkeleton />
              </Box>
            </ShowComponent>
          </Box>
        </Box>
        {/* <Box
          sx={{
            position: "absolute",
            bottom: "-12px",
            left: "30px",
            borderRadius: "50%",
            zIndex: 10,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            padding: 0.3,
            cursor: "pointer",
            backgroundColor: "background.paper",
          }}
          onClick={toggleHeight}
        >
          <Iconify
            icon="bi:arrows-collapse"
            width={16}
            sx={{ color: "text.disabled" }}
          />
        </Box>
        <Box
          sx={{
            position: "absolute",
            bottom: "-12px",
            left: "70px",
            borderRadius: "50%",
            zIndex: 10,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            border: "1px solid",
            borderColor: "divider",
            padding: 0.3,
            backgroundColor: isDragging ? "background.neutral" : "background.paper",
            cursor: isDragging ? "grabbing" : "grab",
          }}
          onMouseDown={handleMouseDown}
        >
          <Iconify
            icon="charm:grab-horizontal"
            width={16}
            sx={{ color: "text.disabled", padding: theme.spacing(0.25) }}
          />
        </Box> */}
      </Box>
    </Box>
  );
};

GraphSection.propTypes = {
  selectedTab: PropTypes.string,
  filters: PropTypes.array,
  showCompare: PropTypes.bool,
  selectedGraphProperty: PropTypes.string,
  selectedGraphEvals: PropTypes.array,
  compareType: PropTypes.string,
  setSelectedGraphEvals: PropTypes.func,
  setSelectedGraphProperty: PropTypes.func,
  dateFilter: PropTypes.array,
  selectedGraphAttributes: PropTypes.string,
  setSelectedGraphAttributes: PropTypes.func,
  setDateFilter: PropTypes.func,
  index: PropTypes.number,
  selectedInterval: PropTypes.string,
  setSelectedInterval: PropTypes.func,
  lineColor: PropTypes.string,
  trafficColor: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
};

export default GraphSection;
