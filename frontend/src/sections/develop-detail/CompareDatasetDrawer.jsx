import React, { useState, useEffect } from "react";
import {
  Box,
  Button,
  Checkbox,
  Drawer,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useParams } from "react-router";
import CompareDrawerSkeleton from "./Common/CompareDrawerSkeleton";
import Tooltip from "@mui/material/Tooltip";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const CompareDatasetDrawer = ({
  onCompareDatasetDrawerClose,
  compareDatasetDrawerVisible,
  datasetOptions,
  setBaseColumnDrawerVisible,
  onSelectedDatasetsChange,
  alreadySelectedDatasets,
  isPlusButtonClick = false,
  setIsChooseWinnerSelected,
}) => {
  const [selectedValues, setSelectedValues] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedDatasets, setSelectedDatasets] = useState([]);
  const { dataset: datasetId } = useParams();

  // Find the current dataset from URL params
  const currentDataset = datasetOptions?.find(
    (option) => option.value === datasetId,
  );

  useEffect(() => {
    // Initialize with the current dataset always selected
    if (compareDatasetDrawerVisible) {
      // If no datasets were previously selected, start with just the current dataset
      if (!alreadySelectedDatasets?.length) {
        if (currentDataset) {
          setSelectedValues([datasetId]);
          setSelectedDatasets([currentDataset.label]);
        } else {
          setSelectedValues([]);
          setSelectedDatasets([]);
        }
      } else {
        // If there are already selected datasets, ensure current dataset is included
        const values = datasetOptions
          .filter((option) => alreadySelectedDatasets.includes(option.label))
          .map((option) => option.value);
        // Add current dataset if not already included
        if (currentDataset && !values.includes(datasetId)) {
          setSelectedValues([datasetId, ...values]);
          setSelectedDatasets([
            currentDataset.label,
            ...alreadySelectedDatasets,
          ]);
        } else {
          setSelectedValues(values);
          setSelectedDatasets([...alreadySelectedDatasets]);
        }
      }
    }
  }, [
    compareDatasetDrawerVisible,
    alreadySelectedDatasets,
    datasetOptions,
    datasetId,
    currentDataset,
  ]);

  const handleOpen = () => {
    // Ensure the current dataset is always at index 0
    if (currentDataset) {
      const valuesWithoutCurrent = selectedValues.filter(
        (val) => val !== datasetId,
      );
      const datasetsWithoutCurrent = selectedDatasets.filter(
        (ds) => ds !== currentDataset.label,
      );
      onSelectedDatasetsChange(
        [datasetId, ...valuesWithoutCurrent],
        [currentDataset.label, ...datasetsWithoutCurrent],
      );
    } else {
      onSelectedDatasetsChange(selectedValues, selectedDatasets);
    }
    if (isPlusButtonClick) {
      // setCompareDatasetDrawerVisible(false);
      setIsChooseWinnerSelected(false);
    }
    setBaseColumnDrawerVisible(true);
  };

  const handleCheckboxChange = (id, val) => {
    // Check if the item is already selected
    const isSelected = selectedValues.includes(id);

    if (isSelected) {
      // Remove the item
      setSelectedValues((prev) => prev.filter((item) => item !== id));
      setSelectedDatasets((prev) => prev.filter((item) => item !== val));
    } else {
      // Add the item
      setSelectedValues((prev) => [...prev, id]);
      setSelectedDatasets((prev) => [...prev, val]);
    }
  };

  const filteredDatasetOptions = datasetOptions?.filter(
    (option) =>
      option.label.toLowerCase().includes(searchQuery.trim().toLowerCase()) &&
      option.value !== datasetId, // Exclude current dataset from filtered list
  );

  const covered = useDrawerCovered(compareDatasetDrawerVisible);
  return (
    <Drawer
      anchor="right"
      open={compareDatasetDrawerVisible}
      sx={coveredDrawerSx(covered)}
      onClose={onCompareDatasetDrawerClose}
      variant="persistent"
      PaperProps={{
        sx: {
          width: "35%",
          height: "100vh",
          position: "fixed",
          zIndex: 20,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          display: "flex",
          flexDirection: "column",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ paddingX: 1.5, mt: 2 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box sx={{ display: "flex" }}>
            <Typography
              sx={{ fontWeight: 500, fontSize: "16px", color: "text.primary" }}
            >
              Current dataset selected:
            </Typography>
            <Tooltip title={currentDataset?.label}>
              <Typography
                sx={{
                  fontWeight: 500,
                  fontSize: "16px",
                  color: "text.primary",
                  ml: 0.6,
                }}
              >
                {currentDataset?.label?.length > 35
                  ? currentDataset?.label?.substring(0, 35) + "..."
                  : currentDataset?.label}
              </Typography>
            </Tooltip>
          </Box>
          <IconButton
            onClick={() => {
              onCompareDatasetDrawerClose();
              // Reset selection but keep current dataset
              if (currentDataset) {
                setSelectedValues([datasetId]);
                setSelectedDatasets([currentDataset.label]);
              } else {
                setSelectedValues([]);
                setSelectedDatasets([]);
              }
            }}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
        <FormSearchField
          fullWidth
          size="small"
          placeholder="Search"
          sx={{
            mt: 1.5,
            mb: 2,
            "& .MuiOutlinedInput-root": {
              borderRadius: "6px",
              minHeight: "34px",
            },
          }}
          searchQuery={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <Typography
          sx={{ fontWeight: 400, fontSize: "13px", color: "text.primary" }}
        >
          Select datasets to compare with:
        </Typography>
      </Box>

      {selectedValues.length ? (
        <>
          <Box sx={{ flexGrow: 1, overflowY: "auto", paddingX: 1.5 }}>
            {filteredDatasetOptions?.length > 0 ? (
              filteredDatasetOptions.map((val, index) => (
                <Box
                  key={val.id || index}
                  sx={{
                    border: "1px solid var(--border-light)",
                    marginTop: 2.2,
                    borderRadius: "8px",
                    display: "flex",
                    alignItems: "center",
                    backgroundColor: "transparent",
                    cursor: "pointer",
                  }}
                  onClick={() => handleCheckboxChange(val?.value, val?.label)}
                >
                  <Checkbox
                    sx={{
                      paddingX: 1.5,
                      "&:hover": { backgroundColor: "transparent" },
                    }}
                    checked={selectedValues.includes(val.value)}
                  />
                  <Tooltip title={val?.label}>
                    <Typography
                      sx={{
                        color: "text.primary",
                        fontWeight: 400,
                        fontSize: "13px",
                      }}
                    >
                      {val?.label?.length > 68
                        ? val?.label?.substring(0, 68) + "..."
                        : val?.label}
                    </Typography>
                  </Tooltip>
                </Box>
              ))
            ) : (
              <Typography
                sx={{ marginTop: 2, textAlign: "center", color: "grey" }}
              >
                No Dataset found
              </Typography>
            )}
          </Box>

          <Box
            sx={{
              paddingX: 1.5,
              paddingY: 3,
              display: "flex",
              justifyContent: "space-between",
              backgroundColor: "background.paper",
              position: "sticky",
              bottom: 0,
            }}
          >
            <Button
              size="small"
              sx={{
                width: "49%",
                color: "text.secondary",
              }}
              variant="outlined"
              onClick={() => {
                onCompareDatasetDrawerClose();
                // Reset selection but keep current dataset
                if (currentDataset) {
                  setSelectedValues([datasetId]);
                  setSelectedDatasets([currentDataset.label]);
                } else {
                  setSelectedValues([]);
                  setSelectedDatasets([]);
                }
              }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              sx={{
                width: "49%",
              }}
              variant="contained"
              color="primary"
              disabled={selectedValues.length <= 1}
              onClick={handleOpen}
            >
              Open
            </Button>
          </Box>
        </>
      ) : (
        <CompareDrawerSkeleton />
      )}
    </Drawer>
  );
};

CompareDatasetDrawer.propTypes = {
  setIsChooseWinnerSelected: PropTypes.func,
  isPlusButtonClick: PropTypes.bool,
  alreadySelectedDatasets: PropTypes.array,
  onSelectedDatasetsChange: PropTypes.func,
  setCompareDatasetDrawerVisible: PropTypes.func,
  setBaseColumnDrawerVisible: PropTypes.func,
  datasetOptions: PropTypes.array,
  onCompareDatasetDrawerClose: PropTypes.func,
  compareDatasetDrawerVisible: PropTypes.bool,
};

export default CompareDatasetDrawer;
