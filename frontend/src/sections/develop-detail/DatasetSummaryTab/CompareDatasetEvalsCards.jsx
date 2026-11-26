import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { ShowComponent } from "src/components/show";
import DatasetCompareEvalSummary from "./DatasetCompareEvalSummary";
import DatasetComparePromptSummary from "./DatasetComparePromptSummary";
import EvalsCard from "./EvalsCard/EvalsCard";
import PromptCard from "./PromptCard";

const tabOptions = [
  { label: "Evals", value: "evals", disabled: false },
  { label: "Prompt", value: "prompt", disabled: false },
];

const CompareDatasetEvalsCards = (props) => {
  const {
    isCompare,
    datasetId,
    selectedDatasets,
    selectedIndex,
    baseColumn,
    commonColumn,
    selectedDatasetData,
    isCommonColumn,
  } = props;

  const [currentTab, setCurrentTab] = useState("evals");

  const changeTab = (e, value) => {
    setCurrentTab(value);
  };

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
      }}
    >
      <Box display="flex" justifyContent={"space-between"}>
        <Box sx={{ mb: 2 }}>
          <SegmentedTabs value={currentTab} onChange={changeTab}>
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
      <Box sx={{ overflow: "auto", height: "calc(100vh - 200px)" }}>
        <ShowComponent condition={currentTab === "evals"}>
          {isCommonColumn ? (
            <DatasetCompareEvalSummary
              currentDataset={datasetId}
              isCompare={isCompare}
              selectedDatasets={selectedDatasets}
              selectedIndex={selectedIndex}
              commonColumn={commonColumn}
              baseColumn={baseColumn}
              selectedDatasetData={selectedDatasetData}
            />
          ) : (
            <EvalsCard
              setCurrentTab={() => {}}
              datasetId={datasetId}
              datasetIndex={selectedIndex}
            />
          )}
        </ShowComponent>
        <ShowComponent condition={currentTab === "prompt"}>
          {isCommonColumn ? (
            <DatasetComparePromptSummary
              currentDataset={datasetId}
              isCompare={isCompare}
              selectedDatasets={selectedDatasets}
              selectedIndex={selectedIndex}
              commonColumn={commonColumn}
              baseColumn={baseColumn}
              selectedDatasetData={selectedDatasetData}
            />
          ) : (
            <PromptCard
              setCurrentTab={() => {}}
              datasetId={datasetId}
              datasetIndex={selectedIndex}
            />
          )}
        </ShowComponent>
      </Box>
    </Box>
  );
};

export default CompareDatasetEvalsCards;

CompareDatasetEvalsCards.propTypes = {
  isCompare: PropTypes.bool,
  datasetId: PropTypes.string,
  selectedDatasets: PropTypes.array,
  selectedIndex: PropTypes.number,
  baseColumn: PropTypes.string,
  commonColumn: PropTypes.array,
  selectedDatasetData: PropTypes.array,
  isCommonColumn: PropTypes.bool,
};
