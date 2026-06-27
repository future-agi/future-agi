import React, { useMemo } from "react";
import { Box, Grid, Skeleton } from "@mui/material";
import ChartsGenerator from "../../ChartsView/ChartsGenerator";
import CustomChartHeader from "./CustomChartHeader";
import useUsersStore from "../Store/usersStore";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { useUrlState } from "src/routes/hooks/use-url-state";
import {
  DEFAULT_DATE_FILTER,
  DEFAULT_ZOOM_RANGE,
  transformDateFilterToBackendFilters,
  transformGraphDataToChartData,
} from "../common";

const UserMetricsGraphSection = () => {
  const {
    chartTypes,
    globalChartType,
    isGlobalChartType,
    toggleChartType,
    toggleGlobalChartType,
  } = useUsersStore();
  const [selectedProjectId] = useUrlState("projectId", null);
  const [selectedEndUserId] = useUrlState("endUserId", null);
  const [dateInterval] = useUrlState("dateInterval", "day");
  const [dateFilter] = useUrlState("dateFilter", DEFAULT_DATE_FILTER);

  const [_, setZoomRange] = useUrlState("zoomRange", DEFAULT_ZOOM_RANGE);

  const { data: graphData, isLoading } = useQuery({
    // If dateFilter is an object, stringify it in the queryKey
    queryKey: [
      "get-graph-data",
      selectedProjectId,
      selectedEndUserId,
      JSON.stringify(dateFilter),
    ],
    queryFn: () => {
      const filters = transformDateFilterToBackendFilters(dateFilter);
      return axios
        .post(
          endpoints.project.getUserGraphData(),
          {
            filters,
            interval: dateInterval,
          },
          {
            params: {
              project_id: selectedProjectId,
              end_user_id: selectedEndUserId,
            },
          },
        )
        .then((response) => response.data);
    },
    enabled: Boolean(selectedProjectId && selectedEndUserId),
  });

  const chartData = useMemo(() => {
    if (!graphData) return [];

    return transformGraphDataToChartData(graphData);
  }, [graphData]);

  const handleToggle = (id) => {
    if (isGlobalChartType) {
      toggleGlobalChartType();
    } else {
      toggleChartType(id);
    }
  };

  return (
    <Box>
      <Grid container spacing={1.5}>
        {isLoading ? (
          <Box sx={{ p: 1.5, pr: 0, width: "100%" }}>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6}>
                <Skeleton variant="rectangular" width="100%" height={300} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <Skeleton variant="rectangular" width="100%" height={300} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <Skeleton variant="rectangular" width="100%" height={300} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <Skeleton variant="rectangular" width="100%" height={300} />
              </Grid>
            </Grid>
          </Box>
        ) : (
          chartData.map((chart) => {
            const chartType = isGlobalChartType
              ? globalChartType
              : chartTypes[chart.id] || "line";

            return (
              <Grid item xs={12} md={6} key={chart.id}>
                <ChartsGenerator
                  id={chart.id}
                  label={chart.label}
                  unit={chart.unit}
                  series={chart.series}
                  chartType={chartType}
                  onZoom={setZoomRange}
                  height="300px"
                  headerComponent={
                    <CustomChartHeader
                      label={chart.label}
                      chartType={chartType}
                      onToggleType={() => handleToggle(chart.id)}
                      totalTraces={
                        chart.label.toLowerCase() === "cost"
                          ? `$ ${chart.total}`
                          : `${chart.total}`
                      }
                    />
                  }
                />
              </Grid>
            );
          })
        )}
      </Grid>
    </Box>
  );
};

export default UserMetricsGraphSection;
