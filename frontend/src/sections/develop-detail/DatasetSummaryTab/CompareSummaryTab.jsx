import React, { useState } from "react";
import { Box, Button, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import CompareDatasetSummaryIcon from "./CompareDatasetSummaryIcon";
import CompareDatasetEvalsCards from "./CompareDatasetEvalsCards";
import { ShowComponent } from "src/components/show";

const CompareSummaryTab = ({
  selectedDatasetData,
  selectedDatasets,
  setIsChooseWinnerButtonVisible,
  isCommonColumn,
  commonColumn,
  baseColumn,
}) => {
  const [datasetId, setDatasetId] = useState(null);
  const [isCompare, setIsCompare] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [selectedModel, setSelectedModel] = useState(null);

  const handleSelectModel = (model, index) => {
    setSelectedIndex(index);
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
                // @ts-ignore
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
              setDatasetId(null);
              // setIsCommonColumn(true);
              setSelectedIndex(null);
            }}
          >
            Compare
          </Button>
          {selectedDatasetData?.map((model, index) => {
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
                <CompareDatasetSummaryIcon index={index} />
                <Tooltip title={model}>
                  <Typography
                    typography="s1"
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
      <ShowComponent condition={!isCommonColumn && isCompare}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            padding: "16px",
            width: "100%",
          }}
        >
          <Box
            sx={{
              marginTop: "16px",
              borderRadius: "4px",
              backgroundColor: "status.info.bg",
              border: "1px solid",
              borderColor: "status.info.border",
              padding: "12px",
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            <Typography
              typography={"s1"}
              fontWeight={"fontWeightSemiBold"}
              color="status.info.fg"
            >
              There are no common columns to compare
            </Typography>
            <Typography
              typography={"s3"}
              fontWeight={"fontWeightRegular"}
              color="status.info.fg"
            >
              {"We've"} summarized each dataset. Please select one to view and
              compare individually.
            </Typography>
          </Box>
        </Box>
      </ShowComponent>
      <ShowComponent condition={Boolean(isCommonColumn) || datasetId}>
        <CompareDatasetEvalsCards
          isCompare={isCompare}
          datasetId={datasetId}
          selectedDatasets={selectedDatasets}
          selectedIndex={selectedIndex}
          baseColumn={baseColumn}
          commonColumn={commonColumn}
          selectedDatasetData={selectedDatasetData}
          isCommonColumn={isCommonColumn}
        />
      </ShowComponent>
    </Box>
  );
};

export default CompareSummaryTab;

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
