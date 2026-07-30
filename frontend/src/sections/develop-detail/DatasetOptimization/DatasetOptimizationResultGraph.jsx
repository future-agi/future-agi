import React from "react";
import { Box, Typography, useTheme } from "@mui/material";
import ReactApexChart from "react-apexcharts";
import { useState, useRef, useEffect, useMemo } from "react";
import Logo from "src/components/logo/logo";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { getUniqueColorPalette } from "src/utils/utils";
import {
  getCategories,
  getGraphTooltipComponent,
  updateCrosshairColor,
} from "src/utils/optimization";
import FormSearchSelectFieldState from "src/components/FromSearchSelectField/FormSearchSelectFieldState";

/**
 * Dataset Optimization Result Graph Component
 *
 * Similar to OptimizationResultGraph from simulation, but uses dataset optimization endpoints.
 */
const DatasetOptimizationResultGraph = ({ optimizationId }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const containerRef = useRef(null);
  const [selectedGraph, setSelectedGraph] = useState(null);
  const [activeSeries, setActiveSeries] = useState(null);

  const { data: graphData, isPending: isPendingGraph } = useQuery({
    queryKey: ["dataset-optimization-graph", optimizationId],
    queryFn: () =>
      axios.get(endpoints.develop.datasetOptimization.graph(optimizationId)),
    enabled: !!optimizationId,
    select: (data) => data?.data?.result,
  });

  // Transform single graph data to match simulation format
  // Dataset optimization returns { name, evaluations: [...] }
  // Simulation expects { [key]: { name, evaluations: [...] } }
  const optimizationGraph = useMemo(() => {
    if (!graphData) return {};
    // Wrap in object with single key if it's not already in that format
    if (graphData.name && graphData.evaluations) {
      return { default: graphData };
    }
    return graphData;
  }, [graphData]);

  useEffect(() => {
    if (
      optimizationGraph &&
      Object.keys(optimizationGraph).length > 0 &&
      selectedGraph === null
    ) {
      const ids = Object.keys(optimizationGraph);
      const selectedIds = ids.slice(0, 10);
      setSelectedGraph(selectedIds);
    }
  }, [optimizationGraph, selectedGraph]);

  const seriesData = useMemo(() => {
    return Object.entries(optimizationGraph || {}).reduce(
      (acc, [key, value]) => {
        if (selectedGraph?.includes(key)) {
          acc.push({
            name: value?.name,
            data:
              value?.evaluations?.map((evaluation) =>
                typeof evaluation?.score === "number"
                  ? evaluation?.score * 100
                  : 0,
              ) ?? [],
          });
        }
        return acc;
      },
      [],
    );
  }, [optimizationGraph, selectedGraph]);

  const categories = useMemo(() => {
    return getCategories(seriesData?.[0]?.data?.length ?? 0);
  }, [seriesData]);

  const evaluationOptions = useMemo(() => {
    return Object.entries(optimizationGraph || {}).map(([key, value]) => ({
      label: value?.name,
      value: key,
    }));
  }, [optimizationGraph]);

  if (isPendingGraph || !graphData) {
    return (
      <Box
        sx={{
          padding: "12px",
          backgroundColor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        <Typography color="text.secondary">Loading graph...</Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        padding: "12px",
        backgroundColor: "background.neutral",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Logo width={20} height={20} collapsed={true} />
        <Typography typography="s1_2" fontWeight="fontWeightMedium">
          Optimization Results
        </Typography>
        <CustomTooltip
          type="black"
          show
          title="Optimized based on LLM evaluation"
          arrow
          placement="top"
          size="small"
        >
          <SvgColor
            src="/assets/icons/ic_info.svg"
            sx={{ width: "12px", height: "12px", color: "text.primary" }}
          />
        </CustomTooltip>
      </Box>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          backgroundColor: "background.paper",
          padding: 2,
          gap: "0px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "center",
            position: "relative",
          }}
        >
          <Box
            ref={containerRef}
            sx={{
              flex: 1,
              display: "flex",
              gap: "20px",
              alignItems: "center",
              overflow: "hidden",
            }}
          >
            {seriesData.map((series, index) => (
              <Box
                key={series.name}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  flexShrink: 0,
                  cursor: "pointer",
                  paddingX: 0.5,
                  borderRadius: 0.5,
                  backgroundColor:
                    activeSeries === index
                      ? getUniqueColorPalette(index).tagBackground
                      : "transparent",
                  userSelect: "none",
                }}
                onClick={() => {
                  if (activeSeries === index) {
                    setActiveSeries(null);
                  } else {
                    setActiveSeries(index);
                  }
                }}
              >
                <Box
                  sx={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    backgroundColor: getUniqueColorPalette(index).tagBackground,
                  }}
                />
                <Typography typography="s1">{series.name}</Typography>
              </Box>
            ))}
          </Box>
          <FormSearchSelectFieldState
            options={evaluationOptions}
            size="small"
            label="Evaluations"
            value={selectedGraph ?? []}
            onChange={(e) => {
              setSelectedGraph(e.target.value);
            }}
            checkbox
            multiple
          />
        </Box>
        <ReactApexChart
          options={{
            chart: {
              id: "dataset-optimization-result-graph",
              type: "line",
              background: "transparent",
              foreColor: isDark ? "#a1a1aa" : undefined,
              toolbar: {
                show: false,
              },
              zoom: {
                enabled: false,
              },
              events: {
                dataPointMouseEnter: (_, chartContext, config) => {
                  updateCrosshairColor(chartContext, config.seriesIndex);
                },
                mouseMove: (_, chartContext, config) => {
                  if (config.seriesIndex !== undefined) {
                    updateCrosshairColor(chartContext, config.seriesIndex);
                  }
                },
                mouseLeave: (_, chartContext) => {
                  updateCrosshairColor(chartContext, undefined);
                },
              },
            },
            theme: {
              mode: isDark ? "dark" : "light",
            },
            colors: seriesData.map((_, idx) => {
              if (activeSeries !== null && idx !== activeSeries) {
                return getUniqueColorPalette(idx).tagBackground;
              } else {
                return getUniqueColorPalette(idx).tagBackground;
              }
            }),
            stroke: {
              width: 2,
              curve: "smooth",
            },
            markers: {
              size: 0,
            },
            xaxis: {
              categories: categories,
              labels: {
                style: {
                  fontFamily: "IBM Plex Sans",
                  fontWeight: 400,
                  fontSize: "12px",
                  lineHeight: "18px",
                  letterSpacing: "0%",
                  textAlign: "center",
                },
              },
              crosshairs: {
                show: true,
                position: "back",
                stroke: {
                  color: getUniqueColorPalette(0).tagBackground,
                  width: 1,
                  dashArray: 4,
                },
              },
            },
            yaxis: {
              title: {
                text: "Evaluation Score",
                style: {
                  fontFamily: "IBM Plex Sans",
                  fontWeight: 400,
                  fontSize: "12px",
                  lineHeight: "18px",
                  letterSpacing: "0%",
                },
              },
              min: 0,
              max: 100,
              tickAmount: 5,
              labels: {
                formatter: (value) => Math.round(value),
              },
            },
            grid: {
              borderColor: "var(--border-default)",
              strokeDashArray: 4,
              xaxis: {
                lines: {
                  show: false,
                },
              },
              yaxis: {
                lines: {
                  show: true,
                },
              },
            },
            legend: {
              show: false,
            },
            tooltip: {
              theme: isDark ? "dark" : "light",
              shared: false,
              fillSeriesColor: true,
              style: {
                border: "none !important",
              },
              onMarkerHover: (_, chartContext, { seriesIndex }) => {
                if (seriesIndex !== undefined) {
                  const color =
                    getUniqueColorPalette(seriesIndex)?.tagBackground ||
                    getUniqueColorPalette(0).tagBackground;
                  const baseEl = chartContext.w.globals.dom.baseEl;
                  const crosshairLine = baseEl?.querySelector(
                    ".apexcharts-xcrosshairs line",
                  );
                  if (crosshairLine) {
                    crosshairLine.setAttribute("stroke", color);
                    crosshairLine.setAttribute("stroke-dasharray", "4");
                    crosshairLine.style.stroke = color;
                    crosshairLine.style.strokeDasharray = "4";
                  }
                }
              },
              custom: ({ seriesIndex, dataPointIndex, w }) => {
                if (seriesIndex !== undefined) {
                  const color =
                    getUniqueColorPalette(seriesIndex)?.tagBackground ||
                    getUniqueColorPalette(0).tagBackground;
                  const baseEl = w.globals.dom.baseEl;
                  const crosshairLine = baseEl?.querySelector(
                    ".apexcharts-xcrosshairs line",
                  );
                  if (crosshairLine) {
                    crosshairLine.setAttribute("stroke", color);
                    crosshairLine.setAttribute("stroke-dasharray", "4");
                    crosshairLine.style.stroke = color;
                    crosshairLine.style.strokeDasharray = "4";
                  }
                }

                const trialName = w.globals.categoryLabels[dataPointIndex];
                const seriesName = w.config.series[seriesIndex].name;
                const value = w.globals.series[seriesIndex][dataPointIndex];
                const color =
                  getUniqueColorPalette(seriesIndex)?.tagBackground ??
                  getUniqueColorPalette(0).tagBackground;

                return getGraphTooltipComponent(
                  trialName,
                  seriesName,
                  value,
                  color,
                );
              },
            },
          }}
          series={seriesData}
          type="line"
          height={260}
        />
      </Box>
    </Box>
  );
};

DatasetOptimizationResultGraph.propTypes = {
  optimizationId: PropTypes.string,
};

export default DatasetOptimizationResultGraph;
