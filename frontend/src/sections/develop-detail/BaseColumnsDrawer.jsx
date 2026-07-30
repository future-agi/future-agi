import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import CompareDrawerSkeleton from "./Common/CompareDrawerSkeleton";
import { useNavigate } from "react-router";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import CompareDatasetSummaryIcon from "./DatasetSummaryTab/CompareDatasetSummaryIcon";

const BaseColumnDrawer = ({
  baseColumnDrawerVisible,
  selectedDatasets,
  onBaseColumnDrawerClose,
  onCompareDatasetDrawerClose,
  setIsCompareDataset,
  setCurrentTab,
  selectedValue,
  setSelectedValue,
  selectedDatasetsValues,
  compareFromOutside = false,
  setIsCommonColumn,
  commitSelections,
  setIsChooseWinnerButtonVisible,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [baseColumnsData, setBaseColumnsData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isDataLoaded, setIsDataLoaded] = useState(false);
  const [selectedValueBase, setSelectedValueBase] = useState("");

  const navigate = useNavigate();

  const { mutate: getBaseColumn } = useMutation({
    mutationFn: () => {
      setLoading(true);
      setIsDataLoaded(false);
      return axios.get(endpoints.dataset.baseColumndata, {
        params: { dataset_ids: selectedDatasets },
        paramsSerializer: (params) => {
          const searchParams = new URLSearchParams();
          if (params.dataset_ids && Array.isArray(params.dataset_ids)) {
            params.dataset_ids.forEach((id) => {
              searchParams.append("dataset_ids", id);
            });
          }
          return searchParams.toString();
        },
      });
    },
    onSuccess: (data) => {
      const baseColumns = data?.data?.result?.base_columns || [];
      setBaseColumnsData(baseColumns);
      setIsDataLoaded(true);
      setLoading(false);
    },
    onError: () => {
      setBaseColumnsData([]);
      setIsDataLoaded(true);
      setLoading(false);
    },
  });

  const selectedDatasetIdsKey = selectedDatasets?.join(",") || "";

  useEffect(() => {
    if (!baseColumnDrawerVisible || !selectedDatasetIdsKey) return;
    getBaseColumn();
  }, [baseColumnDrawerVisible, selectedDatasetIdsKey, getBaseColumn]);

  // Filter base columns based on search query
  const filteredBaseColumns = baseColumnsData.filter((column) =>
    column.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <Drawer
      anchor="right"
      open={baseColumnDrawerVisible}
      onClose={onBaseColumnDrawerClose}
      variant="persistent"
      PaperProps={{
        sx: {
          width: "35%",
          height: "100vh",
          position: "fixed",
          zIndex: 20,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
          overflow: "hidden",
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
      {/* Header Section */}
      <Box sx={{ paddingX: 1.5, paddingTop: 1.5 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography
            sx={{ fontWeight: 500, fontSize: "16px", color: "text.primary" }}
          >
            Select Base Columns
          </Typography>
          <IconButton
            onClick={() => {
              onBaseColumnDrawerClose();
              onCompareDatasetDrawerClose?.();
              compareFromOutside
                ? setSelectedValueBase(null)
                : setSelectedValue?.(null);
            }}
          >
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        {selectedDatasetsValues?.length > 0 && (
          <Box
            sx={{
              backgroundColor: "background.neutral",
              padding: "12px",
              marginTop: 2,
            }}
          >
            <Box
              sx={{
                backgroundColor: "background.paper",
                borderRadius: "8px",
                border: "1px solid",
                borderColor: "divider",
                padding: "12px",
                display: "flex",
                flexDirection: "column",
                gap: 1,
              }}
            >
              <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
                Selected datasets:
              </Typography>
              {selectedDatasetsValues?.map((item, index) => (
                <Box key={index} display="flex" gap={0.5}>
                  <CompareDatasetSummaryIcon index={index} />
                  <Typography
                    typography={"s2"}
                    fontWeight={"fontWeightRegular"}
                    display="flex"
                    gap={1}
                  >
                    {item}
                  </Typography>
                </Box>
              ))}
            </Box>
          </Box>
        )}
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightRegular"}
          marginTop={2}
        >
          Base column will be common across all datasets
        </Typography>

        {/* Search Input */}
        {filteredBaseColumns?.length > 0 && (
          <FormSearchField
            fullWidth
            size="small"
            placeholder="Search"
            sx={{
              marginTop: 3,
              marginBottom: 2,
              "& .MuiOutlinedInput-root": {
                borderRadius: "5px",
              },
            }}
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        )}
      </Box>

      {/* Scrollable List */}
      {loading ? (
        <CompareDrawerSkeleton />
      ) : (
        <>
          <Box sx={{ flexGrow: 1, overflowY: "auto", paddingX: 1.5 }}>
            <RadioGroup
              value={compareFromOutside ? selectedValueBase : selectedValue}
              onChange={(e) =>
                compareFromOutside
                  ? setSelectedValueBase(e.target.value)
                  : setSelectedValue(e.target.value)
              }
            >
              {filteredBaseColumns?.length > 0 ? (
                filteredBaseColumns?.map((val, index) => (
                  <Box
                    key={index}
                    sx={{
                      border: "1px solid var(--border-light)",
                      marginBottom: 2.2,
                      borderRadius: "8px",
                      display: "flex",
                      alignItems: "center",
                      cursor: "pointer",
                    }}
                    onClick={() =>
                      compareFromOutside
                        ? setSelectedValueBase(val)
                        : setSelectedValue(val)
                    }
                  >
                    <Radio
                      value={val}
                      sx={{
                        "&:hover": { backgroundColor: "transparent" },
                      }}
                    />
                    <Typography
                      sx={{
                        color: "text.primary",
                        fontWeight: 400,
                        fontSize: "14px",
                      }}
                    >
                      {val}
                    </Typography>
                  </Box>
                ))
              ) : selectedDatasetsValues?.length > 0 ? (
                <Box
                  sx={{
                    marginTop: "16px",
                    borderRadius: "4px",
                    backgroundColor: "blue.o5",
                    border: "1px solid",
                    borderColor: "blue.200",
                    padding: "12px",
                  }}
                >
                  <Typography
                    typography={"s1"}
                    fontWeight={"fontWeightSemiBold"}
                    color="blue.500"
                  >
                    There are no common columns to compare
                  </Typography>
                </Box>
              ) : (
                <Box
                  sx={{
                    height: "70vh",
                    marginTop: 2,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                  }}
                >
                  <Typography
                    sx={{
                      color: "text.primary",
                      fontWeight: 500,
                      fontSize: "16px",
                    }}
                  >
                    There are no common columns to compare
                  </Typography>
                </Box>
              )}
            </RadioGroup>
          </Box>

          {/* Fixed Button Section */}
          <Box
            sx={{
              position: "sticky",
              bottom: 0,
              backgroundColor: "background.paper",
              paddingX: 1.5,
              paddingY: 2,
            }}
          >
            <Button
              sx={{
                width: "49%",
                height: "36px",
                fontSize: "12px",
                color: "text.secondary",
                borderRadius: "8px",
              }}
              variant="outlined"
              onClick={() => {
                onBaseColumnDrawerClose();
                compareFromOutside
                  ? setSelectedValueBase(null)
                  : setSelectedValue?.(null);
              }}
            >
              Back
            </Button>
            <Button
              sx={{
                width: "49%",
                height: "36px",
                fontSize: "12px",
                borderRadius: "8px",
                marginLeft: "2%",
              }}
              variant="contained"
              color="primary"
              disabled={
                compareFromOutside
                  ? isDataLoaded &&
                    filteredBaseColumns.length > 0 &&
                    !selectedValueBase
                  : isDataLoaded &&
                    filteredBaseColumns.length > 0 &&
                    !selectedValue
              }
              onClick={() => {
                // Commit all pending selections when Compare is clicked
                if (commitSelections) {
                  commitSelections();
                }
                if (compareFromOutside) {
                  navigate(`/dashboard/develop/${selectedDatasets[0]}/`, {
                    state: {
                      isCompare: true,
                      selectedDatasets: selectedDatasets,
                      baseColumn: selectedValueBase,
                      selectedDatasetsValues: selectedDatasetsValues,
                      compareFromOutside: compareFromOutside,
                      isCommonColumn:
                        filteredBaseColumns.length > 0 ? true : false,
                    },
                  });
                } else {
                  setIsCompareDataset(true);
                  onBaseColumnDrawerClose();
                  onCompareDatasetDrawerClose();
                  setCurrentTab("summary");
                  filteredBaseColumns.length > 0
                    ? setIsCommonColumn(true)
                    : setIsCommonColumn(false);
                  filteredBaseColumns.length == 0 &&
                    setIsChooseWinnerButtonVisible(false);
                }
              }}
            >
              Compare
            </Button>
          </Box>
        </>
      )}
    </Drawer>
  );
};

BaseColumnDrawer.propTypes = {
  selectedDatasetsValues: PropTypes.array,
  compareFromOutside: PropTypes.bool,
  setCurrentTab: PropTypes.func,
  setIsCompareDataset: PropTypes.func,
  onCompareDatasetDrawerClose: PropTypes.func,
  baseColumnDrawerVisible: PropTypes.bool,
  selectedDatasets: PropTypes.array,
  onBaseColumnDrawerClose: PropTypes.func,
  selectedValue: PropTypes.string,
  setSelectedValue: PropTypes.func,
  compareOuter: PropTypes.bool,
  setIsCommonColumn: PropTypes.func,
  commitSelections: PropTypes.func,
  setIsChooseWinnerButtonVisible: PropTypes.func,
};

export default BaseColumnDrawer;
