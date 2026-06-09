import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import ChartsGenerator from "../ChartsView/ChartsGenerator";

const MonitorGraph = ({ selectedMetric, control, metricList }) => {
  const metric = useWatch({ control, name: "metric" });
  const metricDetails = metricList?.find((m) => m.id === metric);

  const { data: graphData } = useQuery({
    queryKey: ["monitor-graph", metricDetails],
    queryFn: () =>
      axios.post(endpoints.project.getMonitorGraph(), {
        metric_type: metricDetails?.metricType,
        metric_name: metricDetails?.id,
      }),
    enabled: !!metricDetails,
    select: (data) => data.data.result,
  });

  const chartData = Array.isArray(graphData) ? graphData : [];
  const theme = useTheme();

  const chartCategories = [
    {
      charts: [
        {
          id: "chart-1",
          unit: "s",
          label: `${selectedMetric}`,
          series: [
            {
              name: "Normal values",
              data: chartData.map((data) => ({
                x: new Date(data.timestamp).getTime(),
                y: data.value,
              })),
            },
          ],
        },
      ],
    },
  ];

  return (
    <Box
      sx={{
        padding: theme.spacing(2),
        paddingTop: theme.spacing(0),
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {!metricDetails ? (
        <Box
          sx={{
            height: 400,
            backgroundColor: "background.neutral",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 2,
          }}
        >
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
            sx={{
              width: "207px",
              textAlign: "center",
            }}
          >
            Please fill out the alert details to see the data here
          </Typography>
        </Box>
      ) : (
        <Box>
          {chartCategories.map((category) =>
            category.charts.map((chart) => (
              <ChartsGenerator
                key={chart.id}
                id={chart.id}
                label={chart.label}
                series={chart.series}
                unit={chart.unit}
              />
            )),
          )}
        </Box>
      )}
    </Box>
  );
};

MonitorGraph.propTypes = {
  selectedMetric: PropTypes.string.isRequired,
  control: PropTypes.object,
  metricList: PropTypes.array,
};

export default MonitorGraph;
