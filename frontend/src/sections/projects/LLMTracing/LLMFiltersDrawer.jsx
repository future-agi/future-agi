import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  useTheme,
  Button,
} from "@mui/material";
import LLMFilterBox from "./LLMFilterBox";
import Iconify from "src/components/iconify";
import { getRandomId, getUniqueColorPalette } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";

const LLMFiltersDrawer = ({
  open,
  onClose,
  showCompare,
  primaryFilters,
  setPrimaryFilters,
  primaryFilterDefinition,
  setPrimaryFilterDefinition,
  defaultFilter,
  compareFilters,
  setCompareFilters,
  compareFilterDefinition,
  setCompareFilterDefinition,
  hideLabel,
}) => {
  const theme = useTheme();
  // Local state to hold temporary filter changes
  const [tempPrimaryFilters, setTempPrimaryFilters] = useState(primaryFilters);
  const [tempPrimaryFilterDefinition, setTempPrimaryFilterDefinition] =
    useState(primaryFilterDefinition);
  const [tempCompareFilters, setTempCompareFilters] = useState(compareFilters);
  const [tempCompareFilterDefinition, setTempCompareFilterDefinition] =
    useState(compareFilterDefinition);

  // Update temp state when drawer opens or props change
  useEffect(() => {
    if (open) {
      setTempPrimaryFilters(primaryFilters);
      setTempPrimaryFilterDefinition(primaryFilterDefinition);
      setTempCompareFilters(compareFilters);
      setTempCompareFilterDefinition(compareFilterDefinition);
    }
  }, [
    open,
    primaryFilters,
    primaryFilterDefinition,
    compareFilters,
    compareFilterDefinition,
  ]);

  const handleApplyFilters = () => {
    // Apply all the temporary changes to the actual state
    setPrimaryFilters(tempPrimaryFilters);
    setPrimaryFilterDefinition(tempPrimaryFilterDefinition);

    if (showCompare) {
      setCompareFilters(tempCompareFilters);
      setCompareFilterDefinition(tempCompareFilterDefinition);
    }

    onClose();
  };

  const resetFiltersAndClose = (type) => () => {
    // If primary filter is empty and compare filter is also empty, then
    // we reset the filters and close the drawer
    if (
      type === "primary" &&
      tempCompareFilters?.length === 1 &&
      !tempCompareFilters[0]?.column_id
    ) {
      setPrimaryFilters([{ ...defaultFilter, id: getRandomId() }]);
      setPrimaryFilterDefinition(tempPrimaryFilterDefinition);
      onClose();
    }
    // If compare filter is empty and primary filter is also empty, then
    // we reset the filters and close the drawer
    else if (
      type === "compare" &&
      tempPrimaryFilters?.length === 1 &&
      !tempPrimaryFilters[0]?.column_id
    ) {
      setCompareFilters([{ ...defaultFilter, id: getRandomId() }]);
      setCompareFilterDefinition(tempCompareFilterDefinition);
      onClose();
    }
  };

  const handleCancel = () => {
    // Reset temp state to original values
    setTempPrimaryFilters(primaryFilters);
    setTempPrimaryFilterDefinition(primaryFilterDefinition);
    setTempCompareFilters(compareFilters);
    setTempCompareFilterDefinition(compareFilterDefinition);
    onClose();
  };

  const arePrimaryFiltersValidForCompare = () => {
    const validPropertyIds = new Set();

    const collectIds = (defs) => {
      if (!Array.isArray(defs)) return;
      defs?.forEach((def) => {
        if (def?.propertyId) {
          validPropertyIds.add(def?.propertyId);
        }
        if (Array.isArray(def?.dependents)) {
          collectIds(def?.dependents);
        }
      });
    };

    collectIds(tempCompareFilterDefinition);

    return tempPrimaryFilters.every((filter) => {
      if (!filter?.column_id) return true;
      return validPropertyIds.has(filter.column_id);
    });
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleCancel}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
          width: "40vw",
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
      <IconButton
        onClick={handleCancel}
        sx={{
          position: "absolute",
          top: "10px",
          right: "10px",
          color: "text.primary",
          zIndex: 10,
        }}
      >
        <Iconify icon="akar-icons:cross" />
      </IconButton>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          px: 2,
          pt: 2,
          pb: 0,
        }}
      >
        {/* Header */}
        <Typography variant="s1" fontWeight={"fontWeightMedium"} sx={{ mb: 2 }}>
          Filter
        </Typography>

        {/* Scrollable filters content */}
        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            pr: 1,
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          {/* Primary Filter Section */}
          <Box
            sx={{
              gap: 2,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1,
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                pb: 0.5,
              }}
            >
              {!hideLabel && (
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Box
                    sx={() => {
                      const { tagBackground: bg, tagForeground: text } =
                        getUniqueColorPalette(0);
                      return {
                        width: theme.spacing(3),
                        height: theme.spacing(3.125),
                        borderRadius: theme.spacing(0.5),
                        backgroundColor: bg,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 600,
                        color: text,
                      };
                    }}
                  >
                    A
                  </Box>
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    Primary Graph
                  </Typography>
                </Box>
              )}

              {showCompare && (
                <Button
                  onClick={() => {
                    if (!arePrimaryFiltersValidForCompare()) {
                      enqueueSnackbar(
                        "Some filters used in Primary Graph are not available for Comparison Graph.",
                        { variant: "warning" },
                      );
                      return;
                    }
                    setTempCompareFilters([...tempPrimaryFilters]);
                    // Do not copy definitions
                  }}
                  variant="outlined"
                  color="secondary"
                  size="small"
                  sx={{
                    "& .MuiButton-startIcon": {
                      margin: 0,
                      paddingRight: 0.5,
                    },
                    whiteSpace: "nowrap",
                    fontSize: "0.75rem",
                    px: 1.5,
                    py: 0.5,
                    borderColor: "primary.main",
                    borderRadius: 0.625,
                  }}
                >
                  Apply Same for Comparison Graph
                </Button>
              )}
            </Box>

            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
              <LLMFilterBox
                filters={tempPrimaryFilters}
                setFilters={setTempPrimaryFilters}
                filterDefinition={tempPrimaryFilterDefinition}
                setFilterDefinition={setTempPrimaryFilterDefinition}
                defaultFilter={defaultFilter}
                resetFiltersAndClose={resetFiltersAndClose("primary")}
              />
            </Box>
          </Box>

          {/* Compare Filter Section */}
          {showCompare && (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                p: 1,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  pb: 0.5,
                  gap: 1,
                }}
              >
                <Box
                  sx={() => {
                    const { tagBackground: bg, tagForeground: text } =
                      getUniqueColorPalette(3);
                    return {
                      width: theme.spacing(3),
                      height: theme.spacing(3.125),
                      borderRadius: theme.spacing(0.5),
                      backgroundColor: bg,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 600,
                      color: text,
                    };
                  }}
                >
                  B
                </Box>
                <Typography typography="s1" fontWeight="fontWeightMedium">
                  Compare Graph
                </Typography>
              </Box>

              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                <LLMFilterBox
                  filters={tempCompareFilters}
                  setFilters={setTempCompareFilters}
                  filterDefinition={tempCompareFilterDefinition}
                  setFilterDefinition={setTempCompareFilterDefinition}
                  defaultFilter={defaultFilter}
                  resetFiltersAndClose={resetFiltersAndClose("compare")}
                />
              </Box>
            </Box>
          )}
        </Box>

        {/* Footer buttons */}
        <Box
          sx={{
            pt: 2,
            display: "flex",
            gap: 2,
            justifyContent: "flex-end",
            borderTop: "1px solid",
            borderColor: "background.neutral",
            pb: 3,
          }}
        >
          <Button
            variant="outlined"
            size="small"
            onClick={handleCancel}
            sx={{ width: 140, px: 1, borderColor: "text.disabled" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={handleApplyFilters}
            sx={{ width: 140, px: 1 }}
          >
            Apply Filters
          </Button>
        </Box>
      </Box>
    </Drawer>
  );
};

LLMFiltersDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  showCompare: PropTypes.bool.isRequired,
  primaryFilters: PropTypes.array.isRequired,
  setPrimaryFilters: PropTypes.func.isRequired,
  primaryFilterDefinition: PropTypes.array.isRequired,
  setPrimaryFilterDefinition: PropTypes.func.isRequired,
  defaultFilter: PropTypes.object.isRequired,
  compareFilters: PropTypes.array,
  setCompareFilters: PropTypes.func,
  compareFilterDefinition: PropTypes.array,
  setCompareFilterDefinition: PropTypes.func,
  hideLabel: PropTypes.bool,
};

export default LLMFiltersDrawer;
