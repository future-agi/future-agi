import {
  Box,
  Grid,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import Scrollbar from "src/components/scrollbar";
import Chart from "react-apexcharts";

const primaryColor = [255, 0, 0]; // Red
const comparisonColor = [0, 0, 255]; // Blue

export default function ClusterTable({ clusterData, gradientColors }) {
  const [currentTab, setCurrentTab] = useState("clusters");
  const [tabOptions] = useState([
    { value: "clusters", label: "Clusters`" },
    { value: "settings", label: "Settings" },
  ]);

  useEffect(() => {}, [clusterData]);

  function handleTab(event, newValue) {
    setCurrentTab(newValue);
  }

  const clusterSettings = (
    <>
      <TextField
        fullWidth
        label="Min Cluster Size"
        variant="outlined"
        margin="normal"
      />
      <TextField
        fullWidth
        label="Min Cluster Distance"
        variant="outlined"
        margin="normal"
      />
    </>
  );

  const clusterInfoTable = (
    <>
      <TableContainer
        sx={{ position: "relative", overflow: "unset", height: "100%" }}
      >
        <Scrollbar>
          <Table sx={{ width: "100%" }} stickyHeader size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Metric Value</TableCell>
                <TableCell>Breakdown</TableCell>
              </TableRow>
            </TableHead>

            <TableBody>
              {clusterData?.cluster?.map((clusterRow, clusterIndex) => (
                <ClusterInfoRow
                  rowInfo={clusterRow}
                  key={clusterIndex}
                  clusterIndex={clusterIndex}
                  // style={{ borderLeft: `5px solid ${getBorderColor(clusterIndex)}` }}
                  style={{
                    borderLeft: `5px solid rgb(${gradientColors[clusterIndex]}) !important`,
                  }}
                  colors={{
                    primary: `rgb(${primaryColor})`,
                    comparison: `rgb(${comparisonColor})`,
                  }}
                ></ClusterInfoRow>
              ))}
            </TableBody>
          </Table>
        </Scrollbar>
      </TableContainer>
    </>
  );

  return (
    <>
      <Box
        sx={{
          px: 2.5,
          boxShadow: (theme) =>
            `inset 0 -2px 0 0 ${alpha(theme.palette.text.disabled, 0.08)}`,
        }}
      >
        <Tabs value={currentTab} onChange={handleTab}>
          {tabOptions.map((tab) => (
            <Tab
              key={tab.value}
              iconPosition="end"
              value={tab.value}
              label={tab.label}
            />
          ))}
        </Tabs>
      </Box>

      {currentTab === "settings" && clusterSettings}
      {currentTab === "clusters" && clusterInfoTable}
    </>
  );
}

ClusterTable.propTypes = {
  clusterData: PropTypes.object,
  onSelectCluster: PropTypes.func,
  clusterParams: PropTypes.object,
  gradientColors: PropTypes.array,
};

// ----------------------------------------------------------------------

function ClusterInfoRow({
  rowInfo,
  clusterIndex,
  style,
  colors,
  isComparison,
}) {
  const options = {
    chart: {
      parentHeightOffset: 0,
      type: "bar",
      toolbar: {
        show: false,
      },
      sparkline: {
        enabled: true, // Enable sparkline to remove axes and padding
      },
    },
    plotOptions: {
      bar: {
        horizontal: true,
      },
    },
    dataLabels: {
      enabled: false,
    },
    grid: {
      show: false, // Remove grid lines
    },
    xaxis: {
      labels: {
        show: false,
      },
      axisBorder: {
        show: false,
      },
      axisTicks: {
        show: false,
      },
    },
    yaxis: {
      show: false, // Remove Y-axis completely
    },
    legend: {
      show: false, // Hides the legend (series names)
    },
    tooltip: {
      enabled: true,
      x: {
        show: false,
      },
      y: {
        formatter: (val) => val,
      },
    },
    colors: [colors?.primary, colors?.comparison],
  };

  const series = [
    {
      name: "Primary",
      data: [10],
    },
    {
      name: "Comparison",
      data: [20],
    },
  ];

  // The series (data) for the ApexCharts Bar Graph
  return (
    <TableRow key={clusterIndex}>
      <TableCell sx={style}>{rowInfo.name}</TableCell>
      <TableCell>
        {isComparison && rowInfo.metric.primary}
        {!isComparison && (
          <>
            <Box sx={{ color: colors?.primary }}>{rowInfo.metric.primary}</Box>
            <Box sx={{ color: colors?.comparison }}>
              {rowInfo.metric.comparison}
            </Box>
          </>
        )}
      </TableCell>
      <TableCell>
        <Grid container>
          <Grid item xs={3}>
            {rowInfo.breakdown.total}
          </Grid>
          <Grid item xs={9}>
            <div id="chart">
              <Chart
                options={options}
                series={series}
                type="bar"
                height={30}
                width={"70%"}
              />
            </div>
          </Grid>
        </Grid>
      </TableCell>
    </TableRow>
  );
}

ClusterInfoRow.propTypes = {
  rowInfo: PropTypes.object,
  clusterIndex: PropTypes.number,
  style: PropTypes.object,
  colors: PropTypes.object,
  isComparison: PropTypes.bool,
};
