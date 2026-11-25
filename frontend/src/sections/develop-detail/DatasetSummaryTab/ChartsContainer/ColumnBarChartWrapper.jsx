import { Box, Typography, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useState } from "react";
import { ShowComponent } from "../../../../components/show";
import ChartTableData from "./ChartTableData";
import PropTypes from "prop-types";
import ColumnBarChart from "./ColumnBarChart";
import TotalRowCount from "./TotalRowCount";

const tabOptions = [
  { label: "Graph", value: "graph", disabled: false },
  { label: "Table", value: "table", disabled: false },
];

const ColumnBarChartWrapper = ({
  data,
  graphLabels,
  headerData,
  datasetIndex,
}) => {
  const [currentTab, setCurrentTab] = useState("graph");

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        justifyContent: "space-between",
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Box display={"flex"} gap={0.5}>
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
            {headerData?.average}
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
      <ShowComponent condition={currentTab === "graph"}>
        <ColumnBarChart
          data={data}
          graphLabels={graphLabels}
          datasetIndex={datasetIndex}
        />
      </ShowComponent>
      <ShowComponent condition={currentTab === "table"}>
        <ChartTableData
          data={data}
          graphLabels={graphLabels}
          datasetIndex={datasetIndex}
        />
      </ShowComponent>
    </Box>
  );
};

export default ColumnBarChartWrapper;

ColumnBarChartWrapper.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  headerData: PropTypes.object,
  datasetIndex: PropTypes.number,
};
