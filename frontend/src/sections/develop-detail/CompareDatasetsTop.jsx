import { Box, IconButton, Typography, Button } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef, useEffect } from "react";
import Iconify from "src/components/iconify";
import CompareDatasetDrawer from "./CompareDatasetDrawer";
import BaseColumnDrawer from "./BaseColumnsDrawer";
import { getUniqueColorPalette } from "src/utils/utils";
import Tooltip from "@mui/material/Tooltip";

const CompareDatasetsTop = ({
  setIsChooseWinnerSelected,
  setBaseColumn,
  datasetOptions,
  onSelectedDatasetsChange,
  setIsCompareDataset,
  setSelectedDatasetData,
  setSelectedDatasetsValuesInParent,
  selectedDatasetData,
  setCurrentTab,
  setCompareFromOutSide,
  setIsChooseWinnerButtonVisible,
  setIsCommonColumn,
}) => {
  const [compareDatasetDrawerVisible, setCompareDatasetDrawerVisible] =
    useState(false);
  const [baseColumnDrawerVisible, setBaseColumnDrawerVisible] = useState(false);
  const [localSelectedDatasets, setLocalSelectedDatasets] = useState(
    selectedDatasetData || [],
  );
  const [visibleDatasets, setVisibleDatasets] = useState([]);
  const [hiddenDatasets, setHiddenDatasets] = useState([]);
  const [truncatedLabels, setTruncatedLabels] = useState({});
  const [pendingSelectedDatasets, setPendingSelectedDatasets] = useState([]);
  const [pendingSelectedValues, setPendingSelectedValues] = useState([]);
  const [pendingBaseColumn, setPendingBaseColumn] = useState(null);
  const containerRef = useRef(null);

  // Maximum datasets to show before using "View All" button
  const MAX_VISIBLE_DATASETS = 6;

  // Handle the dataset selection change from the drawer
  const handleSelectedDatasetsChange = (
    selectedValues,
    selectedDatasetLabels,
  ) => {
    setPendingSelectedDatasets(selectedDatasetLabels);
    setPendingSelectedValues(selectedValues);
  };

  // Handle closing the base column drawer
  const onBaseColumnDrawerClose = () => {
    setBaseColumnDrawerVisible(false);
  };

  // Handle closing the compare dataset drawer
  const onCompareDatasetDrawerClose = () => {
    setCompareDatasetDrawerVisible(false);
  };

  // Handle removing a dataset
  const handleRemoveDataset = (datasetToRemove) => {
    const updatedDatasets = localSelectedDatasets.filter(
      (dataset) => dataset !== datasetToRemove,
    );
    setLocalSelectedDatasets(updatedDatasets);
    setSelectedDatasetData(updatedDatasets);

    // Get the corresponding values for the updated datasets
    const updatedValues = datasetOptions
      .filter((option) => updatedDatasets.includes(option.label))
      .map((option) => option.value);

    // Update parent's selected dataset values
    setSelectedDatasetsValuesInParent(updatedValues);

    // Call the parent component's handler if provided
    if (onSelectedDatasetsChange) {
      onSelectedDatasetsChange(updatedValues, updatedDatasets);
    }
  };

  useEffect(() => {
    if (localSelectedDatasets.length <= 1) {
      setIsChooseWinnerButtonVisible(false);
      setIsCompareDataset(false);
      setLocalSelectedDatasets([]);
      setBaseColumn(null);
      setIsChooseWinnerSelected(false);
      setCompareFromOutSide(false);
      setCurrentTab("data");
      setPendingSelectedDatasets([]);
      setPendingSelectedValues([]);
      setPendingBaseColumn(null);
    }
  }, [localSelectedDatasets]);

  // Calculate text truncation and dataset visibility
  useEffect(() => {
    const calculateVisibilityAndTruncation = () => {
      if (!containerRef.current) return;

      const container = containerRef.current;
      const containerWidth = container.offsetWidth;

      // Step 1: Determine visible and hidden datasets based on MAX_VISIBLE_DATASETS
      let newVisibleDatasets = [];
      let newHiddenDatasets = [];

      if (localSelectedDatasets.length <= MAX_VISIBLE_DATASETS) {
        // Show all datasets if total count is less than or equal to MAX_VISIBLE_DATASETS
        newVisibleDatasets = [...localSelectedDatasets];
        newHiddenDatasets = [];
      } else {
        // Show only MAX_VISIBLE_DATASETS and hide the rest
        newVisibleDatasets = localSelectedDatasets.slice(
          0,
          MAX_VISIBLE_DATASETS,
        );
        newHiddenDatasets = localSelectedDatasets.slice(MAX_VISIBLE_DATASETS);
      }

      // Step 2: Calculate available space for text truncation
      const rightControlsWidth = 260; // Space for Add + Exit buttons
      const viewAllButtonWidth = newHiddenDatasets.length > 0 ? 100 : 0; // Space for View All button if needed
      const baseBoxWidth = 70; // Width for badge, padding
      const closeButtonWidth = 20; // Width for close button
      const compareArrowWidth = 40; // Width for compare arrow
      const approxCharWidth = 7; // Approximate width of one character

      // Calculate fixed elements width
      let fixedElementsWidth = 0;

      // Add width for each dataset box (excluding text)
      for (let i = 0; i < newVisibleDatasets.length; i++) {
        // Add width for compare arrows between datasets
        if (i > 0) {
          fixedElementsWidth += compareArrowWidth;
        }

        // Add box width + close button (if not first dataset)
        fixedElementsWidth += baseBoxWidth + (i > 0 ? closeButtonWidth : 0);
      }

      // Add View All button width and arrow if hidden datasets exist
      if (newHiddenDatasets.length > 0) {
        fixedElementsWidth += viewAllButtonWidth + compareArrowWidth;
      }

      // Total available width for the container
      const availableWidth = containerWidth - rightControlsWidth;

      // Available width for all text combined
      const availableTextWidth = availableWidth - fixedElementsWidth;

      // Step 3: Apply text truncation based on available space
      const newTruncatedLabels = {};

      if (newVisibleDatasets.length > 0) {
        // If very limited space, show minimal text
        if (availableTextWidth <= 0) {
          newVisibleDatasets.forEach((dataset) => {
            newTruncatedLabels[dataset] = dataset.substring(0, 3) + "...";
          });
        } else {
          // Calculate characters per dataset with equal distribution
          const charsPerDataset = Math.max(
            5,
            Math.floor(
              (availableTextWidth + 350) /
                (newVisibleDatasets.length * approxCharWidth),
            ),
          );

          // Apply truncation
          newVisibleDatasets.forEach((dataset) => {
            if (dataset.length > charsPerDataset) {
              newTruncatedLabels[dataset] =
                dataset.substring(0, charsPerDataset - 3) + "...";
            } else {
              newTruncatedLabels[dataset] = dataset;
            }
          });
        }
      }

      setVisibleDatasets(newVisibleDatasets);
      setHiddenDatasets(newHiddenDatasets);
      setTruncatedLabels(newTruncatedLabels);
    };

    calculateVisibilityAndTruncation();
    window.addEventListener("resize", calculateVisibilityAndTruncation);
    return () =>
      window.removeEventListener("resize", calculateVisibilityAndTruncation);
  }, [localSelectedDatasets]);

  // Get selected dataset IDs from the datasetOptions
  const selectedDatasetsValues = datasetOptions
    ?.filter((option) => localSelectedDatasets.includes(option.label))
    ?.map((option) => option.value);

  return (
    <>
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          marginLeft: 1.5,
          width: "100%",
          overflow: "hidden",
        }}
      >
        {/* Visible Selected datasets list with Compare Icon in Between */}
        {visibleDatasets.map((item, index) => {
          const letter = String.fromCharCode(65 + index);
          const versionLabel = `${letter}`; // Generates A, B, C, etc.
          const versionColors = getUniqueColorPalette(index);
          const displayText = truncatedLabels[item] || item;

          return (
            <React.Fragment key={index}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  border: "1px solid",
                  borderColor: "action.hover",
                  paddingY: "4px",
                  paddingX: "12px",
                  borderRadius: "4px",
                }}
              >
                <Box
                  sx={{
                    borderRadius: "4px",
                    backgroundColor: versionColors.tagBackground,
                    paddingX: "8px",
                    height: "18px",
                  }}
                >
                  <Typography
                    sx={{
                      color: versionColors.tagForeground,
                      fontSize: "12px",
                      fontWeight: 500,
                    }}
                  >
                    {versionLabel}
                  </Typography>
                </Box>
                <Tooltip title={item}>
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightMedium"}
                    color={"text.primary"}
                    sx={{
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      maxWidth: "100%",
                    }}
                    title={item} // Show full text on hover
                  >
                    {displayText}
                  </Typography>
                </Tooltip>

                {/* Only show close button for datasets after the first one */}
                {index > 0 && (
                  <Box
                    sx={{
                      width: "16px",
                      height: "16px",
                      display: "flex",
                      justifyContent: "center",
                      alignItems: "center",
                      marginLeft: 0.1,
                    }}
                  >
                    <IconButton
                      size="small"
                      sx={{ color: "text.secondary", padding: 0, margin: 0 }}
                      onClick={() => {
                        handleRemoveDataset(item);
                        setIsChooseWinnerSelected(false);
                      }}
                    >
                      <Iconify
                        icon="solar:close-linear"
                        width={14}
                        height={14}
                      />
                    </IconButton>
                  </Box>
                )}
              </Box>
              {index === 0 && (
                <IconButton
                  size="small"
                  sx={{
                    color: "text.secondary",
                    opacity: 1,
                    pointerEvents: "none",
                  }}
                >
                  <Iconify icon="solar:transfer-horizontal-linear" />
                </IconButton>
              )}
            </React.Fragment>
          );
        })}

        {/* View All Button when there are hidden datasets */}
        {hiddenDatasets.length > 0 && (
          <>
            {/* <IconButton
              size="small"
              sx={{
                color: "text.secondary",
                opacity: 1,
                pointerEvents: "none",
              }}
            >
              <Iconify icon="solar:transfer-horizontal-linear" />
            </IconButton> */}
            <Button
              size="small"
              sx={{
                color: "text.primary",
                display: "flex",
                flexDirection: "row",
                gap: "8px",
                border: "1px solid",
                borderColor: "action.hover",
              }}
              onClick={() => {
                setCompareDatasetDrawerVisible(true);
                setBaseColumn(null);
              }}
            >
              <Typography
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightMedium"}
              >
                +{hiddenDatasets.length} more
              </Typography>
              <Iconify
                sx={{ color: "text.secondary" }}
                icon="solar:alt-arrow-down-linear"
                width={14}
                height={14}
              />
            </Button>
          </>
        )}

        <IconButton
          sx={{ color: "text.secondary" }}
          onClick={() => setCompareDatasetDrawerVisible(true)}
        >
          <Iconify icon="solar:add-linear" height={"14px"} width={"14px"} />
        </IconButton>

        {/* Buttons aligned to the right */}
        <Box sx={{ display: "flex", alignItems: "center", marginLeft: "auto" }}>
          <Button
            size="small"
            sx={{
              border: "1px solid",
              borderColor: "action.hover",
              minWidth: "38px",
            }}
            onClick={() => {
              setIsCompareDataset(false);
              setIsChooseWinnerButtonVisible(false);
              setLocalSelectedDatasets([]);
              setBaseColumn(null);
              setIsChooseWinnerSelected(false);
              setCompareFromOutSide(false);
              setCurrentTab("data");
              setPendingSelectedDatasets([]);
              setPendingSelectedValues([]);
              setPendingBaseColumn(null);
            }}
          >
            <Typography
              variant="s2"
              color={"text.secondary"}
              fontWeight={"fontWeightMedium"}
            >
              Exit
            </Typography>
          </Button>
        </Box>
      </Box>

      {/* Compare Dataset Drawer */}
      <CompareDatasetDrawer
        setIsChooseWinnerSelected={setIsChooseWinnerSelected}
        onCompareDatasetDrawerClose={() =>
          setCompareDatasetDrawerVisible(false)
        }
        compareDatasetDrawerVisible={compareDatasetDrawerVisible}
        alreadySelectedDatasets={localSelectedDatasets}
        datasetOptions={datasetOptions}
        setCompareDatasetDrawerVisible={setCompareDatasetDrawerVisible}
        setBaseColumnDrawerVisible={setBaseColumnDrawerVisible}
        onSelectedDatasetsChange={handleSelectedDatasetsChange}
        isPlusButtonClick={true}
      />

      {/* Base Column Drawer */}
      {selectedDatasetsValues?.length > 0 && (
        <BaseColumnDrawer
          setIsCompareDataset={setIsCompareDataset}
          baseColumnDrawerVisible={baseColumnDrawerVisible}
          selectedDatasets={pendingSelectedValues}
          onBaseColumnDrawerClose={onBaseColumnDrawerClose}
          onCompareDatasetDrawerClose={onCompareDatasetDrawerClose}
          selectedValue={pendingBaseColumn}
          setSelectedValue={setPendingBaseColumn}
          setCurrentTab={setCurrentTab}
          commitSelections={() => {
            setLocalSelectedDatasets(pendingSelectedDatasets);
            setSelectedDatasetData(pendingSelectedDatasets);
            setSelectedDatasetsValuesInParent(pendingSelectedValues);
            if (pendingBaseColumn) setBaseColumn(pendingBaseColumn);
          }}
          setIsCommonColumn={setIsCommonColumn}
          setIsChooseWinnerButtonVisible={setIsChooseWinnerButtonVisible}
        />
      )}
    </>
  );
};

CompareDatasetsTop.propTypes = {
  setCurrentTab: PropTypes.func,
  setIsChooseWinnerSelected: PropTypes.func,
  setBaseColumn: PropTypes.func,
  datasetOptions: PropTypes.array,
  onSelectedDatasetsChange: PropTypes.func,
  setIsCompareDataset: PropTypes.func,
  setSelectedDatasetData: PropTypes.func,
  setSelectedDatasetsValuesInParent: PropTypes.func,
  selectedDatasetData: PropTypes.array,
  setCompareFromOutSide: PropTypes.func,
  setIsChooseWinnerButtonVisible: PropTypes.func,
  setIsCommonColumn: PropTypes.func,
};

export default CompareDatasetsTop;
