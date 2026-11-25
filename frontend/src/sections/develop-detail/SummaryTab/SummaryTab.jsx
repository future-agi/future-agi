import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import EvalCard from "./EvalsCard";
import PropmtCard from "./PropmtCard";

const tabOptions = [
  { label: "Evals", value: "evals", disabled: false },
  { label: "Prompt", value: "prompt", disabled: false },
];

const graphData = [
  {
    title: "Context Sufficiency",
    bar: [0.4, 0.6, 0.6, 0.2, 0.2, 0.4, 0.5, 0.5, 0.5, 1],
  },
  {
    title: "less than 300 characters",
    bar: [0.2, 0.6, 0.4, 0.2, 0.5, 0.4, 0.3, 0.5, 0.5, 1],
  },
  {
    title: "response faithfulness",
    bar: [0.1, 0.2, 0.4, 0.3, 0.5, 0.4, 0.5, 0.5, 0.5, 0.1],
  },
  {
    title: "Ragas answer relevancy",
    bar: [0.4, 0.6, 0.6, 0.2, 0.2, 0.4, 0.5, 0.5, 0.5, 1],
  },
  {
    title: "Ragas conciousness",
    bar: [0.2, 0.6, 0.4, 0.2, 0.5, 0.4, 0.3, 0.5, 0.5, 1],
  },
  {
    title: "Answer completeness",
    bar: [0.1, 0.2, 0.4, 0.3, 0.5, 0.4, 0.5, 0.5, 0.5, 0.1],
  },
];

const SummaryTab = ({ setCurrentTabs }) => {
  const [currentTab, setCurrentTab] = useState("evals");

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
        height: "100%",
        overflowY: "hidden",
      }}
    >
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
      <Box sx={{ overflow: "auto", height: "calc(100vh - 200px)" }}>
        <ShowComponent condition={currentTab === "evals"}>
          <EvalCard graphData={graphData} setCurrentTab={setCurrentTabs} />
        </ShowComponent>
        <ShowComponent condition={currentTab === "prompt"}>
          <PropmtCard graphData={graphData} setCurrentTab={setCurrentTabs} />
        </ShowComponent>
      </Box>
    </Box>
  );
};

SummaryTab.propTypes = {
  setCurrentTabs: PropTypes.func.isRequired,
};

export default SummaryTab;
