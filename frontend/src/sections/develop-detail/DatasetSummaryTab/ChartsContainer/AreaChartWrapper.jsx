import { Box, Typography, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useState } from "react";
import AreaChart from "./AreaChart";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import ChartTableData from "./ChartTableData";
import ColumnBarChart from "./ColumnBarChart";
import TotalRowCount from "./TotalRowCount";
import { getSuffixForCharts } from "./constant";

const tabOptions = [
  { label: "Table", value: "table", disabled: false },
  { label: "Column Chart", value: "column_chart", disabled: false },
];

const AreaChartWrapper = ({ data, graphLabels, headerData, datasetIndex }) => {
  const [currentTab, setCurrentTab] = useState("column_chart");
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        justifyContent: currentTab === "table" ? "flex-start" : "flex-end",
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Box display={"flex"} gap={0.5} alignItems={"end"}>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            {headerData?.name} :
          </Typography>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color="status.success.fg"
          >
            {headerData?.average !== null
              ? `${headerData?.average}${getSuffixForCharts(headerData, null)}`
              : "N/A"}
          </Typography>
          {data?.length === 1 && (
            <TotalRowCount value={data?.[0]?.totalCells || 0} />
          )}
        </Box>
        <Box>
          <Box sx={{ mb: 2 }}>
            <SegmentedTabs
              value={currentTab}
              onChange={(e, value) => setCurrentTab(value)}
            >
              {tabOptions.map((tab) => (
                <Tab
                  key={tab.value}
                  label={tab.label}
                  value={tab.value}
                  disabled={tab.disabled}
                />
              ))}
            </SegmentedTabs>
          </Box>
        </Box>
      </Box>
      <Box sx={{ height: "300px" }}>
        <ShowComponent condition={currentTab === "graph_chart"}>
          <AreaChart
            height={300}
            data={data}
            graphLabels={graphLabels}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "column_chart"}>
          <ColumnBarChart
            height={300}
            data={data}
            graphLabels={graphLabels}
            colors={["orange"]}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "table"}>
          <ChartTableData
            height={300}
            data={data}
            graphLabels={graphLabels}
            datasetIndex={datasetIndex}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

export default AreaChartWrapper;

AreaChartWrapper.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  headerData: PropTypes.object,
  datasetIndex: PropTypes.number,
};
