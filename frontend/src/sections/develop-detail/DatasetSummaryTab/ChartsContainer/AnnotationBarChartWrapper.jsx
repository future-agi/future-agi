import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useMemo, useState } from "react";
import ColumnBarChart from "./ColumnBarChart";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import HeatChart from "./HeatChart";

const tabOptions = [
  { label: "Graph", value: "graph", disable: false },
  { label: "Heatmap", value: "heatmap", disable: false },
];

const AnnotationBarChartWrapper = ({ item, names }) => {
  const { graphData, heatmapData } = item;
  const [currentTab, setCurrentTab] = useState("graph");

  const { data, label } = useMemo(() => {
    const data = [];
    const label = [];
    if (currentTab === "graph") {
      data.push({ name: "graph", value: Object.values(graphData || {}) });
      label.push(...Object.keys(graphData || {}));
    }
    if (currentTab === "heatmap") {
      const labels = [];
      const values = [];
      Object.entries(heatmapData || {}).forEach(([key, value], ind) => {
        if (ind === 0) {
          labels.push(...Object.keys(value || {}));
        }
        const name = names.find((temp) => temp?.user_id === key)?.name || "";
        values.push({ name: name, value: Object.values(value || {}) });
      });
      data.push(...values);
      label.push(...labels);
    }
    return { data, label };
  }, [graphData, heatmapData, names, currentTab]);

  return (
    <Box height="max-content">
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
      <ShowComponent condition={currentTab === "graph"}>
        <ColumnBarChart
          height={350}
          data={data}
          graphLabels={label}
          type="annotation"
          colors={["purple"]}
        />
      </ShowComponent>
      <ShowComponent condition={currentTab === "heatmap"}>
        <HeatChart data={data} graphLabels={label} height={350} />
      </ShowComponent>
    </Box>
  );
};

export default AnnotationBarChartWrapper;

AnnotationBarChartWrapper.propTypes = {
  item: PropTypes.object,
  names: PropTypes.array,
};
