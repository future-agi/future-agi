import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import ApexCharts from "react-apexcharts";
import { isCellValueEmpty } from "src/components/table/utils";

const TestDetailSystemMetrics = ({ latencies }) => {
  const theme = useTheme();
  const metricsOrder = useMemo(
    () => [
      {
        value: "endpointing",
        label: "Endpointing",
        color: theme.palette.blue[700],
      },
      {
        value: "transcriber",
        label: "Transcriber",
        color: theme.palette.blue[600],
      },
      { value: "model", label: "LLM", color: theme.palette.blue[500] },
      { value: "voice", label: "Voice", color: theme.palette.blue[400] },
    ],
    [theme],
  );

  const { options, series } = useMemo(() => {
    const seriesData = metricsOrder.reduce((acc, curr) => {
      if (!isCellValueEmpty(latencies?.[curr.value])) {
        const lastValue = acc.length > 0 ? acc[acc.length - 1].y[1] : 0;
        acc.push({
          x: curr.label,
          y: [lastValue, lastValue + latencies[curr.value]],
          fillColor: curr.color,
        });
      }
      return acc;
    }, []);

    const points = metricsOrder.reduce((acc, curr) => {
      if (!isCellValueEmpty(latencies?.[curr.value])) {
        const lastValue = acc.length > 0 ? acc[acc.length - 1].x : 0;
        const pointValue = lastValue + latencies[curr.value];
        acc.push({
          x: parseFloat(pointValue.toFixed(2)),
          y: curr.label,
          marker: {
            size: 0,
          },
          label: {
            offsetY: 35,
            borderColor: "transparent",
            style: {
              color: theme.palette.text.secondary,
              background: "transparent",
              fontSize: "10px",
              fontFamily: "Inter",
              fontWeight: 400,
            },
            text: `${Math.round(pointValue)}ms`,
          },
        });
      }
      return acc;
    }, []);

    const series = [{ data: seriesData }];
    const options = {
      chart: {
        toolbar: { show: false },
        zoom: {
          enabled: false,
        },
      },
      plotOptions: {
        bar: {
          horizontal: true,
          barHeight: "40%",
          distributed: true,
          borderRadius: 4,
        },
      },

      states: {
        hover: {
          filter: {
            type: "none",
          },
        },
        active: {
          filter: {
            type: "none",
          },
        },
      },
      annotations: {
        points,
      },
      dataLabels: {
        enabled: true,
        textAnchor: "middle",
        formatter: (val) => {
          const value = val[1] - val[0] || 0;
          return `${value.toFixed(2)}ms`;
        },
        style: {
          fontSize: "10px",
          fontFamily: "Inter",
          fontWeight: 400,
          colors: [theme.palette.common.white],
        },
      },
      xaxis: {
        type: "numeric",
        tickAmount: 5,
        axisBorder: {
          show: false,
        },
        axisTicks: {
          show: false,
        },
        title: {
          text: "Time (ms)",
          offsetY: 10,
          style: {
            fontSize: "12px",
            color: theme.palette.text.primary,
            fontWeight: 600,
          },
        },
        labels: {
          formatter: (val) => Math.round(val),
          style: {
            fontSize: "11px",
            fontFamily: "Inter",
            fontWeight: 400,
            colors: [theme.palette.text.secondary],
          },
          offsetY: 5,
        },
      },
      tooltip: {
        enabled: false,
      },
      grid: {
        show: false,
        padding: {
          left: 60,
          right: 40,
          top: 0,
          bottom: 0,
        },
      },
      yaxis: {
        axisBorder: {
          show: false,
        },
        axisTicks: {
          show: false,
        },
        labels: {
          style: {
            colors: theme.palette.text.primary,
            fontSize: "16px",
            fontFamily: "Inter",
            fontWeight: 450,
          },
          align: "left",
          offsetX: -60,
        },
      },
      labels: {
        show: true,
      },
      legend: {
        show: false,
      },
    };

    return { options, series };
  }, [latencies, metricsOrder, theme.palette]);

  return (
    <Box>
      <Box>
        <Typography typography="s1_2" fontWeight="fontWeightMedium">
          System Metrics
        </Typography>
        <Typography typography="s1" color="text.secondary">
          Average latency metrics of the call
        </Typography>
      </Box>
      <ApexCharts
        options={options}
        series={series}
        type="rangeBar"
        height="300px"
      />
    </Box>
  );
};

TestDetailSystemMetrics.propTypes = {
  latencies: PropTypes.shape({
    model: PropTypes.number,
    voice: PropTypes.number,
    transcriber: PropTypes.number,
    endpointing: PropTypes.number,
  }),
};

export default TestDetailSystemMetrics;
