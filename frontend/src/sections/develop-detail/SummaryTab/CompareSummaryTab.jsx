import { Box, Button, Typography, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import EvalCard from "./EvalsCard";
import PropmtCard from "./PropmtCard";
import Iconify from "src/components/iconify";
import CompareEvalCard from "./CompareEvalCard";
import { getUniqueColorPalette } from "src/utils/utils";
import Tooltip from "@mui/material/Tooltip";

const tabOptions = [
  { label: "Evals", value: "evals", disabled: false },
  { label: "Prompt", value: "prompt", disabled: false },
];

const CompareSummaryTab = ({
  selectedDatasetData,
  baseColumn,
  selectedDatasets,
  setEvalsData,
  datasetInfo,
  commonColumn,
  isChooseWinnerSelected,
  dataAfterChooseWinner,
  setDataAfterChooseWinner,
  setIsChooseWinnerButtonVisible,
  isCommonColumn,
  setIsCommonColumn,
}) => {
  const [currentTab, setCurrentTab] = useState("evals");

  // Use useMemo for initial state values based on isCommonColumn
  const initialState = useMemo(() => {
    if (!isCommonColumn) {
      return {
        isCompare: false,
        selectedModel: selectedDatasetData[0],
        datasetId: selectedDatasets[0],
      };
    } else {
      return {
        isCompare: true,
        selectedModel: null,
        datasetId: null,
      };
    }
  }, [isCommonColumn, selectedDatasetData, selectedDatasets]);

  const [isCompare, setIsCompare] = useState(initialState.isCompare);
  const [selectedModel, setSelectedModel] = useState(
    initialState.selectedModel,
  );
  const [datasetId, setDatasetId] = useState(initialState.datasetId);

  // Update states when isCommonColumn changes
  React.useEffect(() => {
    setIsCompare(initialState.isCompare);
    setSelectedModel(initialState.selectedModel);
    setDatasetId(initialState.datasetId);
  }, [initialState]);

  const handleSelectModel = (model, index) => {
    setDatasetId(selectedDatasets[index]);
    setIsCompare(false);
    setSelectedModel(model);
    setIsChooseWinnerButtonVisible(false);
  };

  return (
    <Box sx={{ display: "flex", height: "87vh" }}>
      <Box
        sx={{
          width: "17.5%",
          borderRight: "1px solid var(--border-light)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          overflow: "auto",
        }}
      >
        <Box sx={{ width: "90%", marginTop: "20px" }}>
          <Button
            startIcon={
              <Iconify
                icon="solar:transfer-horizontal-linear"
                sx={{ color: isCompare ? "primary.main" : "text.secondary" }}
              />
            }
            sx={{
              width: "100%",
              backgroundColor: isCompare ? "action.hover" : "background.paper",
              color: isCompare ? "primary.main" : "text.primary",
              justifyContent: "flex-start",
              "&:hover": { backgroundColor: "action.selected" },
            }}
            onClick={() => {
              setIsCompare(true);
              setSelectedModel(null);
              setCurrentTab("evals");
              setIsCommonColumn(true);
            }}
          >
            Compare
          </Button>
          {selectedDatasetData.map((model, index) => {
            const { tagBackground, tagForeground } =
              getUniqueColorPalette(index);
            return (
              <Box
                key={index}
                sx={{
                  display: "flex",
                  gap: 1.6,
                  marginTop: "18px",
                  backgroundColor:
                    selectedModel === model
                      ? "action.hover"
                      : "background.paper",
                  cursor: "pointer",
                  padding: "6px",
                  borderRadius: "6px",
                  "&:hover": { backgroundColor: "action.selected" },
                  alignItems: "center",
                }}
                onClick={() => handleSelectModel(model, index)}
              >
                <Typography
                  sx={{
                    color: tagForeground,
                    backgroundColor: tagBackground,
                    borderRadius: "6px",
                    height: "24px",
                    width: "24px",
                    fontSize: "12px",
                    fontWeight: 500,
                    paddingX: "4px",
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                  }}
                >
                  {String.fromCharCode(65 + index)}
                </Typography>
                <Tooltip title={model}>
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightRegular"}
                    color={"text.primary"}
                    sx={{
                      display: "inline-block",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {model}
                  </Typography>
                </Tooltip>
              </Box>
            );
          })}
        </Box>
      </Box>
      <Box
        className="ag-theme-quartz"
        sx={{ flex: 1, padding: "12px", height: "100%", overflowY: "hidden" }}
      >
        {!isCompare && (
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
        )}
        <Box sx={{ overflow: "auto" }}>
          <ShowComponent condition={currentTab === "evals"}>
            {isCompare ? (
              <CompareEvalCard
                baseColumn={baseColumn}
                selectedDatasets={selectedDatasets}
                setEvalsData={setEvalsData}
                datasetInfo={datasetInfo}
                commonColumn={commonColumn}
                isChooseWinnerSelected={isChooseWinnerSelected}
                dataAfterChooseWinner={dataAfterChooseWinner}
                setDataAfterChooseWinner={setDataAfterChooseWinner}
                setIsChooseWinnerButtonVisible={setIsChooseWinnerButtonVisible}
                isCommonColumn={isCommonColumn}
              />
            ) : (
              <EvalCard datasetId={datasetId} />
            )}
          </ShowComponent>
          <ShowComponent condition={currentTab === "prompt"}>
            <PropmtCard datasetId={datasetId} />
          </ShowComponent>
        </Box>
      </Box>
    </Box>
  );
};

CompareSummaryTab.propTypes = {
  setDataAfterChooseWinner: PropTypes.func,
  isChooseWinnerSelected: PropTypes.bool,
  dataAfterChooseWinner: PropTypes.object,
  datasetInfo: PropTypes.array,
  commonColumn: PropTypes.array,
  setEvalsData: PropTypes.func,
  baseColumn: PropTypes.string,
  selectedDatasets: PropTypes.array,
  selectedDatasetData: PropTypes.array,
  experimentSearch: PropTypes.string,
  isCommonColumn: PropTypes.bool,
  setIsChooseWinnerButtonVisible: PropTypes.func,
  setIsCommonColumn: PropTypes.func,
};

export default CompareSummaryTab;
